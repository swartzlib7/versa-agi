"""What `agictl message send` may attach, and that a message never goes without an attachment
it was given (state_message_body.md rules 9 to 13).
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import unittest
from unittest.mock import patch

CORE_INFRA = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, CORE_INFRA)
sys.path.insert(0, os.path.join(CORE_INFRA, "agictl"))

import message_attachments as rules  # noqa: E402

try:  # click and rich are not installed on every interpreter the suite runs on
    from click.testing import CliRunner
    import agictl.cli as agictl_cli
    import comms
except Exception:  # noqa: BLE001
    CliRunner = agictl_cli = comms = None

SKILLS = os.path.join(CORE_INFRA, "skills")
SERVER = os.path.join(CORE_INFRA, "..", "..", "..", "app", "functions", "src", "index.ts")
APP = os.path.join(CORE_INFRA, "..", "..", "..", "app", "lib", "features", "common", "data",
                   "media_upload_service.dart")
PU = "2yLBOuDkgBaq8rIXVZb6HBuTY5c4"


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as handle:
        return handle.read()


class _Files(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)

    def file(self, name: str, content: bytes = b"x", size: int = 0) -> str:
        path = os.path.join(self._dir.name, name)
        with open(path, "wb") as handle:
            handle.write(content)
            if size:
                handle.truncate(size)
        return path

    def missing(self, name: str) -> str:
        return os.path.join(self._dir.name, name)


class TestWhatMayBeAttached(_Files):
    def test_a_picture_a_markdown_file_and_a_link_pass(self):
        found = rules.problems([self.file("chart.PNG")], [self.file("october_sales.md", b"# Sales\n")],
                               ["https://docs.example.com/api"])
        self.assertEqual(found, [])

    def test_pictures_and_files_have_a_type(self):
        self.assertEqual(sorted(set(rules.PICTURE_TYPES.values())),
                         ["image/gif", "image/heic", "image/heif", "image/jpeg", "image/png", "image/webp"])
        self.assertEqual(rules.media_mime("/a/b/photo.JPG"), "image/jpeg")
        self.assertEqual(rules.media_mime("report.pdf"), "application/pdf")
        self.assertEqual(rules.media_mime("settings.ini"), "text/x-ini")
        self.assertEqual(rules.media_mime("sales.XLSX"),
                         "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        self.assertEqual(rules.media_mime("song.mp3"), "")

    def test_the_files_the_app_can_send_pass(self):
        names = ("contract.pdf", "letter.doc", "letter.docx", "old.xls", "sales.xlsx", "notes.txt",
                 "sales.csv", "data.json", "settings.ini", "readme.md", "bundle.zip")
        self.assertEqual(rules.problems([self.file(name) for name in names[:10]], [], []), [])
        self.assertEqual(rules.problems([self.file(names[10])], [], []), [])

    def test_missing_picture(self):
        found = rules.problems([self.missing("chart.png")], [], [])
        self.assertEqual(len(found), 1)
        self.assertIn("there is no file at this path", found[0])

    def test_audio_cannot_be_attached_and_the_agent_is_told_what_to_do(self):
        for name in ("call.mp3", "clip.mp4", "setup.exe", "backup.tar.gz"):
            found = rules.problems([self.file(name)], [], [])
            self.assertEqual(len(found), 1, name)
            self.assertIn(f"{name}: this kind of file cannot be attached.", found[0])
            self.assertIn("A picture (JPEG, PNG, GIF, WebP or HEIC) or a file "
                          "(PDF, Word, Excel, CSV, JSON, INI, TXT, Markdown or ZIP) can be attached.", found[0])
            self.assertIn("Tell the person what the file is and where it is", found[0])

    def test_a_script_or_log_is_pointed_to_markdown(self):
        for name in ("backup.sh", "app.log", "deploy.py", "config.yaml"):
            found = rules.problems([self.file(name)], [], [])
            self.assertEqual(len(found), 1, name)
            self.assertIn("This is a text file", found[0])
            self.assertIn("attach that with --markdown", found[0])

    def test_picture_over_20_mb(self):
        found = rules.problems([self.file("big.png", size=rules.MEDIA_MAX_BYTES + 1)], [], [])
        self.assertEqual(len(found), 1)
        self.assertIn("over the 20.0 MB limit", found[0])
        self.assertEqual(rules.problems([self.file("ok.png", size=rules.MEDIA_MAX_BYTES)], [], []), [])

    def test_empty_picture(self):
        found = rules.problems([self.file("empty.png", b"")], [], [])
        self.assertIn("the file is empty", found[0])

    def test_missing_markdown_file(self):
        found = rules.problems([], [self.missing("report.md")], [])
        self.assertEqual(len(found), 1)
        self.assertIn("Write the file in your workspace first", found[0])

    def test_markdown_over_5_mb(self):
        found = rules.problems([], [self.file("huge.md", size=rules.MARKDOWN_MAX_BYTES + 1)], [])
        self.assertIn("over the 5.0 MB limit", found[0])

    def test_markdown_flag_given_a_picture(self):
        found = rules.problems([], [self.file("photo.md", b"\xff\xd8\xff\xe0\x00\x10JFIF")], [])
        self.assertIn("this is not a text file", found[0])
        self.assertIn("a picture goes in --media and any other file in --file", found[0])

    def test_empty_markdown_file(self):
        found = rules.problems([], [self.file("blank.md", b"  \n")], [])
        self.assertIn("the file is empty", found[0])

    def test_any_text_file_passes_as_markdown(self):
        """The flag sends the file's text; the skills say to write it as Markdown."""
        self.assertEqual(rules.problems([], [self.file("notes.txt", b"plain")], []), [])

    def test_link_must_be_a_web_address(self):
        for bad in ("/home/coa/report.md", "docs.example.com", "ftp://x.example", "https://", ""):
            found = rules.problems([], [], [bad])
            self.assertEqual(len(found), 1, bad)
            self.assertIn("not a web address", found[0])
        self.assertEqual(rules.problems([], [], ["http://example.com", "HTTPS://Example.com/a"]), [])

    def test_eleven_attachments_are_refused(self):
        ten = ["https://example.com/%d" % number for number in range(10)]
        self.assertEqual(rules.problems([], [], ten), [])
        found = rules.problems([self.file("a.png")], [], ten)
        self.assertEqual(len(found), 1)
        self.assertIn("11 attachments: a message carries at most 10", found[0])

    def test_every_problem_is_reported_at_once(self):
        found = rules.problems([self.file("a.mp3")], [self.missing("b.md")], ["nope"])
        self.assertEqual(len(found), 3)

    def test_refusal_says_nothing_was_sent_first(self):
        text = rules.refusal(["a.mp3: no."])
        self.assertTrue(text.startswith("Nothing was sent. a.mp3: no."))
        self.assertIn("send the message again", text)


