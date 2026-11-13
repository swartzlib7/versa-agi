"""Regression tests for message-trigger and cycle-closeout integrity."""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

from click.testing import CliRunner

CORE_INFRA = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
sys.path.insert(0, CORE_INFRA)
sys.path.insert(0, os.path.join(CORE_INFRA, "agictl"))

import agictl.cli as agictl_cli  # noqa: E402
from agitop.data.message_reader import MessageReader  # noqa: E402


class TestConversationContextMemory(unittest.TestCase):
    """SPWN-51: `conversation-context` puts memory and profiles in the spawn prompt.

    The tasks.db connection used a name that did not exist; the error was
    swallowed and the blob carried no memory at all.
    """

    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        d = self.tempdir.name
        self.messages_db = os.path.join(d, "messages.db")
        self.tasks_db = os.path.join(d, "tasks.db")
        scripts = os.path.join(CORE_INFRA, "scripts")
        os.system(f"bash {scripts}/init_messages_db.sh {self.messages_db} >/dev/null 2>&1")
        os.system(f"bash {scripts}/init_tasks_db.sh {self.tasks_db} >/dev/null 2>&1")
        with sqlite3.connect(self.messages_db) as conn:
            conn.execute(
                "INSERT INTO messages (direction, from_user_id, to_user_id, display_name, message_id, "
                "text, mode, status, created_at, channel) VALUES ('received', 'steve-uid', 'vv-coa', "
                "'S.', 'm-1', 'hello there', 'typed', 'unprocessed', datetime('now'), 'vv')"
            )
            # An earlier, handled message: a contact's profile and memory come with its history.
            conn.execute(
                "INSERT INTO messages (direction, from_user_id, to_user_id, display_name, message_id, "
                "text, mode, status, created_at, channel) VALUES ('received', 'steve-uid', 'vv-coa', "
                "'S.', 'm-0', 'earlier note', 'typed', 'processed', datetime('now', '-1 day'), 'vv')"
            )
        with sqlite3.connect(self.tasks_db) as conn:
            conn.execute(
                "INSERT INTO connections (uid, display_name, spoken_lang, country) "
                "VALUES ('steve-uid', 'Steve Marsh', 'af', 'South Africa')"
            )
            conn.execute(
                "INSERT INTO agent_memory_connection (agent_name, contact_uid, preferences, rapport_level) "
                "VALUES ('coa', 'steve-uid', 'prefers short replies', 'building')"
            )
            conn.execute(
                "INSERT INTO projects (id, name, workspace_path, status) VALUES (7, 'Atlas', '/tmp/atlas', 'active')"
            )
            conn.execute(
                "INSERT INTO agent_memory_project (agent_name, project_id, current_phase, next_steps) "
                "VALUES ('coa', 7, 'testing', 'ship the report')"
            )

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _blob(self) -> str:
        with (
            patch.object(agictl_cli, "message_reader", MessageReader(self.messages_db)),
            patch.object(agictl_cli, "tasks_db", self.tasks_db),
            patch.object(agictl_cli, "get_config", return_value={"primary_user": {"uid": "primary"}}),
        ):
            result = CliRunner().invoke(
                agictl_cli.cli,
                ["message", "conversation-context", "vv-coa", "primary",
                 "--injection-mode", "relevant", "--agent-name", "coa"],
            )
        self.assertEqual(result.exit_code, 0, result.output)
        return result.output

    def test_project_memory_is_in_the_blob(self) -> None:
        blob = self._blob()
        self.assertIn("--- PROJECT MEMORY ---", blob)
        self.assertIn("[Project #7]", blob)
        self.assertIn("Phase: testing", blob)
        self.assertIn("Next: ship the report", blob)

    def test_connection_memory_and_profile_are_in_the_blob(self) -> None:
        blob = self._blob()
        self.assertIn("[PROFILE: Steve Marsh (CONNECTION)]", blob)
        self.assertIn("Language: af", blob)
        self.assertIn("[MEMORY for Steve Marsh]", blob)
        self.assertIn("Preferences: prefers short replies", blob)

    def test_name_comes_from_the_connections_table_not_the_message(self) -> None:
        blob = self._blob()
        self.assertIn("Steve Marsh", blob)
        self.assertNotIn("first cycle with the memory system", blob)

    def test_no_undefined_database_name_is_left_in_the_cli(self) -> None:
        with open(agictl_cli.__file__, encoding="utf-8") as fh:
            self.assertNotIn("_db_connect", fh.read())


