"""Update notes for COA (state_update_notes.md, UPD-01..03)."""

from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

CORE_INFRA = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REPO_ROOT = os.path.dirname(os.path.dirname(CORE_INFRA))
sys.path.insert(0, CORE_INFRA)
sys.path.insert(0, os.path.join(CORE_INFRA, "agictl"))

from agictl import update_notes  # noqa: E402

try:
    from click.testing import CliRunner
    import agictl.cli as agictl_cli  # noqa: E402
except ImportError:  # harness venv has no click
    agictl_cli = None

SCRIPTS = os.path.join(CORE_INFRA, "scripts")
NOTE = "# Times are local\n\n## Tell the Primary User\n\nStop adding an offset.\n"   # test text only


def _write(directory, name, text):
    with open(os.path.join(directory, name), "w", encoding="utf-8") as fh:
        fh.write(text)


class TestNotes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_release_notes_in_version_order_then_the_draft(self):
        _write(self.dir, "next.md", "# Draft\n## Tell the Primary User\nx")
        _write(self.dir, "3.10.0.md", "# Ten\n## Tell the Primary User\nx")
        _write(self.dir, "3.9.0.md", "# Nine\n## Tell the Primary User\nx")
        _write(self.dir, "README.md", "# About this folder")
        _write(self.dir, "ideas.md", "# Not a release")
        _write(self.dir, "notes.txt", "ignored")
        notes = update_notes.list_notes(self.dir)
        self.assertEqual([n["name"] for n in notes], ["3.9.0", "3.10.0", "next"])
        self.assertEqual([n["draft"] for n in notes], [False, False, True])
        self.assertEqual(notes[0]["title"], "Nine")

    def test_due_is_decided_by_release_version(self):
        for name in ("3.9.0.md", "3.10.0.md", "3.11.0.md", "next.md"):
            _write(self.dir, name, NOTE)
        notes = update_notes.list_notes(self.dir)
        due = lambda delivered, installed: [n["version"] for n in update_notes.due_notes(notes, delivered, installed)]
        self.assertEqual(due({}, "3.10.0"), ["3.9.0", "3.10.0"])            # 3.11.0 is newer than what is installed
        self.assertEqual(due({"3.9.0": "t"}, "3.10.0"), ["3.10.0"])
        self.assertEqual(due({"3.9.0": "t", "3.10.0": "t"}, "3.10.0"), [])
        self.assertEqual(due({}, "3.11.0"), ["3.9.0", "3.10.0", "3.11.0"])
        self.assertEqual(due(["3.9.0"], "3.11.0"), ["3.10.0", "3.11.0"])

    def test_the_draft_is_never_due(self):
        _write(self.dir, "next.md", NOTE)
        self.assertEqual(update_notes.due_notes(update_notes.list_notes(self.dir), {}, "9.9.9"), [])

    def test_editing_a_delivered_note_does_not_make_it_due_again(self):
        _write(self.dir, "3.9.0.md", NOTE)
        delivered = {"3.9.0": "2026-11-13 12:00:00"}
        _write(self.dir, "3.9.0.md", NOTE + "A later edit.\n")
        self.assertEqual(update_notes.due_notes(update_notes.list_notes(self.dir), delivered, "3.9.0"), [])

    def test_a_malformed_note_is_not_due(self):
        _write(self.dir, "3.9.0.md", "No title line")
        _write(self.dir, "3.9.1.md", "# Long\n## Tell the Primary User\n" + "x" * 5000)
        _write(self.dir, "3.9.2.md", "# No part\n\nSome text with none of the three headings.")
        notes = update_notes.list_notes(self.dir)
        self.assertTrue(all(n["problem"] for n in notes))
        self.assertIn("at least one of these headings", notes[2]["problem"])
        self.assertEqual(update_notes.due_notes(notes, {}, "3.9.2"), [])

    def test_a_note_has_one_or_both_of_the_two_parts(self):
        self.assertEqual(update_notes.SECTIONS, ("## Maintenance after this update", "## Tell the Primary User"))
        self.assertEqual(update_notes.note_problem("# Title\n\n## Tell the Primary User\n\n- point"), "")
        good = ("# Title\n\n## Maintenance after this update\n\n1. **Memory.** It may be stale.\n"
                "   - Check: `agictl memory project list`.\n   - If an entry is wrong: correct it.\n"
                "   - If the entries are right: there is nothing to do.\n")
        self.assertEqual(update_notes.note_problem(good), "")
        self.assertEqual(len(update_notes.maintenance_items(good)), 1)

    def test_a_problem_without_a_solution_is_not_a_note(self):
        # PU 2026-11-13: never tell COA about a problem it can do nothing about.
        stated_only = ("# Title\n\n## Maintenance after this update\n\n"
                       "1. **Cycle history.** Old token totals may be wrong. Nothing can be repaired.\n")
        self.assertIn("needs a `Check:` line", update_notes.note_problem(stated_only))
        check_without_action = ("# Title\n\n## Maintenance after this update\n\n"
                                "1. **Memory.** It may be stale.\n   - Check: the memory list.\n")
        self.assertIn("Maintenance item 1", update_notes.note_problem(check_without_action))
        second_bad = ("# Title\n\n## Maintenance after this update\n\n1. **A.** x\n   - Check: a.\n   - If so: fix.\n"
                      "2. **B.** Just so you know.\n\n## Tell the Primary User\n\n- point\n")
        self.assertIn("Maintenance item 2", update_notes.note_problem(second_bad))

    def test_message_names_the_release_and_does_not_presume(self):
        _write(self.dir, "3.9.0.md", NOTE)
        note = update_notes.list_notes(self.dir)[0]
        text = update_notes.message_text(note)
        self.assertTrue(text.startswith("VERSA AGi UPDATE NOTE — version 3.9.0\n"))
        self.assertIn("Stop adding an offset.", text)
        self.assertIn("mark this message processed", text)
        self.assertIn("do not reply", text)
        self.assertEqual(update_notes.message_id("3.9.0"), "updnote_3.9.0")

    def test_every_note_closes_as_one_job_with_an_end(self):
        closing = update_notes.closing_text("3.9.0")
        for needed in ("one job with an end", "not a standing duty", "do each check once",
                       "Where a check finds nothing, there is nothing to do",
                       "Tell the Primary User: send them those points once",
                       "create ONE task", "Do not create a recurring task",
                       "You may give a sub-agent a specific task", "what done looks like",
                       "The job is closed when", "agictl memory system set update_note_3.9.0",
                       "Do not repeat the checks in later cycles", "close anyway"):
            self.assertIn(needed, closing)
        _write(self.dir, "3.9.0.md", NOTE)
        self.assertTrue(update_notes.message_text(update_notes.list_notes(self.dir)[0]).endswith(closing))

    def test_shipped_notes_ask_for_nothing_open_ended(self):
        for note in update_notes.list_notes():
            lower = note["text"].lower()
            for open_ended in ("every cycle", "each cycle", "keep checking", "keep watching", "monitor",
                               "from now on check", "regularly", "ongoing"):
                self.assertNotIn(open_ended, lower, f"{note['name']}: {open_ended}")
            self.assertLessEqual(len(update_notes.message_text(note)), 6000)

    def test_missing_directory_is_no_notes(self):
        self.assertEqual(update_notes.list_notes(os.path.join(self.dir, "nope")), [])

    def test_the_shipped_notes_are_well_formed(self):
        for note in update_notes.list_notes():
            self.assertEqual(note["problem"], "", note["name"])
            self.assertLessEqual(len(note["text"]), update_notes.NOTE_MAX_CHARS)