class TestInsideTheInstall(_Files):
    """VersaVoice off: files are passed as paths, so only "the file exists" applies."""

    def test_any_existing_file_passes(self):
        found = rules.problems([self.file("call.mp3")], [self.file("blank.md", b"")], ["not-a-link"],
                               to_versavoice=False)
        self.assertEqual(found, [])

    def test_a_missing_file_is_still_refused(self):
        found = rules.problems([self.missing("a.png")], [self.missing("b.md")], [], to_versavoice=False)
        self.assertEqual(len(found), 2)

    def test_links_go_under_the_text(self):
        self.assertEqual(rules.text_with_links("Here it is.", []), "Here it is.")
        self.assertEqual(rules.text_with_links("Here it is.", ["https://a.example", " https://b.example "]),
                         "Here it is.\n\nLinks:\n- https://a.example\n- https://b.example")


class TestSkillsSayTheSame(unittest.TestCase):
    def test_table_in_both_communication_skills(self):
        for name in ("communication_basic.md", "communication.md"):
            text = _read(os.path.join(SKILLS, name))
            self.assertIn(f"| A picture: photo, screenshot, chart | `--media <path>` | {rules.PICTURE_KINDS}; "
                          "20 MB each |", text, name)
            self.assertIn("| `--markdown <path>` | A text file written as Markdown; 5 MB each |", text, name)
            self.assertIn("| A file to keep or open in another program: PDF, Word, Excel, CSV, JSON, INI, TXT, ZIP "
                          "| `--file <path>` | 20 MB each |", text, name)
            self.assertIn("| Anything else: audio, video, a program, another kind of archive | — | Cannot be attached",
                          text, name)

    def test_every_kind_the_table_names_is_accepted(self):
        for extension in ("pdf", "doc", "docx", "xls", "xlsx", "csv", "json", "ini", "txt", "md", "zip"):
            self.assertIn(extension, rules.FILE_TYPES)

    def test_limits_in_the_table_are_the_limits_checked(self):
        self.assertEqual(rules.MEDIA_MAX_BYTES, 20 * 1024 * 1024)
        self.assertEqual(rules.MARKDOWN_MAX_BYTES, 5 * 1024 * 1024)
        self.assertEqual(rules.MAX_ATTACHMENTS, 10)

    def test_always_present_rules_say_how_to_write_the_file(self):
        text = _read(os.path.join(SKILLS, "communication_basic.md"))
        section = text[text.index("## How to Send Messages"):text.index("## How to Read Messages")]
        self.assertIn("code inside a fenced code block, data as a table", section)
        self.assertIn("The file name is what the person sees.", section)
        self.assertIn("**Read here or open elsewhere?**", section)
        self.assertIn("The message goes with every attachment or not at all.", section)

    def test_nothing_says_media_takes_documents_or_50_mb(self):
        for name in ("communication.md", "communication_basic.md", "cli_reference.md", "cli_reference_agent.md"):
            text = _read(os.path.join(SKILLS, name))
            self.assertNotIn("images, audio, documents", text, name)
            self.assertNotIn("10 per message, 50MB per file", text, name)
            self.assertNotIn("truncate silently", text, name)

    def test_references_name_the_kinds_and_the_refusal(self):
        for name in ("cli_reference.md", "cli_reference_agent.md"):
            text = _read(os.path.join(SKILLS, name))
            self.assertIn(f"A picture: {rules.PICTURE_KINDS}, 20 MB each", text, name)
            self.assertIn(f"| `--file` | File path |", text, name)
            self.assertIn(f"{rules.FILE_KINDS}, 20 MB each", text, name)
            self.assertIn("the send is refused and nothing goes out", text, name)