class _RecordingConsole:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def print(self, message: str, **kwargs) -> None:
        if "stderr" in kwargs:
            raise AssertionError("stderr must be configured on Console, not print()")
        self.lines.append(str(message))


class TestMessageIdentityAliases(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tempdir.name, "messages.db")
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE messages (
                    id INTEGER PRIMARY KEY,
                    direction TEXT,
                    from_user_id TEXT,
                    to_user_id TEXT,
                    display_name TEXT,
                    message_id TEXT,
                    text TEXT,
                    original_text TEXT,
                    mode TEXT,
                    status TEXT,
                    created_at TEXT,
                    channel TEXT
                )
                """
            )
            conn.executemany(
                """
                INSERT INTO messages (
                    direction, from_user_id, to_user_id, display_name,
                    message_id, text, mode, status, created_at, channel
                ) VALUES ('received', ?, ?, ?, ?, ?, 'typed', 'unprocessed',
                          datetime('now'), ?)
                """,
                [
                    ("clerk", "coa", "Clerk", "internal-1", "internal", "internal"),
                    ("human", "vv-coa", "Human", "external-1", "external", "vv"),
                    ("human", "other-uid", "Human", "external-2", "other", "vv"),
                ],
            )

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _invoke(self, agent_uid: str):
        with (
            patch.object(agictl_cli, "messages_db", self.db_path),
            patch.object(
                agictl_cli,
                "get_config",
                return_value={"versavoice": {"sub_account_id": "vv-coa"}},
            ),
            patch.object(agictl_cli, "get_agent_name", return_value="coa"),
        ):
            return CliRunner().invoke(
                agictl_cli.cli,
                ["message", "get", agent_uid, "--unread"],
            )

    def _insert_outbound_streak(self, contact_uid: str, *, internal_reply: bool) -> None:
        with sqlite3.connect(self.db_path) as conn:
            for index in range(5):
                conn.execute(
                    """
                    INSERT INTO messages (
                        direction, from_user_id, to_user_id, display_name,
                        message_id, text, original_text, mode, status,
                        created_at, channel
                    ) VALUES (
                        'sent', 'vv-coa', ?, 'Primary User', ?, ?, ?,
                        'typed', 'sent', datetime('now', ?), 'vv'
                    )
                    """,
                    (
                        contact_uid,
                        f"outbound-{contact_uid}-{index}",
                        f"status {index}",
                        f"status {index}",
                        f"-{6 - index} minutes",
                    ),
                )
            if internal_reply:
                conn.execute(
                    """
                    INSERT INTO messages (
                        direction, from_user_id, to_user_id, display_name,
                        message_id, text, original_text, mode, status,
                        created_at, channel
                    ) VALUES (
                        'received', ?, 'coa', 'Primary User', ?, 'reply',
                        'reply', 'typed', 'unprocessed',
                        datetime('now', '-1 minute'), 'internal'
                    )
                    """,
                    (contact_uid, f"reply-{contact_uid}"),
                )

    def test_configured_uid_includes_internal_agent_name(self) -> None:
        result = self._invoke("vv-coa")
        self.assertEqual(result.exit_code, 0, result.output)
        message_ids = {row["message_id"] for row in json.loads(result.output)}
        self.assertEqual(message_ids, {"internal-1", "external-1"})

    def test_unrelated_uid_does_not_gain_current_agent_aliases(self) -> None:
        result = self._invoke("other-uid")
        self.assertEqual(result.exit_code, 0, result.output)
        message_ids = {row["message_id"] for row in json.loads(result.output)}
        self.assertEqual(message_ids, {"external-2"})

    def test_internal_primary_reply_resets_shared_outbound_streak(self) -> None:
        self._insert_outbound_streak("primary", internal_reply=True)
        streak = MessageReader(self.db_path).get_outbound_streak(
            "vv-coa",
            "primary",
            agent_name="coa",
        )
        self.assertEqual(streak["count"], 0)

    def test_unanswered_outbound_streak_remains_active(self) -> None:
        self._insert_outbound_streak("unanswered", internal_reply=False)
        streak = MessageReader(self.db_path).get_outbound_streak(
            "vv-coa",
            "unanswered",
            agent_name="coa",
        )
        self.assertEqual(streak["count"], 5)
        self.assertGreaterEqual(streak["latest_outbound_age_hours"], 0)

    def test_history_filter_uses_status_and_preserves_prior_messages(self) -> None:
        self._insert_outbound_streak("primary", internal_reply=True)
        history = MessageReader(self.db_path).get_contact_history(
            "vv-coa",
            "primary",
            exclude_unprocessed=True,
            agent_name="coa",
        )
        self.assertEqual(len(history), 5)
        self.assertTrue(all(row["direction"] == "sent" for row in history))

    def test_current_internal_reply_cannot_conflict_with_flood_warning(self) -> None:
        self._insert_outbound_streak("primary", internal_reply=True)
        reader = MessageReader(self.db_path)
        with (
            patch.object(agictl_cli, "message_reader", reader),
            patch.object(
                agictl_cli,
                "tasks_db",
                os.path.join(self.tempdir.name, "missing-tasks.db"),
            ),
            patch.object(
                agictl_cli,
                "get_config",
                return_value={"primary_user": {"uid": "primary"}},
            ),
        ):
            result = CliRunner().invoke(
                agictl_cli.cli,
                [
                    "message",
                    "conversation-context",
                    "vv-coa",
                    "primary",
                    "--injection-mode",
                    "relevant",
                    "--agent-name",
                    "coa",
                ],
            )

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("NEW MESSAGES (UNREAD — MUST RESPOND)", result.output)
        self.assertNotIn("OUTBOUND STREAK", result.output)
        self.assertNotIn("NO reply received", result.output)

    def test_outbound_streak_command_uses_identity_union(self) -> None:
        self._insert_outbound_streak("primary", internal_reply=True)
        with patch.object(agictl_cli, "message_reader", MessageReader(self.db_path)):
            result = CliRunner().invoke(
                agictl_cli.cli,
                [
                    "message",
                    "outbound-streak",
                    "vv-coa",
                    "primary",
                    "--agent-name",
                    "coa",
                ],
            )

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(json.loads(result.output)["count"], 0)


class TestMessageGetChronology(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tempdir.name, "messages.db")
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE messages (
                    id INTEGER PRIMARY KEY,
                    direction TEXT,
                    from_user_id TEXT,
                    to_user_id TEXT,
                    display_name TEXT,
                    message_id TEXT,
                    text TEXT,
                    original_text TEXT,
                    mode TEXT,
                    status TEXT,
                    created_at TEXT,
                    channel TEXT
                )
                """
            )
            conn.executemany(
                """
                INSERT INTO messages (
                    direction, from_user_id, to_user_id, display_name,
                    message_id, text, mode, status, created_at, channel
                ) VALUES (
                    'received', 'human', 'vv-coa', 'Primary User', ?, ?,
                    'typed', 'unprocessed', ?, 'vv'
                )
                """,
                [
                    ("msg-c", "third", "2026-08-31 12:00:30"),
                    ("msg-a", "first", "2026-08-31 12:00:10"),
                    ("msg-b", "second", "2026-08-31 12:00:20"),
                    ("msg-d", "fourth", "2026-08-31 12:00:40"),
                ],
            )

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _invoke(self, *extra: str):
        with (
            patch.object(agictl_cli, "messages_db", self.db_path),
            patch.object(
                agictl_cli,
                "get_config",
                return_value={"versavoice": {"sub_account_id": "vv-coa"}},
            ),
            patch.object(agictl_cli, "get_agent_name", return_value="coa"),
        ):
            return CliRunner().invoke(
                agictl_cli.cli,
                ["message", "get", "vv-coa", "--unread", *extra],
            )

    def test_unread_returns_oldest_to_newest(self) -> None:
        result = self._invoke()
        self.assertEqual(result.exit_code, 0, result.output)
        ids = [row["message_id"] for row in json.loads(result.output)]
        self.assertEqual(ids, ["msg-a", "msg-b", "msg-c", "msg-d"])

    def test_last_n_keeps_newest_window_in_chronological_order(self) -> None:
        result = self._invoke("--last-n-count", "2")
        self.assertEqual(result.exit_code, 0, result.output)
        ids = [row["message_id"] for row in json.loads(result.output)]
        self.assertEqual(ids, ["msg-c", "msg-d"])


