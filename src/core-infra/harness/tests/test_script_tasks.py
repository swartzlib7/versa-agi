"""Unit tests for TD-SCRIPT-001 Script Tasks.

Covers the dependency-light pieces of the Script Task stack — the lifeline
due-selection helper (``utility_store.list_due_script_tasks``), the AGi-Tools
containment guard (``script_runner.contain_script_path``), and the deterministic
subprocess runner (``script_runner.run_script_task`` — return code, parameters,
timeout). The lifeline routing/alert command (``agictl task run-due-scripts``)
needs a Click context + VV alert side effects and is exercised manually, not here.

Run:  python -m unittest harness.tests.test_script_tasks   (from core-infra)
"""

import os
import sqlite3
import stat
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import script_runner  # noqa: E402
import utility_store  # noqa: E402
from script_runner import (  # noqa: E402
    ScriptRunError,
    contain_script_path,
    run_script_task,
)

# Minimal tasks schema — only the columns the script-task selection query reads.
_TASKS_SCHEMA = """
CREATE TABLE tasks (
  id                      INTEGER PRIMARY KEY AUTOINCREMENT,
  title                   TEXT,
  assigned_to             TEXT,
  status                  TEXT DEFAULT 'planned',
  due_date                DATETIME,
  task_kind               TEXT DEFAULT 'standard',
  script_path             TEXT,
  script_parameters       TEXT,
  script_interval_seconds INTEGER,
  utility_model_id        TEXT,
  utility_start_alert     INTEGER DEFAULT 0,
  utility_stop_alert      INTEGER DEFAULT 0,
  utility_spawn_agent     TEXT
);
"""


class TestDueScriptSelection(unittest.TestCase):
    """utility_store.list_due_script_tasks — the lifeline due gate."""

    def setUp(self):
        self._dir = tempfile.mkdtemp()
        self.db = os.path.join(self._dir, "tasks.db")
        conn = sqlite3.connect(self.db)
        conn.executescript(_TASKS_SCHEMA)
        conn.commit()
        conn.close()
        self._prev = os.environ.get("AGICTL_TASKS_DB")
        os.environ["AGICTL_TASKS_DB"] = self.db

    def tearDown(self):
        if self._prev is None:
            os.environ.pop("AGICTL_TASKS_DB", None)
        else:
            os.environ["AGICTL_TASKS_DB"] = self._prev

    def _insert(self, **cols):
        keys = ", ".join(cols)
        marks = ", ".join("?" for _ in cols)
        conn = sqlite3.connect(self.db)
        conn.execute(f"INSERT INTO tasks ({keys}) VALUES ({marks})", tuple(cols.values()))
        conn.commit()
        conn.close()

    def test_selects_only_due_planned_script_tasks(self):
        # Due once-off script task — selected.
        self._insert(
            title="once", assigned_to="coa", status="planned",
            due_date="2000-01-01 00:00:00", task_kind="script",
            script_path="sync.sh", script_interval_seconds=None,
        )
        # Due recurring script task — selected (carries its interval).
        self._insert(
            title="recurring", assigned_to="coa", status="planned",
            due_date="2000-01-02 00:00:00", task_kind="script",
            script_path="poll.sh", script_interval_seconds=3600,
        )
        # Future due date — excluded.
        self._insert(
            title="future", assigned_to="coa", status="planned",
            due_date="2999-01-01 00:00:00", task_kind="script", script_path="later.sh",
        )
        # Wrong assignee — excluded.
        self._insert(
            title="other", assigned_to="sylvie", status="planned",
            due_date="2000-01-01 00:00:00", task_kind="script", script_path="x.sh",
        )
        # Not planned — excluded.
        self._insert(
            title="done", assigned_to="coa", status="done",
            due_date="2000-01-01 00:00:00", task_kind="script", script_path="x.sh",
        )
        # Utility task (wrong kind) — excluded.
        self._insert(
            title="util", assigned_to="coa", status="planned",
            due_date="2000-01-01 00:00:00", task_kind="utility", utility_model_id="m1",
        )
        # Script kind but empty path — excluded.
        self._insert(
            title="nopath", assigned_to="coa", status="planned",
            due_date="2000-01-01 00:00:00", task_kind="script", script_path="  ",
        )

        rows = utility_store.list_due_script_tasks("coa")
        titles = [r["title"] for r in rows]
        self.assertEqual(titles, ["once", "recurring"])  # due-date ASC order
        self.assertIsNone(rows[0]["script_interval_seconds"])
        self.assertEqual(rows[1]["script_interval_seconds"], 3600)

    def test_missing_db_returns_empty(self):
        os.environ["AGICTL_TASKS_DB"] = os.path.join(self._dir, "nope.db")
        self.assertEqual(utility_store.list_due_script_tasks("coa"), [])


