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

    def test_a_first_time_contact_gets_their_profile_and_memory(self) -> None:
        """SPWN-53: no earlier messages must not drop the profile and the memory."""
        with sqlite3.connect(self.messages_db) as conn:
            conn.execute(
                "INSERT INTO messages (direction, from_user_id, to_user_id, display_name, message_id, "
                "text, mode, status, created_at, channel) VALUES ('received', 'new-uid', 'vv-coa', "
                "'N.', 'm-9', 'bonjour', 'typed', 'unprocessed', datetime('now'), 'vv')"
            )
        with sqlite3.connect(self.tasks_db) as conn:
            conn.execute(
                "INSERT INTO connections (uid, display_name, spoken_lang, country) "
                "VALUES ('new-uid', 'Nadia Roux', 'fr', 'France')"
            )
            conn.execute(
                "INSERT INTO agent_memory_connection (agent_name, contact_uid, preferences) "
                "VALUES ('coa', 'new-uid', 'introduced by Steve')"
            )
        blob = self._blob()
        self.assertIn("[PROFILE: Nadia Roux (CONNECTION)]", blob)
        self.assertIn("Communicate in fr.", blob)
        self.assertIn("[MEMORY for Nadia Roux]", blob)
        self.assertIn("Preferences: introduced by Steve", blob)
        self.assertIn("[CONNECTION: Nadia Roux (new-uid) — no earlier messages]", blob)
        # The contact with history is unchanged, and the new mail still closes the blob.
        self.assertIn("[CONNECTION: Steve Marsh (steve-uid) — last 1 messages]", blob)
        self.assertLess(blob.index("no earlier messages"), blob.index("bonjour"))


class TestCountUnprocessedWindow(unittest.TestCase):
    """SPWN-54: `message count-unprocessed <sub_account> <seconds>` honours its time window."""

    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.messages_db = os.path.join(self.tempdir.name, "messages.db")
        scripts = os.path.join(CORE_INFRA, "scripts")
        os.system(f"bash {scripts}/init_messages_db.sh {self.messages_db} >/dev/null 2>&1")
        rows = (
            ("m-new", "vv-coa", "unprocessed", "datetime('now', '-30 seconds')"),
            ("m-old", "vv-coa", "unprocessed", "datetime('now', '-2 hours')"),
            ("m-iso", "vv-coa", "unprocessed", "strftime('%Y-%m-%dT%H:%M:%SZ', 'now', '-40 seconds')"),
            ("m-internal", "coa", "unprocessed", "datetime('now', '-50 seconds')"),
            ("m-done", "vv-coa", "processed", "datetime('now', '-10 seconds')"),
            ("m-other", "vv-web", "unprocessed", "datetime('now', '-10 seconds')"),
        )
        with sqlite3.connect(self.messages_db) as conn:
            for message_id, to_user, status, created in rows:
                conn.execute(
                    "INSERT INTO messages (direction, from_user_id, to_user_id, display_name, message_id, text, "
                    f"mode, status, created_at, channel) VALUES ('received', 'steve-uid', ?, 'S.', ?, 'hi', "
                    f"'typed', ?, {created}, 'vv')", (to_user, message_id, status))

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _count(self, *args):
        with patch.object(agictl_cli, "message_reader", MessageReader(self.messages_db)):
            return CliRunner().invoke(agictl_cli.cli, ["message", "count-unprocessed", *args])

    def test_zero_means_no_window_as_lifeline_calls_it(self) -> None:
        self.assertEqual(self._count("vv-coa", "0").output.strip(), "3")
        self.assertEqual(self._count("vv-coa", "0", "--agent-name", "coa").output.strip(), "4")

    def test_a_window_counts_only_what_arrived_inside_it(self) -> None:
        self.assertEqual(self._count("vv-coa", "300").output.strip(), "2")           # m-new, m-iso; not m-old
        self.assertEqual(self._count("vv-coa", "300", "--agent-name", "coa").output.strip(), "3")
        self.assertEqual(self._count("vv-coa", "35").output.strip(), "1")            # m-new only
        self.assertEqual(self._count("vv-coa", "10800").output.strip(), "3")         # three hours: all of them

    def test_an_iso_row_is_compared_as_a_time_not_as_text(self) -> None:
        # As text, '…T…Z' sorts after any same-day plain value, so it would count in every window.
        self.assertEqual(self._count("vv-coa", "35").output.strip(), "1")

    def test_a_negative_window_is_refused(self) -> None:
        result = self._count("vv-coa", "--", "-5")
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("0 (no window) or a number of seconds", result.output)

    def test_the_documents_say_what_zero_means(self) -> None:
        with open(os.path.join(CORE_INFRA, "skills", "cli_reference.md"), encoding="utf-8") as fh:
            self.assertIn("0 = no window", fh.read())