@unittest.skipUnless(os.path.isfile(SERVER) and os.path.isfile(APP),
                     "the VersaVoice AI source is not next to this checkout")
class TestSameListAsTheServerAndTheApp(unittest.TestCase):
    """Three lists that must agree: the app's own uploads, the API endpoint agents use, and ours."""

    def server_types(self) -> set[str]:
        source = _read(SERVER)
        start = source.index("async function handleUploadMedia(")
        block = source[source.index("const OOXML =", start):source.index("];", source.index("const ALLOWED_MIMES = [", start))]
        ooxml = re.search(r'const OOXML = "([^"]+)"', block).group(1)
        types = set(re.findall(r'"((?:image|application|text)/[\w.+-]+)"', block))
        types |= {ooxml + tail for tail in re.findall(r'OOXML \+ "([\w.]+)"', block)}
        return types - {ooxml}

    def app_types(self) -> set[str]:
        source = _read(APP)
        types = set()
        for name in ("imageMimeTypes", "fileMimeTypes"):
            start = source.index(f"static const Set<String> {name} = {{")
            types |= set(re.findall(r"'([\w.+/-]+)'", source[start:source.index("};", start)]))
        return types

    def test_server_takes_what_the_app_sends(self):
        self.assertEqual(self.server_types(), self.app_types())

    def test_every_type_we_send_is_one_the_server_takes(self):
        self.assertLessEqual(set(rules.MEDIA_TYPES.values()), self.server_types())

    def test_we_cover_every_extension_the_app_maps(self):
        source = _read(APP)
        start = source.index("static const Map<String, String> extensionMimeMap = {")
        mapped = dict(re.findall(r"'(\w+)':\s*'([\w.+/-]+)'", source[start:source.index("};", start)]))
        self.assertEqual(rules.MEDIA_TYPES, mapped)


@unittest.skipIf(comms is None, "click or rich is not installed")
class TestBuildReportsWhatItCouldNotBuild(_Files):
    def test_refused_upload_is_a_problem(self):
        found: list[str] = []
        refused = {"error": "api_account_pending", "message": "This API account is awaiting confirmation."}
        with patch.object(comms, "api_request", return_value=refused):
            built = comms.build_attachments("token", "sub", PU, media_paths=[self.file("chart.png")],
                                            problems=found)
        self.assertEqual(built, [])
        self.assertEqual(len(found), 1)
        self.assertIn("chart.png: the upload was refused (This API account is awaiting confirmation.)",
                      found[0])

    def test_upload_sends_the_type_from_the_one_list(self):
        answer = {"downloadUrl": "https://storage.example/x", "meta": {"fileName": "p.heic"}}
        with patch.object(comms, "api_request", return_value=answer) as request:
            found: list[str] = []
            built = comms.build_attachments("token", "sub", PU, media_paths=[self.file("p.heic")], problems=found)
        self.assertEqual(found, [])
        self.assertEqual(built[0]["type"], "media")
        self.assertEqual(request.call_args.kwargs["body"]["mimeType"], "image/heic")

    def test_missing_markdown_is_a_problem(self):
        found: list[str] = []
        built = comms.build_attachments("token", "sub", PU, markdown_paths=[self.missing("r.md")], problems=found)
        self.assertEqual(built, [])
        self.assertIn("--markdown", found[0])

    def test_markdown_title_is_the_file_name(self):
        built = comms.build_attachments("token", "sub", PU,
                                        markdown_paths=[self.file("october_sales.md", b"# Sales\n")])
        self.assertEqual(built[0]["meta"]["title"], "october_sales")
        self.assertEqual(built[0]["value"], "# Sales\n")


