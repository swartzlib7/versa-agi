"""Local time in and out of agictl and the agent prompts (state_time_display.md, TIME-02..06)."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

CORE_INFRA = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, CORE_INFRA)
sys.path.insert(0, os.path.join(CORE_INFRA, "agictl"))

try:
    from click.testing import CliRunner
    import agictl.cli as agictl_cli  # noqa: E402
except ImportError:  # the harness venv has no click; these run on the agictl interpreter
    raise unittest.SkipTest("click not installed in this interpreter")
import local_time  # noqa: E402
from agitop.data.agent_reader import AgentReader  # noqa: E402
from agitop.data.message_reader import MessageReader  # noqa: E402
from agitop.data.tasks_reader import TasksReader  # noqa: E402

SCRIPTS = os.path.join(CORE_INFRA, "scripts")


class _Dbs(unittest.TestCase):
    """Real schemas in a temp dir, machine zone America/New_York."""

    def setUp(self):
        self._tz = os.environ.get("TZ")
        os.environ["TZ"] = "America/New_York"
        time.tzset()
        self.tmp = tempfile.TemporaryDirectory()
        d = self.tmp.name
        self.tasks_db = os.path.join(d, "tasks.db")
        self.cycles_db = os.path.join(d, "cycles.db")
        self.messages_db = os.path.join(d, "messages.db")
        self.agents_db = os.path.join(d, "agents.db")
        for script, db in (("init_tasks_db.sh", self.tasks_db), ("init_cycles_db.sh", self.cycles_db),
                           ("init_messages_db.sh", self.messages_db)):
            os.system(f"bash {SCRIPTS}/{script} {db} >/dev/null 2>&1")
        with sqlite3.connect(self.agents_db) as conn:
            conn.execute("CREATE TABLE agents (name TEXT PRIMARY KEY, protected INTEGER DEFAULT 0)")
            conn.executemany("INSERT INTO agents (name) VALUES (?)", [("coa",), ("web-dev",)])
        with sqlite3.connect(self.tasks_db) as conn:
            conn.execute("INSERT INTO projects (id, name, workspace_path) VALUES (1, 'Atlas', '/tmp/atlas')")

    def tearDown(self):
        self.tmp.cleanup()
        if self._tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = self._tz
        time.tzset()

    def invoke(self, args, caller="web-dev"):
        with (
            patch.dict(os.environ, {"AGICTL_AGENT_USER": f"agi-{caller}", "VERSA_AGENT_NAME": caller}, clear=False),
            patch.object(agictl_cli, "tasks_db", self.tasks_db),
            patch.object(agictl_cli, "cycles_db", self.cycles_db),
            patch.object(agictl_cli, "agents_db", self.agents_db),
            patch.object(agictl_cli, "tasks_reader", TasksReader(self.tasks_db)),
            patch.object(agictl_cli, "agent_reader", AgentReader(self.agents_db, self.cycles_db, "", self.tasks_db)),
            patch.object(agictl_cli, "get_agent_name", return_value=caller),
            patch.object(agictl_cli, "_caller_agent_name", return_value=caller),
        ):
            return CliRunner().invoke(agictl_cli.cli, args)

    def stored(self, task_id, column="due_date"):
        with sqlite3.connect(self.tasks_db) as conn:
            return conn.execute(f"SELECT {column} FROM tasks WHERE id=?", (task_id,)).fetchone()[0]

    def add(self, due, title="Report"):
        return self.invoke(["task", "add", title, "--project", "1", "--due-date", due])


class TestTypedDueDate(_Dbs):
    """TIME-02: a typed time is local, stored in UTC, and anything else is refused."""

    def test_local_time_is_stored_in_utc_and_echoed_local(self):
        r = self.add("2026-11-13 11:30")
        self.assertEqual(r.exit_code, 0, r.output)
        out = json.loads(r.output)
        self.assertEqual(out["due_date"], "2026-11-13 11:30 EST")
        self.assertEqual(self.stored(out["task_id"]), "2026-11-13 16:30:00")

    def test_the_task_fires_at_the_local_time_not_five_hours_early(self):
        # F5: the due test compares stored text with UTC now. 11:30 EST must not be due at 11:31 UTC.
        task_id = json.loads(self.add("2026-11-13 11:30").output)["task_id"]
        with sqlite3.connect(self.tasks_db) as conn:
            due_at = lambda now: conn.execute(
                "SELECT COUNT(*) FROM tasks WHERE id=? AND due_date <= ?", (task_id, now)).fetchone()[0]
            self.assertEqual(due_at("2026-11-13 11:31:00"), 0)      # 06:31 EST
            self.assertEqual(due_at("2026-11-13 16:29:00"), 0)      # 11:29 EST
            self.assertEqual(due_at("2026-11-13 16:30:00"), 1)      # 11:30 EST

    def test_a_summer_date_typed_in_winter_gets_the_summer_offset(self):
        task_id = json.loads(self.add("2027-07-01 09:00").output)["task_id"]
        self.assertEqual(self.stored(task_id), "2027-07-01 13:00:00")

    def test_explicit_zone_and_now(self):
        self.assertEqual(self.stored(json.loads(self.add("2026-11-13T16:30:00Z").output)["task_id"]),
                         "2026-11-13 16:30:00")
        before = local_time.to_store(local_time.utc_now())
        stored = self.stored(json.loads(self.add("now").output)["task_id"])
        self.assertRegex(stored, r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")
        self.assertGreaterEqual(stored, before)

    def test_what_is_not_a_date_is_refused_and_nothing_is_stored(self):
        for bad in ("tomorrow", "in 2 hours", "11:30", "2026-11-13 11:30 PST"):
            r = self.add(bad)
            self.assertEqual(r.exit_code, 1, bad)
            self.assertIn("--due-date", json.loads(r.output)["error"])
        with sqlite3.connect(self.tasks_db) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0], 0)

    def test_update_parses_the_same_way_and_says_what_it_understood(self):
        task_id = json.loads(self.add("2026-11-13 11:30").output)["task_id"]
        r = self.invoke(["task", "update", str(task_id), "--due-date", "2026-11-14 08:00"])
        self.assertEqual(r.exit_code, 0, r.output)
        self.assertEqual(json.loads(r.output)["due_date"], "2026-11-14 08:00 EST")
        self.assertEqual(self.stored(task_id), "2026-11-14 13:00:00")
        r = self.invoke(["task", "update", str(task_id), "--due-date", "next week"])
        self.assertEqual(r.exit_code, 1)
        self.assertEqual(self.stored(task_id), "2026-11-14 13:00:00")

    def test_a_shown_due_date_can_be_typed_straight_back(self):
        task_id = json.loads(self.add("2026-11-13 11:30").output)["task_id"]
        shown = json.loads(self.invoke(["task", "get", str(task_id)]).output)["due_date"]
        self.invoke(["task", "update", str(task_id), "--due-date", shown])
        self.assertEqual(self.stored(task_id), "2026-11-13 16:30:00")


class TestOutputIsLocal(_Dbs):
    """TIME-04: the time fields themselves are local, with the zone."""

    def _task(self):
        with sqlite3.connect(self.tasks_db) as conn:
            conn.execute(
                "INSERT INTO tasks (id, title, status, priority, assigned_to, project_id, due_date, created_at, updated_at) "
                "VALUES (5, 'Report', 'in_progress', 'normal', 'web-dev', 1, '2026-11-13 16:30:00', "
                "'2026-11-13 02:10:00', '2026-11-13 02:10:00')")
            conn.execute("INSERT INTO task_progress (task_id, agent_name, note, created_at) "
                         "VALUES (5, 'web-dev', 'DONE: draft', '2026-11-13 16:45:00')")

    def test_task_list_get_and_progress(self):
        self._task()
        listed = json.loads(self.invoke(["task", "list", "--all"]).output)
        self.assertEqual(listed[0]["due_date"], "2026-11-13 11:30 EST")
        self.assertEqual(listed[0]["created_at"], "2026-11-12 21:10 EST")
        got = json.loads(self.invoke(["task", "get", "5"]).output)
        self.assertEqual(got["due_date"], "2026-11-13 11:30 EST")
        self.assertEqual(got["recent_progress"][0]["created_at"], "2026-11-13 11:45 EST")
        journal = json.loads(self.invoke(["task", "progress", "5"]).output)
        self.assertEqual(journal[0]["created_at"], "2026-11-13 11:45 EST")
        self.assertEqual(self.stored(5), "2026-11-13 16:30:00")          # storage untouched

    def test_cycle_get_is_local_and_keeps_its_shape(self):
        with sqlite3.connect(self.cycles_db) as conn:
            conn.execute("INSERT INTO cycles (id, started_at, ended_at, handoff) "
                         "VALUES ('web-dev-100', '2026-11-13 16:30:00', '2026-11-13 17:00:00', 'Open: X')")
        out = json.loads(self.invoke(["cycle", "get", "web-dev-100"]).output)
        self.assertEqual(out["cycle"]["started_at"], "2026-11-13 11:30 EST")
        self.assertEqual(out["cycle"]["handoff"], "Open: X")

    def test_frames_are_local_for_people_and_stored_utc_for_the_harness(self):
        with sqlite3.connect(self.cycles_db) as conn:
            conn.execute(
                "INSERT INTO frames (agent_name, thread_id, status, frame_start, frame_end, summary, message_ids) "
                "VALUES ('web-dev', '7-1', 'active', '2026-11-13T16:30:00Z', '2026-11-13T16:50:00Z', 's', 'm0')")
        shown = json.loads(self.invoke(["cycle", "frames", "list", "--thread", "7-1"]).output)
        self.assertEqual(shown[0]["frame_start"], "2026-11-13 11:30 EST")
        raw = json.loads(self.invoke(["cycle", "frames", "list", "--thread", "7-1", "--harness"]).output)
        self.assertEqual(raw[0]["frame_start"], "2026-11-13T16:30:00Z")
        self.assertEqual(raw[0]["frame_end"], "2026-11-13T16:50:00Z")

    def test_no_listed_command_prints_a_row_without_converting_it(self):
        with open(agictl_cli.__file__, encoding="utf-8") as fh:
            src = fh.read()
        for name in ("agent_list", "agent_show", "task_list", "task_get", "task_progress", "message_get",
                     "frames_search", "project_list", "project_members", "game_show", "game_list",
                     "opponent_list", "awareness_list", "awareness_get", "connection_list_agent",
                     "memory_connection_get", "memory_connection_list", "memory_project_get",
                     "memory_project_list", "memory_system_get", "memory_system_list"):
            start = src.index(f"def {name}(")
            body = src[start:src.index("\n@", start)]
            raw = [ln for ln in body.split("\n") if "json.dumps(" in ln and "indent=2, default=str" in ln
                   and "local_time.localize(" not in ln]
            self.assertEqual(raw, [], name)


class TestPromptBlocks(_Dbs):
    """TIME-03: one zone in a prompt."""

    def test_conversation_history_and_new_messages_are_local(self):
        with sqlite3.connect(self.messages_db) as conn:
            for mid, status, when in (("m-0", "processed", "2026-11-13 02:10:00"), ("m-1", "unprocessed", "2026-11-13 16:30:00")):
                conn.execute(
                    "INSERT INTO messages (direction, from_user_id, to_user_id, display_name, message_id, text, "
                    "mode, status, created_at, channel) VALUES ('received', 'steve', 'vv-coa', 'Steve', ?, 'hi', "
                    "'typed', ?, ?, 'vv')", (mid, status, when))
        with (patch.object(agictl_cli, "message_reader", MessageReader(self.messages_db)),
              patch.object(agictl_cli, "tasks_db", self.tasks_db),
              patch.object(agictl_cli, "get_config", return_value={"primary_user": {"uid": "primary"}})):
            blob = CliRunner().invoke(agictl_cli.cli, ["message", "conversation-context", "vv-coa", "primary",
                                                       "--agent-name", "coa"]).output
        self.assertIn("[2026-11-12 21:10 EST] THEM: hi", blob)
        self.assertIn("[2026-11-13 11:30 EST] FROM Steve", blob)
        self.assertNotIn("16:30", blob)
        self.assertNotIn("02:10", blob)

    def test_mid_cycle_mail_is_local(self):
        with open(os.path.join(CORE_INFRA, "harness", "midcycle_mail.py"), encoding="utf-8") as fh:
            self.assertIn('dt = local_time.show(msg.get("created_at") or "")', fh.read())

    def test_frames_block_and_last_call_are_local(self):
        with open(os.path.join(CORE_INFRA, "harness", "agent_harness.py"), encoding="utf-8") as fh:
            src = fh.read()
        self.assertIn("f\"Frame {local_time.show(row.get('frame_start'))} to \"", src)
        with open(os.path.join(CORE_INFRA, "harness", "live_call.py"), encoding="utf-8") as fh:
            call = fh.read()
        self.assertIn('when = local_time.show(row.get("created_at") or "")', call)
        self.assertNotIn('{when} UTC (', call)


class TestLifelinePromptSql(unittest.TestCase):
    """TIME-03, TIME-06: Lifeline's own prompt queries and the one instruction about zones."""

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(CORE_INFRA, "lifeline.sh"), encoding="utf-8") as fh:
            cls.text = fh.read()

    def test_no_prompt_block_is_labelled_utc_or_left_unlabelled(self):
        self.assertNotIn("Timestamp (UTC)", self.text)
        for label in ("| Due (local time) | Script", "| Priority | Due (local time)", "| Time (local) | Author",
                      "Last assessed (local time):", "local time)\""):
            self.assertIn(label, self.text)

    def test_the_task_and_journal_queries_convert_in_sql(self):
        for fragment in ("strftime('%Y-%m-%d %H:%M', due_date, 'localtime')",
                         "strftime('%Y-%m-%d %H:%M', p.created_at, 'localtime')",
                         "strftime('%Y-%m-%d %H:%M', environment_assessed_at, 'localtime')",
                         "strftime('%Y-%m-%d %H:%M', resolved_at, 'localtime')"):
            self.assertIn(fragment, self.text)
        # The wake decision itself still compares UTC with UTC.
        self.assertIn("due_date <= datetime('now')", self.text)

    def test_the_active_task_query_gives_local_time_and_keeps_odd_values(self):
        start = self.text.index("\"SELECT '#' || id, title, status, priority, COALESCE(strftime(")
        query = self.text[start + 1:self.text.index(" FROM tasks WHERE assigned_to", start)] + " FROM tasks ORDER BY id"
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "t.db")
            with sqlite3.connect(db) as conn:
                conn.execute("CREATE TABLE tasks (id INTEGER, title TEXT, status TEXT, priority TEXT, due_date TEXT)")
                conn.executemany("INSERT INTO tasks VALUES (?, 't', 'planned', 'normal', ?)",
                                 [(1, "2026-11-13 16:30:00"), (2, "2026-07-04 16:30:00"), (3, "tomorrow"), (4, None)])
            out = subprocess.run(["sqlite3", "-separator", " | ", db, query], capture_output=True, text=True,
                                 env=dict(os.environ, TZ="America/New_York"))
            self.assertEqual(out.returncode, 0, out.stderr)
            dues = [line.split(" | ")[-1] for line in out.stdout.strip().split("\n")]
            self.assertEqual(dues, ["2026-11-13 11:30", "2026-07-04 12:30", "tomorrow", "no due date"])

    def test_agents_are_told_one_thing_about_zones(self):
        self.assertIn("Every time you are shown is in this zone", self.text)
        self.assertIn("Do not convert to UTC and do not add an offset", self.text)
        self.assertNotIn("NEVER assume UTC", self.text)
        skills = os.path.join(CORE_INFRA, "skills")
        with open(os.path.join(skills, "task_scheduling.md"), encoding="utf-8") as fh:
            scheduling = fh.read()
        self.assertNotIn("date -u", scheduling)
        self.assertIn("local time", scheduling)

    def test_ide_times_are_local(self):
        self.assertNotIn("Seed generated $(date -u", self.text)
        self.assertIn("date -d \"${_ide_on}\" '+%Y-%m-%d %H:%M %Z'", self.text)