@unittest.skipIf(agictl_cli is None, "click not installed in this interpreter")
class TestDelivery(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = self.tmp.name
        self.notes = os.path.join(d, "notes")
        os.mkdir(self.notes)
        self.cycles_db = os.path.join(d, "cycles.db")
        self.messages_db = os.path.join(d, "messages.db")
        os.system(f"bash {SCRIPTS}/init_cycles_db.sh {self.cycles_db} >/dev/null 2>&1")
        os.system(f"bash {SCRIPTS}/init_messages_db.sh {self.messages_db} >/dev/null 2>&1")

    def tearDown(self):
        self.tmp.cleanup()

    def run_cmd(self, *args, installed="3.9.0"):
        with patch.object(agictl_cli, "cycles_db", self.cycles_db), \
                patch.object(agictl_cli, "messages_db", self.messages_db):
            result = CliRunner().invoke(agictl_cli.cli, ["system", "update-notes", "--notes-dir", self.notes,
                                                         "--installed-version", installed, *args])
        return result, (json.loads(result.output) if result.output.strip().startswith("{") else {})

    def inbox(self):
        with sqlite3.connect(self.messages_db) as conn:
            conn.row_factory = sqlite3.Row
            return [dict(r) for r in conn.execute("SELECT * FROM messages ORDER BY id")]

    def record(self):
        with sqlite3.connect(self.cycles_db) as conn:
            row = conn.execute("SELECT value FROM config WHERE key='update_notes_delivered'").fetchone()
        return json.loads(row[0]) if row else {}

    def test_an_update_puts_the_release_note_in_coas_inbox_once(self):
        _write(self.notes, "3.9.0.md", NOTE)
        result, out = self.run_cmd("--deliver")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(out["sent"], ["3.9.0"])
        rows = self.inbox()
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual((row["direction"], row["from_user_id"], row["to_user_id"], row["status"], row["channel"]),
                         ("received", "lifeline", "coa", "unprocessed", "internal"))
        self.assertEqual(row["message_id"], "updnote_3.9.0")
        self.assertTrue(row["text"].startswith("VERSA AGi UPDATE NOTE — version 3.9.0"))
        self.assertEqual(list(self.record()), ["3.9.0"])
        # Every later update of the same release sends nothing.
        for _ in range(3):
            _, again = self.run_cmd("--deliver")
            self.assertEqual(again["sent"], [])
        self.assertEqual(len(self.inbox()), 1)

    def test_the_draft_is_not_delivered(self):
        _write(self.notes, "next.md", NOTE)
        _, out = self.run_cmd("--deliver")
        self.assertEqual(out["sent"], [])
        self.assertEqual(self.inbox(), [])

    def test_a_note_for_a_release_newer_than_the_install_waits(self):
        _write(self.notes, "3.10.0.md", NOTE)
        _, out = self.run_cmd("--deliver", installed="3.9.0")
        self.assertEqual(out["sent"], [])
        _, out = self.run_cmd("--deliver", installed="3.10.0")
        self.assertEqual(out["sent"], ["3.10.0"])

    def test_an_instance_that_skipped_releases_gets_each_note_in_order(self):
        _write(self.notes, "3.9.0.md", NOTE)
        _write(self.notes, "3.10.0.md", "# Another change\n\n## Tell the Primary User\n\nDo this.\n")
        _, out = self.run_cmd("--deliver", installed="3.10.0")
        self.assertEqual(out["sent"], ["3.9.0", "3.10.0"])
        self.assertEqual([r["message_id"] for r in self.inbox()], ["updnote_3.9.0", "updnote_3.10.0"])

    def test_a_later_release_sends_only_its_own_note(self):
        _write(self.notes, "3.9.0.md", NOTE)
        self.run_cmd("--deliver", installed="3.9.0")
        _write(self.notes, "3.10.0.md", "# Another change\n\n## Tell the Primary User\n\nDo this.\n")
        _, out = self.run_cmd("--deliver", installed="3.10.0")
        self.assertEqual(out["sent"], ["3.10.0"])
        self.assertEqual(len(self.inbox()), 2)

    def test_editing_a_shipped_note_never_sends_it_again(self):
        _write(self.notes, "3.9.0.md", NOTE)
        self.run_cmd("--deliver")
        _write(self.notes, "3.9.0.md", NOTE + "Edited later.\n")
        _, out = self.run_cmd("--deliver")
        self.assertEqual(out["sent"], [])
        self.assertEqual(len(self.inbox()), 1)

    def test_a_lost_record_does_not_cause_a_second_message(self):
        # The note is in the inbox but the delivery record is gone (restored database, interrupted run).
        _write(self.notes, "3.9.0.md", NOTE)
        self.run_cmd("--deliver")
        with sqlite3.connect(self.cycles_db) as conn:
            conn.execute("DELETE FROM config WHERE key='update_notes_delivered'")
        _, out = self.run_cmd("--deliver")
        self.assertEqual(out["sent"], [])
        self.assertEqual(out["already_in_inbox"], ["3.9.0"])
        self.assertEqual(len(self.inbox()), 1)
        self.assertEqual(list(self.record()), ["3.9.0"])        # recorded again
        # Also true once COA has processed the message.
        with sqlite3.connect(self.messages_db) as conn:
            conn.execute("UPDATE messages SET status='processed'")
        with sqlite3.connect(self.cycles_db) as conn:
            conn.execute("DELETE FROM config WHERE key='update_notes_delivered'")
        self.run_cmd("--deliver")
        self.assertEqual(len(self.inbox()), 1)

    def test_a_new_installation_records_the_notes_and_sends_nothing(self):
        _write(self.notes, "3.9.0.md", NOTE)
        result, out = self.run_cmd("--mark-delivered")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(out["marked"], ["3.9.0"])
        self.assertEqual(self.inbox(), [])
        _, later = self.run_cmd("--deliver")          # its first update, same release
        self.assertEqual(later["sent"], [])
        self.assertEqual(self.inbox(), [])

    def test_listing_shows_what_was_delivered(self):
        _write(self.notes, "3.9.0.md", NOTE)
        _write(self.notes, "next.md", "# Draft\n## Tell the Primary User\nx")
        _, before = self.run_cmd()
        self.assertEqual([(n["name"], n["draft"], n["delivered_at"]) for n in before["notes"]],
                         [("3.9.0", False, ""), ("next", True, "")])
        self.run_cmd("--deliver")
        _, after = self.run_cmd()
        self.assertTrue(after["notes"][0]["delivered_at"])
        self.assertEqual(after["notes"][1]["delivered_at"], "")
        self.assertEqual(len(self.inbox()), 1)

    def test_a_malformed_note_is_reported_and_not_sent(self):
        _write(self.notes, "3.9.0.md", "no title")
        _, out = self.run_cmd("--deliver")
        self.assertEqual(out["sent"], [])
        self.assertEqual(out["skipped"][0]["name"], "3.9.0")
        self.assertEqual(self.inbox(), [])

    def test_a_note_that_cannot_be_stored_is_tried_again_next_time(self):
        _write(self.notes, "3.9.0.md", NOTE)
        with patch.object(agictl_cli, "_deliver_update_note", return_value=False):
            result, out = self.run_cmd("--deliver")
        self.assertEqual(result.exit_code, 1)
        self.assertEqual(out["failed"], ["3.9.0"])
        self.assertEqual(self.record(), {})
        _, retry = self.run_cmd("--deliver")
        self.assertEqual(retry["sent"], ["3.9.0"])


def _bump_module():
    spec = importlib.util.spec_from_file_location("bump_version", os.path.join(REPO_ROOT, "bump_version.py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules["bump_version"] = module          # dataclasses look the module up by name
    spec.loader.exec_module(module)
    return module


class TestBumpAsksAboutTheNote(unittest.TestCase):
    """UPD-02: every version change decides about a COA note."""

    def setUp(self):
        self.bump = _bump_module()
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self._patches = [patch.object(self.bump, "UPDATE_NOTES_DIR", self.dir),
                         patch.object(self.bump, "PENDING_NOTE", self.dir / "next.md")]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        self.tmp.cleanup()

    def test_pending_note_is_stamped_with_the_new_version(self):
        (self.dir / "next.md").write_text(NOTE)
        self.assertEqual(self.bump.coa_note_plan("3.9.0"), ("rename", "COA update note: next.md → 3.9.0.md"))

    def test_versioned_note_already_there(self):
        (self.dir / "3.9.0.md").write_text(NOTE)
        self.assertEqual(self.bump.coa_note_plan("3.9.0")[0], "present")

    def test_no_note_stops_an_unattended_bump_unless_told(self):
        self.assertEqual(self.bump.coa_note_plan("3.9.0")[0], "none")
        with patch.object(self.bump.sys.stdin, "isatty", return_value=False), self.assertRaises(SystemExit) as ctx:
            self.bump.confirm_no_coa_note("3.9.0", assume_none=False)
        self.assertEqual(ctx.exception.code, 1)
        self.bump.confirm_no_coa_note("3.9.0", assume_none=True)      # --no-coa-note

    def test_two_notes_for_one_release_is_an_error(self):
        (self.dir / "next.md").write_text(NOTE)
        (self.dir / "3.9.0.md").write_text(NOTE)
        with self.assertRaises(ValueError):
            self.bump.coa_note_plan("3.9.0")

    def test_sync_and_edition_date_leave_notes_alone(self):
        with open(os.path.join(REPO_ROOT, "bump_version.py"), encoding="utf-8") as fh:
            src = fh.read()
        self.assertIn("if not keep_version:\n        note_action, note_text = coa_note_plan(new_version)", src)
        self.assertIn('if note_action == "none" and not args.dry_run:', src)


class TestSetupHook(unittest.TestCase):
    def test_update_delivers_and_install_only_records(self):
        with open(os.path.join(CORE_INFRA, "..", "setup.sh"), encoding="utf-8") as fh:
            setup = fh.read()
        block = setup[setup.index("# ─── Update notes for COA"):setup.index("# ─── Summary ───")]
        self.assertIn("agictl system update-notes --deliver", block)
        self.assertIn("agictl system update-notes --mark-delivered", block)
        self.assertLess(block.index('if [ "${UPDATE_MODE}" = true ]; then'), block.index("--deliver"))
        self.assertIn('"${DRY_RUN:-false}" != true', block)
        self.assertNotIn("exit 1", block)

    def test_shipped_notes_check_before_they_act_and_presume_nothing(self):
        notes = {n["name"]: n["text"] for n in update_notes.list_notes()}
        for name, text in notes.items():
            if update_notes.SECTION_MAINTENANCE in text:
                self.assertIn("Nothing below is known to be true on this instance", text, name)
            # A habit seen on one instance is not stated as fact about every instance.
            for presumed in ("you have been", "plus 5 hours", "+5", "5 hours late", "Stop adding",
                             "your researcher"):
                self.assertNotIn(presumed, text, name)
        time_note = notes["3.8.1"]
        for needed in ("If you find one:", "If you find none:", "Due times of open tasks", "The other agents",
                       "This note went to you only", "--due-date"):
            self.assertIn(needed, time_note)

    def test_every_shipped_maintenance_item_has_a_check_an_action_and_a_nothing_to_do(self):
        for note in update_notes.list_notes():
            for item in update_notes.maintenance_items(note["text"]):
                self.assertIn("Check:", item, note["name"])
                self.assertGreaterEqual(item.count("- If "), 2, f"{note['name']}: {item[:40]}")
                self.assertIn("there is nothing to do", item, f"{note['name']}: {item[:40]}")
            # Nothing that is only information: no problem without a solution, no feature tour.
            for only_information in ("Nothing can be repaired", "do not rely on", "for your information",
                                     "## New in this release", "## Fixes that may have affected data"):
                self.assertNotIn(only_information, note["text"], note["name"])

    def test_shipped_notes_have_their_parts_in_order(self):
        for note in update_notes.list_notes():
            positions = [note["text"].find(section) for section in update_notes.SECTIONS]
            present = [p for p in positions if p >= 0]
            self.assertEqual(present, sorted(present), note["name"])
            self.assertTrue(note["text"].startswith(f"# {note['version']} — ") or note["draft"], note["name"])

    def test_the_notes_folder_holds_notes_only(self):
        # The folder is deployed to every installation and synced to the public repository.
        # Guidance for developers lives in design/spec/state/state_update_notes.md, not here.
        for filename in os.listdir(update_notes.NOTES_DIR):
            name, ext = os.path.splitext(filename)
            self.assertEqual(ext, ".md", filename)
            self.assertTrue(update_notes.is_version(name) or name == update_notes.DRAFT_NAME,
                            f"{filename} is not a release note or the draft")


if __name__ == "__main__":
    unittest.main()