class TestCycleCloseout(unittest.TestCase):
    def test_missing_awareness_warns_without_breaking_closeout(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            cycles_db = os.path.join(tempdir, "cycles.db")
            with sqlite3.connect(cycles_db) as conn:
                conn.execute(
                    """
                    CREATE TABLE cycles (
                        id TEXT PRIMARY KEY,
                        started_at TEXT,
                        session_start_ts TEXT,
                        last_awareness_ts TEXT,
                        ended_at TEXT,
                        summary TEXT
                    )
                    """
                )
                conn.execute(
                    """
                    INSERT INTO cycles (
                        id, started_at, session_start_ts, last_awareness_ts
                    ) VALUES (
                        'coa-1', datetime('now'), datetime('now'), NULL
                    )
                    """
                )

            standard = _RecordingConsole()
            errors = _RecordingConsole()
            missing_agents_db = os.path.join(tempdir, "missing-agents.db")
            with (
                patch.object(agictl_cli, "cycles_db", cycles_db),
                patch.object(agictl_cli, "console", standard),
                patch.object(agictl_cli, "error_console", errors),
                patch.dict(
                    os.environ,
                    {"AGICTL_AGENTS_DB": missing_agents_db},
                ),
            ):
                result = CliRunner().invoke(
                    agictl_cli.cli,
                    ["cycle", "end", "clean", "closeout", "--no-handoff", "--agent", "coa"],
                )

            self.assertEqual(result.exit_code, 0, result.output)
            self.assertTrue(any("Cycle ended: clean closeout" in line for line in standard.lines))
            self.assertTrue(any("AWARENESS NOT RECORDED" in line for line in errors.lines))
            with sqlite3.connect(cycles_db) as conn:
                ended_at, summary = conn.execute(
                    "SELECT ended_at, summary FROM cycles WHERE id='coa-1'"
                ).fetchone()
            self.assertIsNotNone(ended_at)
            self.assertEqual(summary, "clean closeout")


class TestLifelineFloodGuardWiring(unittest.TestCase):
    def test_hard_guard_uses_shared_identity_aware_streak_command(self) -> None:
        lifeline_path = os.path.join(CORE_INFRA, "lifeline.sh")
        with open(lifeline_path, encoding="utf-8") as handle:
            source = handle.read()
        section = source.split(
            "# ── Message Flood Guard", 1
        )[1].split("# ── Package Approval Notification", 1)[0]

        self.assertIn("message outbound-streak", section)
        self.assertIn('--agent-name "${AGENT_NAME}"', section)
        self.assertNotIn('sqlite3 "${MESSAGES_DB}"', section)


if __name__ == "__main__":
    unittest.main()
