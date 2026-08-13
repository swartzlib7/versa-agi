"""Unit tests for TD-UTIL-001 Utility Models (Phase F).

Covers the dependency-light pieces of the Utility Model stack — modality mime
maps (default derivation + input/output validation), the output-driver registry
dispatch (text writer + image/audio/video stub), and the ``utility_models`` store
CRUD. The chat/generation invocation path (``utility_runner._invoke_chat_model``)
needs a live provider + langchain and is exercised by the §13 manual matrix, not
here.

Run:  python -m unittest harness.tests.test_utility_models   (from core-infra)
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import base64 as _b64  # noqa: E402

import db_connect  # noqa: E402
import modality_maps  # noqa: E402
import utility_store  # noqa: E402
from harness import generation as gen  # noqa: E402
from harness.utility_runner import UtilityRunError  # noqa: E402
from model_drivers.errors import DriverError  # noqa: E402
from model_drivers.output import get_output_driver, has_real_driver  # noqa: E402
from provider_runtime import ProviderRuntimeError, resolve_provider_route  # noqa: E402

try:
    import openai  # noqa: F401

    _HAS_OPENAI = True
except Exception:  # noqa: BLE001
    _HAS_OPENAI = False

_AGENTS_SCHEMA = """
CREATE TABLE catalog_modality_maps (
  catalog_key TEXT PRIMARY KEY,
  map_json    TEXT NOT NULL,
  updated_at  DATETIME DEFAULT (datetime('now'))
);
CREATE TABLE utility_models (
  id              TEXT PRIMARY KEY,
  label           TEXT NOT NULL,
  catalog_model   TEXT NOT NULL,
  system_prompt   TEXT NOT NULL,
  output_modality TEXT NOT NULL DEFAULT 'text',
  output_path     TEXT,
  run_as_agent    TEXT NOT NULL DEFAULT 'coa',
  config_json     TEXT,
  enabled         INTEGER NOT NULL DEFAULT 1,
  created_at      DATETIME DEFAULT (datetime('now')),
  updated_at      DATETIME DEFAULT (datetime('now'))
);
"""


class _TempAgentsDB(unittest.TestCase):
    """Base: a throwaway agents.db wired via AGICTL_AGENTS_DB."""

    def setUp(self):
        self._dir = tempfile.mkdtemp()
        self.db = os.path.join(self._dir, "agents.db")
        conn = db_connect.connect(self.db, row_factory=False)
        conn.executescript(_AGENTS_SCHEMA)
        conn.commit()
        conn.close()
        self._prev = os.environ.get("AGICTL_AGENTS_DB")
        os.environ["AGICTL_AGENTS_DB"] = self.db

    def tearDown(self):
        if self._prev is None:
            os.environ.pop("AGICTL_AGENTS_DB", None)
        else:
            os.environ["AGICTL_AGENTS_DB"] = self._prev

    def _touch(self, name: str) -> str:
        p = os.path.join(self._dir, name)
        with open(p, "w", encoding="utf-8") as f:
            f.write("x")
        return p


class TestModalityMapDefaults(unittest.TestCase):
    def test_default_map_includes_declared_input_and_file_bucket(self):
        m = {"input_modalities": "text,image", "output_modalities": "text,image"}
        mp = modality_maps.default_map_for_catalog_entry(m)
        self.assertIn("text", mp["input"])
        self.assertIn("image", mp["input"])
        # The merged `file` bucket is always offered for --input-files.
        self.assertIn(modality_maps.FILE_MODALITY, mp["input"])
        # Output mirrors declared output modalities.
        self.assertIn("text", mp["output"])
        self.assertIn("image", mp["output"])
        self.assertNotIn("audio", mp["output"])

    def test_text_only_model_has_no_image_input(self):
        m = {"input_modalities": "text", "output_modalities": "text"}
        mp = modality_maps.default_map_for_catalog_entry(m)
        self.assertNotIn("image", mp["input"])
        self.assertEqual(mp["output"], {"text": ["*"]})

    def test_extension_allowed_wildcard_and_explicit(self):
        self.assertTrue(modality_maps.extension_allowed("text", "anything", ["*"]))
        self.assertTrue(modality_maps.extension_allowed("image", "PNG", ["png", "jpg"]))
        self.assertFalse(modality_maps.extension_allowed("image", "tiff", ["png", "jpg"]))

    def test_json_blob_round_trip(self):
        m = {"input": {"text": ["*"]}, "output": {"text": ["*"]}}
        blob = modality_maps.map_to_json_blob(m)
        self.assertEqual(modality_maps.parse_map_json(blob), m)
        self.assertIsNone(modality_maps.parse_map_json(""))
        self.assertIsNone(modality_maps.parse_map_json("not json"))


class TestModalityValidation(_TempAgentsDB):
    def setUp(self):
        super().setUp()
        modality_maps.save_modality_map(
            "test-model",
            {
                "input": {"image": ["jpg", "png"], "file": ["pdf", "txt"]},
                "output": {"text": ["*"], "image": ["png"]},
            },
            agents_db=self.db,
        )

    def test_input_accepts_listed_image_and_file(self):
        png = self._touch("ref.png")
        pdf = self._touch("doc.pdf")
        ok, err, checked = modality_maps.validate_input_files("test-model", [png, pdf], agents_db=self.db)
        self.assertTrue(ok, err)
        mods = {c["modality"] for c in checked}
        self.assertEqual(mods, {"image", "file"})

    def test_input_rejects_unlisted_extension(self):
        gif = self._touch("anim.gif")  # gif not in image allow-list
        ok, err, _ = modality_maps.validate_input_files("test-model", [gif], agents_db=self.db)
        self.assertFalse(ok)
        self.assertIn("not allowed", err)

    def test_input_rejects_missing_file(self):
        ok, err, _ = modality_maps.validate_input_files(
            "test-model", [os.path.join(self._dir, "nope.png")], agents_db=self.db
        )
        self.assertFalse(ok)
        self.assertIn("not found", err)

    def test_input_rejects_remote_url(self):
        ok, err, _ = modality_maps.validate_input_files(
            "test-model", ["https://example.com/x.png"], agents_db=self.db
        )
        self.assertFalse(ok)
        self.assertIn("Remote URLs", err)

    def test_output_artifact_validation(self):
        ok, _ = modality_maps.validate_output_artifact("test-model", "/tmp/a.png", "image", agents_db=self.db)
        self.assertTrue(ok)
        bad, err = modality_maps.validate_output_artifact("test-model", "/tmp/a.gif", "image", agents_db=self.db)
        self.assertFalse(bad)
        missing, err2 = modality_maps.validate_output_artifact("test-model", "/tmp/a.mp3", "audio", agents_db=self.db)
        self.assertFalse(missing)


class TestOutputDriverRegistry(unittest.TestCase):
    def test_text_driver_is_real_and_writes(self):
        self.assertTrue(has_real_driver("text"))
        d = tempfile.mkdtemp()
        path = os.path.join(d, "out.txt")
        get_output_driver("text")(path, "phase-f")
        with open(path, encoding="utf-8") as f:
            self.assertEqual(f.read(), "phase-f")

    def test_media_modalities_are_stubs(self):
        # image/audio are real drivers now; only video remains a stub.
        self.assertFalse(has_real_driver("video"))
        with self.assertRaises(UtilityRunError) as ctx:
            get_output_driver("video")()
        self.assertEqual(ctx.exception.code, "driver_pending")

    def test_unknown_modality_raises(self):
        with self.assertRaises(ValueError):
            get_output_driver("hologram")


class TestUtilityStore(_TempAgentsDB):
    def test_crud_round_trip(self):
        utility_store.add_utility_model(
            um_id="weekly-summary",
            label="Weekly Summary",
            catalog_model="gemini-2.5-flash",
            system_prompt="Summarize the week.",
            output_modality="text",
            output_path=".agent/attachments/utility",
        )
        row = utility_store.get_utility_model("weekly-summary")
        self.assertIsNotNone(row)
        self.assertEqual(row["label"], "Weekly Summary")
        self.assertTrue(row["enabled"])

        self.assertTrue(utility_store.update_utility_model("weekly-summary", {"label": "Wk", "enabled": False}))
        row = utility_store.get_utility_model("weekly-summary")
        self.assertEqual(row["label"], "Wk")
        self.assertFalse(row["enabled"])

        self.assertEqual(len(utility_store.list_utility_models()), 1)
        self.assertEqual(len(utility_store.list_utility_models(enabled_only=True)), 0)

        self.assertTrue(utility_store.remove_utility_model("weekly-summary"))
        self.assertIsNone(utility_store.get_utility_model("weekly-summary"))

    def test_invalid_output_modality_rejected(self):
        with self.assertRaises(ValueError):
            utility_store.add_utility_model(
                um_id="bad",
                label="Bad",
                catalog_model="gemini-2.5-flash",
                system_prompt="x",
                output_modality="hologram",
                output_path="",
            )

    def test_parse_input_files_json(self):
        self.assertEqual(utility_store.parse_input_files_json('["a.jpg", "b.pdf"]'), ["a.jpg", "b.pdf"])
        self.assertEqual(utility_store.parse_input_files_json(None), [])
        self.assertEqual(utility_store.parse_input_files_json("not json"), [])


class TestGenerationParsers(unittest.TestCase):
    def test_decode_data_url(self):
        raw = b"\x89PNG\r\n\x1a\n"
        url = "data:image/png;base64," + _b64.b64encode(raw).decode()
        data, mime = gen._decode_data_url(url)
        self.assertEqual(data, raw)
        self.assertEqual(mime, "image/png")

    def test_decode_data_url_rejects_plain_url(self):
        with self.assertRaises(DriverError):
            gen._decode_data_url("https://example.com/x.png")

    def test_parse_image(self):
        raw = b"img-bytes"
        url = "data:image/webp;base64," + _b64.b64encode(raw).decode()
        msg = {"images": [{"type": "image_url", "image_url": {"url": url}}]}
        data, ext, mime, transcript = gen._parse_image(msg)
        self.assertEqual(data, raw)
        self.assertEqual(ext, "webp")
        self.assertEqual(mime, "image/webp")
        self.assertIsNone(transcript)

    def test_parse_image_no_images(self):
        with self.assertRaises(DriverError) as c:
            gen._parse_image({"images": []})
        self.assertEqual(c.exception.code, "no_artifact")

    def test_parse_audio(self):
        raw = b"audio-bytes"
        msg = {"audio": {"data": _b64.b64encode(raw).decode(), "transcript": "hi"}}
        data, ext, mime, transcript = gen._parse_audio(msg, "mp3")
        self.assertEqual(data, raw)
        self.assertEqual(ext, "mp3")
        self.assertEqual(mime, "audio/mpeg")
        self.assertEqual(transcript, "hi")

    def test_parse_audio_no_data(self):
        with self.assertRaises(DriverError) as c:
            gen._parse_audio({"audio": {}}, "wav")
        self.assertEqual(c.exception.code, "no_artifact")

    def test_build_user_content_text_only(self):
        self.assertEqual(gen._build_user_content("hello", [], "image"), "hello")

    def test_build_user_content_with_image_input(self):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "ref.png")
        with open(p, "wb") as f:
            f.write(b"px")
        parts = gen._build_user_content("edit this", [{"modality": "image", "path": p, "ext": "png"}], "image")
        self.assertIsInstance(parts, list)
        self.assertEqual(parts[0]["type"], "text")
        self.assertEqual(parts[1]["type"], "image_url")
        self.assertIn("data:image/png;base64,", parts[1]["image_url"]["url"])

    def test_generation_provider_error_is_normalized(self):
        original = gen.resolve_provider_route
        original_driver = gen.resolve_model_driver

        def fail(_catalog_model):
            raise ProviderRuntimeError("provider_unsupported", "not wired")

        gen.resolve_provider_route = fail
        gen.resolve_model_driver = lambda *args, **kwargs: object()
        try:
            with self.assertRaises(UtilityRunError) as raised:
                gen.generate_media("model", "image", prompt="test")
        finally:
            gen.resolve_provider_route = original
            gen.resolve_model_driver = original_driver
        self.assertEqual(raised.exception.code, "provider_unsupported")


@unittest.skipUnless(_HAS_OPENAI, "openai SDK not installed")
class TestGenerateMediaMocked(unittest.TestCase):
    """End-to-end generate_media with a fake OpenAI client (no network)."""

    def setUp(self):
        self._real = openai.OpenAI
        self._real_resolve_route = gen.resolve_provider_route
        self._prev_key = os.environ.get("OPENROUTER_API_KEY")
        self._prev_openai_key = os.environ.get("OPENAI_API_KEY")
        os.environ["OPENROUTER_API_KEY"] = "sk-or-test"
        os.environ["OPENAI_API_KEY"] = "sk-openai-test"

        def resolve_test_route(catalog_model):
            provider = (
                "openai"
                if catalog_model == "gpt-audio-1.5"
                else "openrouter"
            )
            return resolve_provider_route(
                catalog_model,
                catalog={
                    catalog_model: {
                        "provider": provider,
                        "enabled": True,
                        "input_modalities": "text",
                        "output_modalities": "text,image,audio",
                    }
                },
                providers={
                    "openai": {
                        "cls": "ChatOpenAI",
                        "enabled": True,
                    },
                    "openrouter": {
                        "cls": "ChatOpenAI",
                        "enabled": True,
                    },
                },
            )

        gen.resolve_provider_route = resolve_test_route

    def tearDown(self):
        openai.OpenAI = self._real
        gen.resolve_provider_route = self._real_resolve_route
        if self._prev_key is None:
            os.environ.pop("OPENROUTER_API_KEY", None)
        else:
            os.environ["OPENROUTER_API_KEY"] = self._prev_key
        if self._prev_openai_key is None:
            os.environ.pop("OPENAI_API_KEY", None)
        else:
            os.environ["OPENAI_API_KEY"] = self._prev_openai_key

    def _patch_client(self, payload: dict) -> dict:
        captured: dict = {}

        class _Msg:
            def model_dump(self):
                return payload

        class _Choice:
            message = _Msg()

        class _Resp:
            choices = [_Choice()]

        class _Delta:
            def __init__(self, audio):
                self.audio = audio

            def model_dump(self):
                return {"audio": self.audio}

        class _StreamChoice:
            def __init__(self, audio):
                self.delta = _Delta(audio)

        class _Chunk:
            def __init__(self, audio):
                self.choices = [_StreamChoice(audio)]

        def _audio_stream():
            # Emit the audio payload as two delta fragments to exercise the
            # base64-concat + transcript-accumulate accumulator.
            audio = payload.get("audio") or {}
            b64 = audio.get("data") or ""
            transcript = audio.get("transcript") or ""
            mid = len(b64) // 2
            yield _Chunk({"data": b64[:mid], "transcript": transcript})
            yield _Chunk({"data": b64[mid:]})

        class _Comp:
            def create(self, **kw):
                captured.update(kw)
                if kw.get("stream"):
                    return _audio_stream()
                return _Resp()

        class _Chat:
            completions = _Comp()

        class _Client:
            def __init__(self, **kw):
                captured["_client_kwargs"] = kw
                self.chat = _Chat()

        openai.OpenAI = lambda **kw: _Client(**kw)
        return captured

    def test_generate_each_openrouter_image_binding(self):
        raw = b"\x89PNG-data"
        url = "data:image/png;base64," + _b64.b64encode(raw).decode()
        for catalog_model in (
            "google/gemini-3.1-flash-image",
            "openai/gpt-5.4-image-2",
        ):
            with self.subTest(catalog_model=catalog_model):
                captured = self._patch_client(
                    {
                        "images": [{"image_url": {"url": url}}],
                        "content": "done",
                    }
                )
                data, ext, mime, transcript = gen.generate_media(
                    catalog_model,
                    "image",
                    prompt="a cat",
                )
                self.assertEqual(data, raw)
                self.assertEqual(ext, "png")
                self.assertIsNone(transcript)
                self.assertEqual(
                    captured["extra_body"]["modalities"],
                    ["image", "text"],
                )
                self.assertEqual(
                    captured["_client_kwargs"]["base_url"],
                    "https://openrouter.ai/api/v1",
                )

    def test_generate_each_pcm16_audio_binding(self):
        raw = b"RIFF-audio-pcm-samples"
        cases = {
            "openai/gpt-audio": "https://openrouter.ai/api/v1",
            "openai/gpt-audio-mini": "https://openrouter.ai/api/v1",
            "gpt-audio-1.5": "https://api.openai.com/v1",
        }
        for catalog_model, endpoint in cases.items():
            with self.subTest(catalog_model=catalog_model):
                captured = self._patch_client(
                    {
                        "audio": {
                            "data": _b64.b64encode(raw).decode(),
                            "transcript": "spoken",
                        }
                    }
                )
                data, ext, mime, transcript = gen.generate_media(
                    catalog_model,
                    "audio",
                    prompt="say hi",
                    config={"audio_format": "wav", "voice": "verse"},
                )
                # Streaming audio only supports pcm16; always request it and
                # package the requested file container locally.
                self.assertEqual(
                    captured["extra_body"]["audio"]["format"],
                    "pcm16",
                )
                self.assertEqual(
                    captured["extra_body"]["audio"]["voice"],
                    "verse",
                )
                self.assertTrue(
                    captured.get("stream"),
                    "audio output must be streamed",
                )
                self.assertEqual(captured["_client_kwargs"]["base_url"], endpoint)
                self.assertEqual(ext, "wav")
                self.assertEqual(mime, "audio/wav")
                self.assertEqual(transcript, "spoken")
                import io as _io
                import wave as _wave
                with _wave.open(_io.BytesIO(data), "rb") as wf:
                    self.assertEqual(wf.readframes(wf.getnframes()), raw)


class TestMediaDrivers(unittest.TestCase):
    def test_image_audio_drivers_write_bytes(self):
        self.assertTrue(has_real_driver("image"))
        self.assertTrue(has_real_driver("audio"))
        self.assertFalse(has_real_driver("video"))
        d = tempfile.mkdtemp()
        ip = os.path.join(d, "a.png")
        ap = os.path.join(d, "a.mp3")
        get_output_driver("image")(ip, b"\x01\x02")
        get_output_driver("audio")(ap, b"\x03\x04")
        with open(ip, "rb") as f:
            self.assertEqual(f.read(), b"\x01\x02")
        with open(ap, "rb") as f:
            self.assertEqual(f.read(), b"\x03\x04")


class TestRunUtilityModelMedia(_TempAgentsDB):
    """End-to-end ``run_utility_model`` for image/audio output (TD-UTIL-002).

    ``generate_media`` is mocked, so these run without network or the ``openai``
    SDK. This is the coverage that was MISSING and let the media-output gap ship:
    the runner was only ever tested for ``output_modality='text'``, so the
    unconditional fall-through to the text path went unnoticed. These tests assert
    the runner (a) calls ``generate_media``, (b) writes the real media artifact
    with the correct extension, and (c) never falls through to the text path.
    """

    def setUp(self):
        super().setUp()
        from harness import utility_runner as runner
        from model_drivers import registry

        self.runner = runner
        self.registry = registry
        # Permissive output map so validate_output_artifact passes for png/mp3.
        modality_maps.save_modality_map(
            "gen-model",
            {
                "input": {"text": ["*"], "image": ["png", "jpg"]},
                "output": {"image": ["png"], "audio": ["mp3"], "text": ["*"]},
            },
            agents_db=self.db,
        )
        # Stub the catalog lookup — "gen-model" is not in the deployed models.ini.
        self._real_load = runner.load_catalog
        self._real_entry = runner.catalog_entry_for_model
        self._real_read = runner.read_setup_value
        self._real_gen = gen.generate_media
        self._real_resolve_driver = registry.resolve_model_driver
        self._real_chat = runner._invoke_chat_model
        self._real_lock = runner._RUN_LOCK_DIR
        runner.load_catalog = lambda: {}
        runner.catalog_entry_for_model = lambda m, c: {
            "enabled": True,
            "output_modalities": "image,audio,video,text",
        }
        # Force deterministic setup.ini reads (write_manifest=true default).
        runner.read_setup_value = lambda section, key, default="": default
        # Keep the run-lock off the real system path (/var/lib/versa-agi/...).
        runner._RUN_LOCK_DIR = os.path.join(self._dir, "locks")
        registry.resolve_model_driver = lambda *args, **kwargs: object()

    def tearDown(self):
        self.runner.load_catalog = self._real_load
        self.runner.catalog_entry_for_model = self._real_entry
        self.runner.read_setup_value = self._real_read
        self.runner._invoke_chat_model = self._real_chat
        self.runner._RUN_LOCK_DIR = self._real_lock
        self.registry.resolve_model_driver = self._real_resolve_driver
        gen.generate_media = self._real_gen
        super().tearDown()

    def _add_um(self, modality: str, config_json: str | None = None) -> str:
        um_id = f"gen-{modality}"
        utility_store.add_utility_model(
            um_id=um_id,
            label=f"Gen {modality}",
            catalog_model="gen-model",
            system_prompt="make a thing",
            output_modality=modality,
            output_path=self._dir,
            config_json=config_json,
        )
        return um_id

    def test_image_um_writes_png_and_never_calls_chat(self):
        gen.generate_media = lambda *a, **k: (b"\x89PNG-bytes", "png", "image/png", None)

        def _boom(*a, **k):
            raise AssertionError("text path (_invoke_chat_model) must not run for media output")

        self.runner._invoke_chat_model = _boom

        res = self.runner.run_utility_model(self._add_um("image"), output_dir=self._dir, context_agent="coa")

        self.assertTrue(res["success"])
        self.assertEqual(len(res["artifacts"]), 1)
        art = res["artifacts"][0]
        self.assertEqual(art["modality"], "image")
        self.assertEqual(art["ext"], "png")
        self.assertTrue(art["path"].endswith(".png"))
        self.assertTrue(os.path.isfile(art["path"]))
        with open(art["path"], "rb") as f:
            self.assertEqual(f.read(), b"\x89PNG-bytes")
        self.assertTrue(os.path.isfile(os.path.join(self._dir, "manifest.json")))

    def test_audio_um_writes_mp3_with_transcript(self):
        gen.generate_media = lambda *a, **k: (b"ID3-audio", "mp3", "audio/mpeg", "spoken words")
        res = self.runner.run_utility_model(self._add_um("audio"), output_dir=self._dir, context_agent="coa")
        art = res["artifacts"][0]
        self.assertEqual(art["modality"], "audio")
        self.assertEqual(art["ext"], "mp3")
        self.assertTrue(art["path"].endswith(".mp3"))
        self.assertEqual(art["transcript"], "spoken words")
        with open(art["path"], "rb") as f:
            self.assertEqual(f.read(), b"ID3-audio")

    def test_config_json_round_trips_into_generate_media(self):
        seen: dict = {}

        def _capture(model, modality, *, prompt, input_files, config):
            seen["model"] = model
            seen["modality"] = modality
            seen["prompt"] = prompt
            seen["config"] = config
            return (b"x", "png", "image/png", None)

        gen.generate_media = _capture
        self.runner.run_utility_model(
            self._add_um("image", config_json='{"voice": "verse", "audio_format": "mp3"}'),
            output_dir=self._dir,
            context_agent="coa",
        )
        self.assertEqual(seen["model"], "gen-model")
        self.assertEqual(seen["modality"], "image")
        self.assertEqual(seen["prompt"], "make a thing")
        self.assertEqual(seen["config"].get("voice"), "verse")

    def test_unbound_media_model_returns_no_driver(self):
        self.registry.resolve_model_driver = lambda *args, **kwargs: None

        def _boom(*args, **kwargs):
            raise AssertionError("generate_media must not run without an exact driver")

        gen.generate_media = _boom
        with self.assertRaises(UtilityRunError) as raised:
            self.runner.run_utility_model(
                self._add_um("image"),
                output_dir=self._dir,
                context_agent="coa",
            )
        self.assertEqual(raised.exception.code, "no_driver")

    def test_video_um_still_stubs_driver_pending(self):
        # Video has no output model/driver — must raise driver_pending, never
        # reach generate_media or the text path.
        def _boom_gen(*a, **k):
            raise AssertionError("generate_media must not run for video")

        gen.generate_media = _boom_gen
        with self.assertRaises(UtilityRunError) as ctx:
            self.runner.run_utility_model(self._add_um("video"), output_dir=self._dir, context_agent="coa")
        self.assertEqual(ctx.exception.code, "driver_pending")


if __name__ == "__main__":
    unittest.main()
