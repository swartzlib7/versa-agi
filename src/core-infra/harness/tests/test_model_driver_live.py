"""Opt-in paid live acceptance for exact ModelDriver bindings.

Nothing runs unless ``VERSA_DRIVER_LIVE_TEST=1`` and explicit catalog keys are
provided in ``VERSA_DRIVER_LIVE_KEYS``. Image-input probes use a generated 32×32
PNG by default (xAI requires ≥512 total pixels); ``VERSA_DRIVER_LIVE_IMAGE`` can
select a local test image.
"""

from __future__ import annotations

import base64
import os
import sys
import tempfile
import unittest

CORE_INFRA = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
REPOSITORY_MODELS_INI = os.path.join(os.path.dirname(CORE_INFRA), "models.ini")
MODELS_INI = REPOSITORY_MODELS_INI if os.path.isfile(REPOSITORY_MODELS_INI) else None
sys.path.insert(0, CORE_INFRA)

from langchain_core.messages import HumanMessage  # noqa: E402

from harness.generation import generate_media  # noqa: E402
from model_catalog import load_catalog, load_providers  # noqa: E402
from model_drivers.registry import list_model_drivers, resolve_model_driver  # noqa: E402
from provider_runtime import (  # noqa: E402
    create_langchain_client,
    resolve_provider_route,
)


LIVE_ENABLED = os.getenv("VERSA_DRIVER_LIVE_TEST") == "1"
REQUESTED_KEYS = {
    key.strip()
    for key in os.getenv("VERSA_DRIVER_LIVE_KEYS", "").split(",")
    if key.strip()
}
IMAGE_PATH = os.getenv("VERSA_DRIVER_LIVE_IMAGE", "auto")
# Solid 32×32 RGB PNG — meets xAI's ≥512 total-pixel minimum.
_PROBE_PNG = (
    "iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAIAAAD8GO2jAAAAKElEQVR42u3N"
    "MQ0AAAgDsKlFLaKmgoOkSf8ms7cEAoFAIBAIBALBl6AO1cg9OtnDMwAAAABJ"
    "RU5ErkJggg=="
)


@unittest.skipUnless(
    LIVE_ENABLED and REQUESTED_KEYS,
    "paid live probes require VERSA_DRIVER_LIVE_TEST=1 and explicit keys",
)
class TestLiveModelDrivers(unittest.TestCase):
    """Controlled provider calls; select only the exact keys under evaluation."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.catalog = load_catalog(MODELS_INI)
        cls.providers = load_providers(MODELS_INI)
        known_keys = {binding.catalog_key for binding in list_model_drivers()}
        unknown = REQUESTED_KEYS - {"all"} - known_keys
        if unknown:
            raise ValueError(
                f"VERSA_DRIVER_LIVE_KEYS contains unbound keys: {sorted(unknown)}"
            )
        cls._generated_image = None
        cls.image_path = IMAGE_PATH
        if IMAGE_PATH == "auto":
            cls._generated_image = tempfile.NamedTemporaryFile(
                suffix=".png",
                delete=False,
            )
            cls._generated_image.write(base64.b64decode(_PROBE_PNG))
            cls._generated_image.close()
            cls.image_path = cls._generated_image.name

    @classmethod
    def tearDownClass(cls) -> None:
        if cls._generated_image is not None:
            os.unlink(cls._generated_image.name)

    def _selected_bindings(self, direction: str):
        rows = [
            binding
            for binding in list_model_drivers()
            if binding.direction == direction
            and (
                "all" in REQUESTED_KEYS
                or binding.catalog_key in REQUESTED_KEYS
            )
        ]
        requested_for_direction = {
            key
            for key in REQUESTED_KEYS
            if key == "all"
            or any(
                binding.catalog_key == key and binding.direction == direction
                for binding in list_model_drivers()
            )
        }
        if requested_for_direction and not rows:
            self.fail(
                f"No {direction} ModelDriver matched "
                f"{sorted(requested_for_direction)}"
            )
        return rows

    def test_selected_image_input_bindings(self) -> None:
        bindings = self._selected_bindings("input")
        if not bindings:
            self.skipTest("no selected input bindings")
        if not self.image_path or not os.path.isfile(self.image_path):
            self.fail(
                "VERSA_DRIVER_LIVE_IMAGE must identify a small local image "
                "for input probes, or use 'auto'"
            )

        for binding in bindings:
            with self.subTest(catalog_key=binding.catalog_key):
                resolved = resolve_model_driver(
                    binding.catalog_key,
                    "input",
                    "image",
                    catalog=self.catalog,
                    providers=self.providers,
                )
                self.assertIsNotNone(resolved)
                assert resolved is not None
                route = resolve_provider_route(
                    binding.catalog_key,
                    catalog=self.catalog,
                    providers=self.providers,
                )
                client = create_langchain_client(route, native_params={})
                content = resolved.adapter.entrypoint(
                    path=self.image_path,
                    caption="Describe this test image in one short sentence.",
                    config=resolved.binding.config,
                )
                response = client.invoke([HumanMessage(content=content)])
                self.assertIsNotNone(response)
                self.assertIsNotNone(getattr(response, "content", None))

    def test_selected_output_bindings(self) -> None:
        bindings = self._selected_bindings("output")
        if not bindings:
            self.skipTest("no selected output bindings")

        for binding in bindings:
            with self.subTest(
                catalog_key=binding.catalog_key,
                modality=binding.modality,
            ):
                config = (
                    {"audio_format": "wav", "voice": "alloy"}
                    if binding.modality == "audio"
                    else {}
                )
                data, ext, mime, transcript = generate_media(
                    binding.catalog_key,
                    binding.modality,
                    prompt=(
                        "Say: Versa AGi driver test."
                        if binding.modality == "audio"
                        else "A single blue circle on a white background."
                    ),
                    config=config,
                )
                self.assertTrue(data)
                self.assertTrue(ext)
                self.assertTrue(mime.startswith(f"{binding.modality}/"))
                if binding.modality == "audio":
                    self.assertIsInstance(transcript, (str, type(None)))


if __name__ == "__main__":
    unittest.main()