@unittest.skipIf(agictl_cli is None, "click or rich is not installed")
class TestSendRefuses(_Files):
    """`agictl message send`: every attachment, or no message."""

    def send(self, *arguments, versavoice=True, upload=None):
        identity = {"versavoice": {"api_token": "token", "sub_account_id": "sub"}, "primary_user": {"uid": PU}}
        with patch.object(agictl_cli, "_is_vv_enabled", return_value=versavoice), \
                patch.object(agictl_cli, "get_config", return_value=identity), \
                patch.object(agictl_cli, "get_agent_name", return_value="coa"), \
                patch.object(agictl_cli, "send_message", return_value=True) as sent, \
                patch.object(agictl_cli, "_send_as_internal") as internal, \
                patch.object(comms, "api_request", return_value=upload) as request:
            result = CliRunner().invoke(agictl_cli.message, ["send", PU, "Here it is.", *arguments])
        return result, sent, internal, request

    def answer(self, result) -> dict:
        return json.loads(result.output[result.output.index("{"):])

    def test_wrong_kind_is_refused_before_anything_is_sent(self):
        result, sent, _, request = self.send("--file", self.file("call.mp3"))
        self.assertEqual(result.exit_code, 1)
        answer = self.answer(result)
        self.assertFalse(answer["success"])
        self.assertTrue(answer["error"].startswith("Nothing was sent."))
        self.assertIn("this kind of file cannot be attached", answer["error"])
        sent.assert_not_called()
        request.assert_not_called()

    def test_missing_markdown_file_is_refused(self):
        result, sent, _, _ = self.send("--markdown", self.missing("report.md"))
        self.assertEqual(result.exit_code, 1)
        sent.assert_not_called()

    def test_old_flag_spelling_is_checked_and_attached_the_same(self):
        result, sent, _, _ = self.send("--markdown-paths", self.missing("report.md"))
        self.assertEqual(result.exit_code, 1)
        sent.assert_not_called()
        result, sent, _, _ = self.send("--markdown-paths", self.file("steps.md", b"# Steps\n"))
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(sent.call_args.kwargs["attachments"][0]["meta"]["title"], "steps")

    def test_refused_upload_stops_the_send(self):
        refused = {"error": "upload_failed", "message": "Failed to upload file to storage."}
        result, sent, _, request = self.send("--media", self.file("chart.png"), upload=refused)
        self.assertEqual(result.exit_code, 1)
        request.assert_called_once()
        sent.assert_not_called()
        self.assertIn("the upload was refused (Failed to upload file to storage.)", self.answer(result)["error"])

    def test_good_attachments_are_sent(self):
        uploaded = {"downloadUrl": "https://storage.example/x", "meta": {"fileName": "chart.png"}}
        result, sent, _, request = self.send("--media", self.file("chart.png"), "--file",
                                             self.file("contract.pdf"), "--markdown",
                                             self.file("october_sales.md", b"# Sales\n"),
                                             "--url", "https://docs.example.com", upload=uploaded)
        self.assertEqual(result.exit_code, 0, result.output)
        kinds = [attachment["type"] for attachment in sent.call_args.kwargs["attachments"]]
        self.assertEqual(kinds, ["media", "media", "markdown", "url"])
        self.assertEqual(self.answer(result)["attachments"], 4)
        uploaded_as = [call.kwargs["body"]["mimeType"] for call in request.call_args_list]
        self.assertEqual(uploaded_as, ["image/png", "application/pdf"])

    def test_inside_the_install_links_are_kept_under_the_text(self):
        result, sent, internal, _ = self.send("--url", "https://docs.example.com", "--media",
                                              self.file("contract.pdf"), versavoice=False)
        self.assertEqual(result.exit_code, 0, result.output)
        sent.assert_not_called()
        self.assertEqual(internal.call_args.args[1], "Here it is.\n\nLinks:\n- https://docs.example.com")
        self.assertEqual(len(internal.call_args.kwargs["attachment_paths"]), 1)

    def test_inside_the_install_a_missing_file_is_refused(self):
        result, _, internal, _ = self.send("--markdown", self.missing("report.md"), versavoice=False)
        self.assertEqual(result.exit_code, 1)
        internal.assert_not_called()


if __name__ == "__main__":
    unittest.main()