class TestDashboard(unittest.TestCase):
    """TIME-05: agitop shows local time everywhere and stores typed times correctly (source checks;
    the conversion itself is covered by test_local_time)."""

    def _read(self, *rel):
        with open(os.path.join(CORE_INFRA, "agitop", *rel), encoding="utf-8") as fh:
            return fh.read()

    def test_one_converter_instead_of_six_copies(self):
        for name in ("tasks.py", "messages.py", "agents.py", "projects.py", "agent_memory_modal.py", "strategy_modal.py"):
            src = self._read("panels", name)
            body = src[src.index("def _utc_to_local("):]
            body = body[:body.index("\n\n\n")]
            self.assertIn("local_time.show(", body, name)
            self.assertNotIn("strptime", body, name)
            self.assertNotIn(".astimezone()", body, name)

    def test_task_editor_uses_the_typed_dates_own_offset(self):
        src = self._read("panels", "tasks.py")
        body = src[src.index("def _local_to_utc("):]
        body = body[:body.index("\n\n\n")]
        self.assertIn("local_time.local_to_store(local_str)", body)
        self.assertNotIn("datetime.now().astimezone().tzinfo", src)       # F8: today's offset for any date

    def test_raw_fields_are_converted(self):
        self.assertIn('_utc_to_local(str(m.get("assigned_at") or ""))', self._read("panels", "projects.py"))
        settings = self._read("panels", "system_settings_modal.py")
        self.assertIn('local_time.show(r["updated_at"], missing="--")', settings)
        self.assertIn('local_time.show(r.get("requested_at", ""), missing="—")', settings)
        self.assertIn('ts = local_time.show(rec.get("ts", ""), seconds=True)', settings)
        menu = self._read("panels", "agent_prompt_menu.py")
        self.assertIn('local_time.show(row.get("frame_start"))', menu)
        self.assertIn("local_times_in_text(status_msg)", menu)
        self.assertIn("return local_time.show_date(value)", self._read("panels", "organization.py"))
        self.assertIn('local_time.show_date(conn.get("first_seen") or "")', self._read("panels", "org_record_modal.py"))

    def test_token_totals_use_the_local_month(self):
        reader = self._read("data", "agent_reader.py")
        self.assertNotIn("strftime('%Y-%m-01', 'now')\"", reader)
        self.assertEqual(reader.count("datetime(strftime('%Y-%m-01', 'now', 'localtime'), 'utc')"), 3)
        with open(os.path.join(CORE_INFRA, "lifeline.sh"), encoding="utf-8") as fh:
            self.assertIn("started_at >= datetime(strftime('%Y-%m-01', 'now', 'localtime'), 'utc')", fh.read())

    def test_the_local_month_start_in_sql(self):
        # 2026-11-30 21:00 EST is 2026-12-01 02:00 UTC: still November locally.
        out = subprocess.run(
            ["sqlite3", ":memory:",
             "SELECT datetime(strftime('%Y-%m-01', '2026-12-01 02:00:00', 'localtime'), 'utc'), "
             "datetime(strftime('%Y-%m-01', '2026-12-01 06:00:00', 'localtime'), 'utc');"],
            capture_output=True, text=True, env=dict(os.environ, TZ="America/New_York"))
        self.assertEqual(out.stdout.strip(), "2026-11-01 04:00:00|2026-12-01 05:00:00")

    def test_setup_ships_the_converter_with_the_shared_modules(self):
        with open(os.path.join(CORE_INFRA, "..", "setup.sh"), encoding="utf-8") as fh:
            setup = fh.read()
        shared = setup[setup.index("_VERSA_LIB_SHARED_PY=("):]
        self.assertIn("  local_time.py\n", shared[:shared.index(")")])