class TestContainment(unittest.TestCase):
    """script_runner.contain_script_path — AGi-Tools jail."""

    def setUp(self):
        self._dir = tempfile.mkdtemp()
        self.root = os.path.realpath(os.path.join(self._dir, "AGi-Tools"))
        os.makedirs(self.root)

    def _make(self, rel: str, body: str = "#!/bin/bash\nexit 0\n") -> str:
        p = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(body)
        return p

    def test_valid_script_resolves(self):
        self._make("sync.sh")
        real = contain_script_path("sync.sh", self.root)
        self.assertEqual(real, os.path.join(self.root, "sync.sh"))

    def test_empty_path_rejected(self):
        with self.assertRaises(ScriptRunError) as ctx:
            contain_script_path("  ", self.root)
        self.assertEqual(ctx.exception.code, "path_missing")

    def test_remote_url_rejected(self):
        with self.assertRaises(ScriptRunError) as ctx:
            contain_script_path("https://evil.test/x.sh", self.root)
        self.assertEqual(ctx.exception.code, "path_invalid")

    def test_traversal_escape_rejected(self):
        self._make("ok.sh")
        with self.assertRaises(ScriptRunError) as ctx:
            contain_script_path("../escape.sh", self.root)
        self.assertEqual(ctx.exception.code, "path_escape")

    def test_symlink_escape_rejected(self):
        outside = os.path.join(self._dir, "outside.sh")
        with open(outside, "w", encoding="utf-8") as f:
            f.write("#!/bin/bash\nexit 0\n")
        link = os.path.join(self.root, "link.sh")
        os.symlink(outside, link)
        with self.assertRaises(ScriptRunError) as ctx:
            contain_script_path("link.sh", self.root)
        self.assertEqual(ctx.exception.code, "path_escape")

    def test_missing_file_rejected(self):
        with self.assertRaises(ScriptRunError) as ctx:
            contain_script_path("ghost.sh", self.root)
        self.assertEqual(ctx.exception.code, "not_found")

    def test_non_shell_rejected(self):
        self._make("data.py")
        with self.assertRaises(ScriptRunError) as ctx:
            contain_script_path("data.py", self.root)
        self.assertEqual(ctx.exception.code, "not_shell")


class TestRunScriptTask(unittest.TestCase):
    """script_runner.run_script_task — deterministic subprocess + rc routing."""

    def setUp(self):
        self._dir = tempfile.mkdtemp()
        self.root = os.path.realpath(os.path.join(self._dir, "AGi-Tools"))
        os.makedirs(self.root)
        # Redirect the run-lock dir into the tempdir (no /var/lib write in tests).
        self._prev_lock = script_runner._RUN_LOCK_DIR
        script_runner._RUN_LOCK_DIR = os.path.join(self._dir, "script-runs")

    def tearDown(self):
        script_runner._RUN_LOCK_DIR = self._prev_lock

    def _script(self, name: str, body: str) -> str:
        p = os.path.join(self.root, name)
        with open(p, "w", encoding="utf-8") as f:
            f.write(body)
        os.chmod(p, os.stat(p).st_mode | stat.S_IEXEC)
        return name

    def test_success_returns_zero_and_stdout(self):
        self._script("ok.sh", "#!/bin/bash\necho hello-script\nexit 0\n")
        res = run_script_task(
            "ok.sh", agitools_root=self.root,
            max_runtime_seconds=30, output_tail_lines=10,
        )
        self.assertTrue(res["success"])
        self.assertEqual(res["returncode"], 0)
        self.assertIn("hello-script", res["stdout_tail"])
        self.assertFalse(res["timed_out"])

    def test_nonzero_exit_is_not_success(self):
        self._script("fail.sh", "#!/bin/bash\necho boom >&2\nexit 7\n")
        res = run_script_task(
            "fail.sh", agitools_root=self.root,
            max_runtime_seconds=30, output_tail_lines=10,
        )
        self.assertFalse(res["success"])
        self.assertEqual(res["returncode"], 7)
        self.assertIn("boom", res["stderr_tail"])

    def test_parameters_are_passed_as_argv(self):
        self._script("args.sh", "#!/bin/bash\necho \"$1:$2\"\n")
        res = run_script_task(
            "args.sh", agitools_root=self.root, parameters="alpha beta",
            max_runtime_seconds=30, output_tail_lines=10,
        )
        self.assertIn("alpha:beta", res["stdout_tail"])

    def test_timeout_reports_124(self):
        self._script("slow.sh", "#!/bin/bash\nsleep 5\n")
        res = run_script_task(
            "slow.sh", agitools_root=self.root,
            max_runtime_seconds=1, output_tail_lines=10,
        )
        self.assertTrue(res["timed_out"])
        self.assertEqual(res["returncode"], script_runner.TIMEOUT_RC)
        self.assertFalse(res["success"])

    def test_concurrent_lock_blocks_second_run(self):
        self._script("locked.sh", "#!/bin/bash\nexit 0\n")
        os.makedirs(script_runner._RUN_LOCK_DIR, exist_ok=True)
        # Pre-place the lock the runner would acquire for task 42.
        real = os.path.join(self.root, "locked.sh")
        lock = script_runner._acquire_run_lock(42, real)
        self.assertIsNotNone(lock)
        with self.assertRaises(ScriptRunError) as ctx:
            run_script_task(
                "locked.sh", agitools_root=self.root, task_id=42,
                max_runtime_seconds=30, output_tail_lines=10,
            )
        self.assertEqual(ctx.exception.code, "running")
        script_runner._release_run_lock(lock)


if __name__ == "__main__":
    unittest.main()