class TestDeleteLocalMessages(unittest.TestCase):
    """SPWN-55: COA deletes the local copies of messages for itself and its sub-agents:
    agent-to-agent ones and VersaVoice ones. VersaVoice itself is never called."""

    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        d = self.tempdir.name
        self.messages_db = os.path.join(d, "messages.db")
        self.agents_db = os.path.join(d, "agents.db")
        os.system(f"bash {os.path.join(CORE_INFRA, 'scripts')}/init_messages_db.sh {self.messages_db} >/dev/null 2>&1")
        with sqlite3.connect(self.agents_db) as conn:
            conn.execute("CREATE TABLE agents (name TEXT PRIMARY KEY)")
            conn.executemany("INSERT INTO agents (name) VALUES (?)", [("coa",), ("web-dev",), ("researcher",)])
        rows = (
            # id, direction, from, to, status, channel, created (UTC)
            ("int_a1", "sent", "coa", "web-dev", "sent", "internal", "2026-10-01 12:00:00"),
            ("int_a2", "received", "coa", "web-dev", "processed", "internal", "2026-10-01 12:00:00"),
            ("int_b1", "sent", "web-dev", "coa", "sent", "internal", "2026-11-10 12:00:00"),
            ("int_b2", "received", "web-dev", "coa", "processed", "internal", "2026-11-10 12:00:00"),
            ("int_c1", "received", "researcher", "coa", "unprocessed", "internal", "2026-11-12 12:00:00"),
            ("int_c0", "sent", "researcher", "coa", "sent", "internal", "2026-11-12 12:00:00"),
            ("int_d1", "received", "lifeline", "web-dev", "processed", "internal", "2026-11-12 13:00:00"),
            ("vv-1", "received", "steve-uid", "vv-coa", "processed", "vv", "2026-10-01 12:00:00"),
            ("vv-2", "sent", "vv-coa", "steve-uid", "delivered", "vv", "2026-10-01 12:05:00"),
            ("vv-3", "received", "pu-uid", "vv-coa", "unprocessed", "vv", "2026-11-12 09:00:00"),
            ("vv-4", "sent", "vv-coa", "pu-uid", "queued", "vv", "2026-11-12 09:05:00"),
            ("vv-5", "received", "pu-uid", "vv-web", "processed", "vv", "2026-11-12 09:10:00"),
        )
        self.vv_ids = {"coa": "vv-coa", "web-dev": "vv-web"}
        with sqlite3.connect(self.messages_db) as conn:
            for mid, direction, sender, to, status, channel, created in rows:
                conn.execute(
                    "INSERT INTO messages (direction, from_user_id, to_user_id, display_name, message_id, text, "
                    "mode, status, channel, created_at) VALUES (?, ?, ?, ?, ?, 'hi', 'typed', ?, ?, ?)",
                    (direction, sender, to, sender, mid, status, channel, created))

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _run(self, *args, caller="coa"):
        with (
            patch.dict(os.environ, {"AGICTL_AGENT_USER": caller, "TZ": "America/New_York"}, clear=False),
            patch.object(agictl_cli, "message_reader", MessageReader(self.messages_db)),
            patch.object(agictl_cli, "agents_db", self.agents_db),
            patch.object(agictl_cli, "_agent_vv_uid_map", return_value=self.vv_ids),
            patch("comms.api_request", side_effect=AssertionError("VersaVoice must not be called")),
        ):
            return CliRunner().invoke(agictl_cli.cli, ["message", "delete-local", *args])

    def _ids(self) -> set:
        with sqlite3.connect(self.messages_db) as conn:
            return {row[0] for row in conn.execute("SELECT message_id FROM messages")}

    def _reply(self, result) -> dict:
        return json.loads(result.output.strip().splitlines()[-1])

    ALL = {"int_a1", "int_a2", "int_b1", "int_b2", "int_c1", "int_c0", "int_d1", "vv-1", "vv-2", "vv-3", "vv-4", "vv-5"}

    def _tombstones(self) -> set:
        with sqlite3.connect(self.messages_db) as conn:
            return {row[0] for row in conn.execute("SELECT message_id FROM deleted_message_ids")}

    def test_coa_clears_its_own_messages_of_both_kinds_and_keeps_unread_and_unsent(self) -> None:
        reply = self._reply(self._run("--agent", "coa"))
        self.assertEqual(reply["deleted"], 4)
        self.assertEqual(reply["by_channel"], {"internal": 2, "vv": 2})
        self.assertEqual((reply["kept_unread"], reply["kept_unsent"]), (2, 1))
        self.assertEqual(reply["versavoice"], "not touched")
        # Gone: what COA sent and handled, between agents and with people. Kept: unread mail
        # (int_c1, vv-3), the message still queued to send (vv-4), and every other agent's copy.
        self.assertEqual(self._ids(), self.ALL - {"int_a1", "int_b2", "vv-1", "vv-2"})
        self.assertEqual(self._tombstones(), {"vv-1", "vv-2"})       # inbox sync cannot bring them back

    def test_coa_clears_a_sub_agents_messages(self) -> None:
        reply = self._reply(self._run("--agent", "web-dev"))
        self.assertEqual(reply["by_channel"], {"internal": 3, "vv": 1})
        self.assertEqual(self._ids(), self.ALL - {"int_a2", "int_b1", "int_d1", "vv-5"})

    def test_channel_narrows_to_one_kind(self) -> None:
        self.assertEqual(self._reply(self._run("--agent", "coa", "--channel", "internal"))["by_channel"], {"internal": 2})
        self.assertTrue({"vv-1", "vv-2"} <= self._ids())
        self.assertEqual(self._reply(self._run("--agent", "coa", "--channel", "vv"))["by_channel"], {"vv": 2})
        self.assertFalse({"vv-1", "vv-2"} & self._ids())

    def test_include_unread_takes_unread_rows_but_never_an_unsent_one(self) -> None:
        reply = self._reply(self._run("--agent", "coa", "--include-unread"))
        self.assertEqual((reply["deleted"], reply["kept_unread"], reply["kept_unsent"]), (6, 0, 1))
        self.assertFalse({"int_c1", "vv-3"} & self._ids())
        self.assertIn("vv-4", self._ids())

    def test_with_and_before_narrow_the_set(self) -> None:
        self.assertEqual(self._reply(self._run("--agent", "web-dev", "--with", "lifeline"))["deleted"], 1)
        self.assertNotIn("int_d1", self._ids())
        # One person's conversation only: the contact's uid.
        self.assertEqual(self._reply(self._run("--agent", "coa", "--with", "steve-uid"))["by_channel"], {"vv": 2})
        # 2026-11-01 00:00 local time: only the October exchange is older.
        self.assertEqual(self._reply(self._run("--agent", "web-dev", "--before", "2026-11-01 00:00"))["deleted"], 1)
        self.assertNotIn("int_a2", self._ids())
        self.assertIn("int_b1", self._ids())

    def test_dry_run_counts_by_kind_and_deletes_nothing(self) -> None:
        reply = self._reply(self._run("--agent", "coa", "--dry-run"))
        self.assertEqual((reply["action"], reply["deleted"]), ("would_delete", 4))
        self.assertEqual(reply["by_channel"], {"internal": 2, "vv": 2})
        self.assertEqual(self._ids(), self.ALL)
        self.assertEqual(self._tombstones(), set())

    def test_named_ids_of_either_kind(self) -> None:
        reply = self._reply(self._run("int_a1", "vv-1", "int_missing"))
        self.assertEqual((reply["deleted"], reply["not_found"]), (2, ["int_missing"]))
        self.assertFalse({"int_a1", "vv-1"} & self._ids())
        reply = self._reply(self._run("int_b1", "vv-2", "--channel", "internal"))
        self.assertEqual((reply["deleted"], reply["skipped_channel"]), (1, ["vv-2"]))
        self.assertIn("vv-2", self._ids())

    def test_a_sub_agent_caller_is_refused(self) -> None:
        result = self._run("--agent", "web-dev", caller="web-dev")
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("Permission denied", result.output)
        self.assertEqual(self._ids(), self.ALL)

    def _cloud_delete(self, *args, api_reply):
        calls = []

        def fake_api(endpoint, token, method="GET", **kwargs):
            calls.append((endpoint, method))
            return api_reply

        import comms
        with (
            patch.dict(os.environ, {"AGICTL_AGENT_USER": "coa"}, clear=False),
            patch.object(agictl_cli, "message_reader", MessageReader(self.messages_db)),
            patch.object(agictl_cli, "messages_db", self.messages_db),
            patch.object(agictl_cli, "get_config",
                         return_value={"versavoice": {"sub_account_id": "vv-coa", "api_token": "t"}}),
            patch.object(comms, "api_request", fake_api),
        ):
            result = CliRunner().invoke(agictl_cli.cli, ["message", "delete", *args])
        return result, calls

    def test_message_delete_removes_a_versavoice_message_from_the_cloud_and_the_local_copy(self) -> None:
        result, calls = self._cloud_delete("vv-1", "--channel", "c1", api_reply={"success": True})
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(calls[0][1], "DELETE")
        self.assertNotIn("vv-1", self._ids())                       # the local row really goes
        with sqlite3.connect(self.messages_db) as conn:             # and cannot be synced back in
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM deleted_message_ids WHERE message_id='vv-1'").fetchone()[0], 1)

    def test_message_delete_keeps_the_local_copy_when_the_cloud_refuses(self) -> None:
        result, _ = self._cloud_delete("vv-1", "--channel", "c1", api_reply={"success": False, "message": "nope"})
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("vv-1", self._ids())

    def test_message_delete_points_a_local_message_to_delete_local(self) -> None:
        result, calls = self._cloud_delete("int_a1", "--channel", "c1", api_reply={"success": True})
        self.assertNotEqual(result.exit_code, 0)
        self.assertEqual(calls, [])                                 # the cloud is not called for it
        self.assertIn("agictl message delete-local int_a1", result.output)
        self.assertIn("int_a1", self._ids())

    def test_an_unknown_agent_and_a_bad_call_are_refused(self) -> None:
        self.assertIn("not found in registry", self._run("--agent", "nobody").output)
        self.assertIn("one or the other", self._run().output)
        self.assertIn("one or the other", self._run("int_a1", "--agent", "coa").output)
        self.assertIn("--before", self._run("--agent", "coa", "--before", "tomorrow").output)
        self.assertEqual(self._ids(), self.ALL)


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