if __name__ == "__main__":
    unittest.main()


class TestOrganizationRows(unittest.TestCase):
    """TIME-09: `agictl organization … get / list` shows its two time fields in local time and
    leaves a record's calendar dates alone."""

    def setUp(self):
        from agictl import organization_cli
        self.organization_cli = organization_cli
        self._tz = os.environ.get("TZ")
        os.environ["TZ"] = "America/New_York"
        time.tzset()
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "organization.db")
        os.system(f"bash {SCRIPTS}/init_organization_db.sh {self.db} >/dev/null 2>&1")
        self._prev = os.environ.get("AGICTL_ORGANIZATION_DB")
        os.environ["AGICTL_ORGANIZATION_DB"] = self.db
        with sqlite3.connect(self.db) as conn:
            conn.execute("INSERT INTO organizations (id, name, created_at, updated_at) "
                         "VALUES (1, 'Acme', '2026-11-13 02:10:00', '2026-11-13 16:30:00')")
            conn.execute("INSERT INTO organizations (id, name, created_at, updated_at) "
                         "VALUES (2, 'Client', '2026-07-01 14:00:00', '2026-07-01 14:00:00')")
            # A due date that carries a time must still be left alone: it is a calendar date here.
            conn.execute("INSERT INTO invoices (id, org_id, customer_org_id, invoice_number, status, issue_date, "
                         "due_date, created_at, updated_at) VALUES (1, 1, 2, 'INV-1', 'draft', '2026-11-13', "
                         "'2026-12-13 00:00:00', '2026-11-13 16:30:00', '2026-11-13 16:30:00')")

    def tearDown(self):
        self.tmp.cleanup()
        for name, value in (("TZ", self._tz), ("AGICTL_ORGANIZATION_DB", self._prev)):
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        time.tzset()

    def run_cli(self, *args):
        import click

        def json_response(success, **kw):
            print(json.dumps({"success": success, **kw}))
            return success

        @click.group()
        def root():
            pass

        self.organization_cli.register(root, json_response=json_response)
        result = CliRunner().invoke(root, ["organization", *args], catch_exceptions=False)
        return json.loads(result.output.strip().splitlines()[-1])

    def test_get_and_list_show_the_two_time_fields_local(self):
        row = self.run_cli("org", "get", "1")["row"]
        self.assertEqual(row["created_at"], "2026-11-12 21:10 EST")
        self.assertEqual(row["updated_at"], "2026-11-13 11:30 EST")
        rows = {r["id"]: r for r in self.run_cli("org", "list")["rows"]}
        self.assertEqual(rows[1]["updated_at"], "2026-11-13 11:30 EST")
        self.assertEqual(rows[2]["created_at"], "2026-07-01 10:00 EDT")        # that date's own offset

    def test_calendar_dates_of_a_record_are_never_shifted(self):
        row = self.run_cli("invoice", "get", "1")["row"]
        self.assertEqual(row["issue_date"], "2026-11-13")
        self.assertEqual(row["due_date"], "2026-12-13 00:00:00")               # not 2026-12-12 19:00 EST
        self.assertEqual(row["created_at"], "2026-11-13 11:30 EST")

    def test_stored_prints_the_row_as_stored(self):
        self.assertEqual(self.run_cli("org", "get", "1", "--stored")["row"]["updated_at"], "2026-11-13 16:30:00")
        rows = self.run_cli("org", "list", "--stored")["rows"]
        self.assertEqual({r["created_at"] for r in rows}, {"2026-11-13 02:10:00", "2026-07-01 14:00:00"})

    def test_nothing_stored_changes(self):
        self.run_cli("org", "list")
        with sqlite3.connect(self.db) as conn:
            self.assertEqual(conn.execute("SELECT updated_at FROM organizations WHERE id=1").fetchone()[0],
                             "2026-11-13 16:30:00")


