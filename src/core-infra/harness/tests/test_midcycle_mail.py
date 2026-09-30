"""Mid-cycle unread inject for the spawned agent on this cycle.

Run from core-infra:
  python -m unittest harness.tests.test_midcycle_mail harness.tests.test_agent_reads
"""

from __future__ import annotations

import json
import os
import unittest

from harness.midcycle_mail import (
    MidcycleInbox,
    format_arrival_block,
    shown_message_ids,
)

CORE_INFRA = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LIFELINE = os.path.join(CORE_INFRA, "lifeline.sh")

SPAWN_WAKE = """
--- NEW MESSAGES (UNREAD — MUST RESPOND) ---
  [!] [2026-09-29] FROM Stephen (pu-1): already in the wake
     → mark-processed: agictl message mark-processed spawn-id
--- END NEW MESSAGES ---
"""

SYLVIE_ROWS = [
    {
        "message_id": "spawn-id",
        "from_user_id": "pu-1",
        "to_user_id": "sylvie-sub",
        "display_name": "Stephen",
        "text": "already in the wake",
        "created_at": "2026-09-29 01:00:00",
        "status": "unprocessed",
        "direction": "received",
    },
    {
        "message_id": "new-id",
        "from_user_id": "pu-1",
        "to_user_id": "sylvie-sub",
        "display_name": "Stephen",
        "text": "arrived while spawned",
        "created_at": "2026-09-29 01:05:00",
        "raw_payload": json.dumps({
            "agiProjects": [{"id": "26"}],
            "replyToMessageId": "earlier",
        }),
        "status": "unprocessed",
        "direction": "received",
    },
    {
        "message_id": "other-agent",
        "from_user_id": "pu-1",
        "to_user_id": "coa",
        "display_name": "Stephen",
        "text": "for coa",
        "created_at": "2026-09-29 01:06:00",
        "status": "unprocessed",
        "direction": "received",
    },
    {
        "message_id": "sent-id",
        "from_user_id": "sylvie-sub",
        "to_user_id": "pu-1",
        "display_name": "Sylvie",
        "text": "her own outbound",
        "created_at": "2026-09-29 01:07:00",
        "status": "unprocessed",
        "direction": "sent",
    },
]


def _runner(payload):
    calls = []

    def run(argv):
        calls.append(list(argv))
        if payload == "fail":
            raise OSError("agictl unavailable")
        return json.dumps(payload)

    run.calls = calls
    return run


class TestMidcycleMail(unittest.TestCase):
    def test_shown_ids_come_from_the_wake_block(self) -> None:
        self.assertEqual(shown_message_ids(SPAWN_WAKE), {"spawn-id"})

    def test_second_unread_id_is_injected_spawn_id_is_not(self) -> None:
        runner = _runner(SYLVIE_ROWS)
        inbox = MidcycleInbox(
            "sylvie",
            ["sylvie-sub", "sylvie"],
            shown_message_ids(SPAWN_WAKE),
            runner=runner,
        )
        found = inbox.poll()
        self.assertIsNotNone(found)
        text, ids = found
        self.assertEqual(ids, {"new-id"})
        self.assertIn("arrived while spawned", text)
        self.assertNotIn("already in the wake", text)
        self.assertNotIn("for coa", text)
        self.assertNotIn("her own outbound", text)
        self.assertIn("TAGGED PROJECT IDS: 26", text)
        self.assertIn("REPLY TO MESSAGE ID: earlier", text)
        self.assertIn("mark-processed new-id", text.replace("agictl message ", ""))
        self.assertIn("during this cycle", text)
        self.assertEqual(
            runner.calls,
            [["agictl", "message", "received-unread", "--agent", "sylvie"]],
        )
        inbox.commit(ids)
        self.assertIsNone(inbox.poll())

    def test_ids_in_the_system_prompt_are_already_this_cycle(self) -> None:
        prompt = "system\n" + SPAWN_WAKE + "\nYou are waking up.\nWake reason: 1 unprocessed message(s)."
        inbox = MidcycleInbox.from_env("sylvie", prompt)
        inbox.runner = _runner(SYLVIE_ROWS)
        inbox.ids = ["sylvie-sub", "sylvie"]
        found = inbox.poll()
        self.assertIsNotNone(found)
        _, ids = found
        self.assertEqual(ids, {"new-id"})

    def test_format_skips_ids_already_shown(self) -> None:
        text, ids = format_arrival_block(
            [{"message_id": "spawn-id", "from_user_id": "pu", "text": "old", "created_at": "t"}],
            {"spawn-id"},
        )
        self.assertIsNone(text)
        self.assertEqual(ids, set())

    def test_failed_read_does_not_raise(self) -> None:
        notes = []
        inbox = MidcycleInbox(
            "sylvie",
            ["sylvie"],
            set(),
            runner=_runner("fail"),
            log=notes.append,
        )
        self.assertIsNone(inbox.poll())
        self.assertIsNone(inbox.poll())
        self.assertEqual(len(notes), 1)
        self.assertIn("sylvie", notes[0])

    def test_skip_paths_do_not_sync_inbox_and_do_not_spawn(self) -> None:
        with open(LIFELINE, encoding="utf-8") as fh:
            src = fh.read()
        # Free-lock tick, plus the call inside the during-spawn loop.
        self.assertEqual(src.count("_vv_sync_agent_inbox "), 2)
        self.assertEqual(src.count("_vv_inbox_while_spawned "), 1)
        for marker in (
            'log "SKIP spawn: ${AGENT_NAME} (already running — lock held)"',
            'log "SKIP spawn: ${AGENT_NAME} (harness process already running for user ${AGENT_USER})"',
        ):
            start = src.index(marker)
            block = src[start:src.index("continue", start)]
            self.assertNotIn("_vv_sync_agent_inbox ", block)
            self.assertNotIn("_vv_inbox_while_spawned ", block)
            self.assertIn("_run_due_utility_and_scripts ", block)
            self.assertNotIn("agent_harness", block)
        free = src.index("# Idle agents only.")
        free_block = src[free:src.index("_run_due_utility_and_scripts ", free)]
        self.assertIn("_vv_sync_agent_inbox ", free_block)
        self.assertNotIn("_vv_inbox_while_spawned ", free_block)
        harness_at = src.index("python -m harness.agent_harness")
        poll_at = src.index("_vv_inbox_while_spawned ")
        self.assertLess(poll_at, harness_at)
        self.assertIn("INBOX_POLL_PID", src[harness_at:src.index("cp \"${RESULT_FILE}\"", harness_at)])
        loop_at = src.index("_vv_inbox_while_spawned() {")
        loop = src[loop_at:src.index("_vv_run_instance_sync() {", loop_at)]
        self.assertIn("sleep 10", loop)
        self.assertIn("_VV_INSTANCE_SYNCED_THIS_TICK=false", loop)
        self.assertIn('"${agent_name}"', loop)


if __name__ == "__main__":
    unittest.main()