class _Column:
    def __init__(self, label):
        from rich.text import Text
        self.label = Text(label)


class _Table:
    def __init__(self, *labels):
        self.columns = {i: _Column(label) for i, label in enumerate(labels)}
        self.refreshed = 0

    def refresh(self):
        self.refreshed += 1


class TestZoneHeadings(unittest.TestCase):
    """TIME-10: a heading that names the zone follows the clock while agitop is open."""

    def setUp(self):
        from agitop import zone_heading
        self.zone_heading = zone_heading

    def test_a_heading_is_read_when_drawn(self):
        with patch.object(local_time, "zone_label", return_value="EST"):
            self.assertEqual(self.zone_heading.heading("Due"), "Due (EST)")
        with patch.object(local_time, "zone_label", return_value="EDT"):
            self.assertEqual(self.zone_heading.heading("Due"), "Due (EDT)")

    def test_refresh_renames_the_column_after_a_clock_change_and_only_that_column(self):
        table = _Table("Title", "Due (EST)", "Status")
        with patch.object(local_time, "zone_label", return_value="EST"):
            self.assertFalse(self.zone_heading.refresh(table, "Due"))           # nothing changed
        self.assertEqual(table.refreshed, 0)
        with patch.object(local_time, "zone_label", return_value="EDT"):
            self.assertTrue(self.zone_heading.refresh(table, "Due"))
            self.assertFalse(self.zone_heading.refresh(table, "Due"))           # once is enough
        self.assertEqual([c.label.plain for c in table.columns.values()], ["Title", "Due (EDT)", "Status"])
        self.assertEqual(table.refreshed, 1)
        self.assertFalse(self.zone_heading.refresh(_Table("Title"), "Due"))     # no such column

    def test_no_panel_reads_the_zone_name_once_at_start_up(self):
        panels = os.path.join(CORE_INFRA, "agitop", "panels")
        for name in sorted(os.listdir(panels)):
            if not name.endswith(".py"):
                continue
            with open(os.path.join(panels, name), encoding="utf-8") as fh:
                src = fh.read()
            self.assertNotRegex(src, r'(?m)^_TZ\s*=', name)
            self.assertNotIn('strftime("%Z")', src.replace('.astimezone().strftime("%Z")', ""), name)

    def test_the_tables_that_stay_on_screen_check_their_heading_on_refresh(self):
        panels = os.path.join(CORE_INFRA, "agitop", "panels")
        for name, call in (("agents.py", 'zone_heading.refresh(self, "Last Cycle")'),
                           ("messages.py", 'zone_heading.refresh(self.table, "Date/Time")'),
                           ("tasks.py", 'zone_heading.refresh(self.table, "Due")')):
            with open(os.path.join(panels, name), encoding="utf-8") as fh:
                src = fh.read()
            body = src[src.index("    def refresh_data(self)"):]
            self.assertIn(call, body[:600], name)

    def test_a_real_table_on_screen_shows_the_new_heading(self):
        try:
            from textual.app import App
            from textual.geometry import Region
            from textual.widgets import DataTable
        except ImportError:
            self.skipTest("textual not installed in this interpreter")
        import asyncio
        zone_heading = self.zone_heading

        class Demo(App):
            def compose(self):
                yield DataTable()

            def on_mount(self):
                with patch.object(local_time, "zone_label", return_value="EST"):
                    table = self.query_one(DataTable)
                    table.add_columns("Title", zone_heading.heading("Due"))
                    table.add_row("Report", "2026-11-13 11:30:00")

        def drawn(app):
            return app.query_one(DataTable).render_lines(Region(0, 0, 60, 1))[0].text

        async def scenario():
            app = Demo()
            async with app.run_test(size=(80, 24)) as pilot:
                await pilot.pause()
                before = drawn(app)
                with patch.object(local_time, "zone_label", return_value="EDT"):
                    zone_heading.refresh(app.query_one(DataTable), "Due")
                await pilot.pause()
                return before, drawn(app)

        before, after = asyncio.run(scenario())
        self.assertIn("Due (EST)", before)
        self.assertIn("Due (EDT)", after)
        self.assertNotIn("Due (EST)", after)
