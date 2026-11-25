"""The backup an update makes (state_host_services.md §1.8).

The real functions of scripts/update_backup.sh run in bash against temp folders.

Run:  python -m unittest harness.tests.test_update_backup   (from core-infra)
"""

import os
import re
import sqlite3
import subprocess
import tempfile
import unittest

CORE_INFRA = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SRC = os.path.dirname(CORE_INFRA)
SCRIPT = os.path.join(CORE_INFRA, "scripts", "update_backup.sh")


def _write(path, text="x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as out:
        out.write(text)


class BackupCase(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._cleanup)
        self.tmp = self._dir.name
        self.root = os.path.join(self.tmp, "var-backups")
        self.notice = os.path.join(self.tmp, "state", "update_backup_notice")
        self.legacy = os.path.join(self.tmp, "watchdog", "backups")
        self.code = os.path.join(self.tmp, "core-infra")
        self.data = os.path.join(self.tmp, "data")
        self.etc = os.path.join(self.tmp, "etc")
        self.home = os.path.join(self.tmp, "home-coa")
        _write(os.path.join(self.code, "lifeline.sh"), "code")
        _write(os.path.join(self.etc, "setup.ini"), "[agent]\n")
        _write(os.path.join(self.data, "coa", "cycles", "result_1.json"), "log")
        self.db = os.path.join(self.data, "coa", "tasks.db")
        with sqlite3.connect(self.db) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("CREATE TABLE tasks (id INTEGER, title TEXT)")
            conn.execute("INSERT INTO tasks VALUES (1, 'first')")
        _write(os.path.join(self.home, "coa-env", "workspace", "notes.md"), "notes")
        _write(os.path.join(self.home, "coa-env", "workspace", "proj", ".git", "HEAD"), "ref")
        _write(os.path.join(self.home, "coa-env", "workspace", "proj", "node_modules", "big.js"), "x" * 5000)
        _write(os.path.join(self.home, ".cache", "junk"), "x" * 5000)
        _write(os.path.join(self.home, "coa-env", "venv", "bin", "python"), "x" * 5000)
        _write(os.path.join(self.home, ".config", "rclone", "rclone.conf"), "[drive]")

    def _cleanup(self):
        subprocess.run(["chmod", "-R", "u+rwx", self.tmp], check=False)
        self._dir.cleanup()

    def run_backup(self, stamp, free=None, legacy=True, extra=""):
        env = dict(os.environ, VERSA_UPDATE_BACKUP_ROOT=self.root, VERSA_UPDATE_BACKUP_NOTICE=self.notice,
                   VERSA_UPDATE_BACKUP_MARGIN_BYTES="1000")
        if free is not None:
            env["VERSA_UPDATE_BACKUP_FREE_BYTES"] = str(free)
        script = (f'source "{SCRIPT}"\nupdate_backup_reset\n'
                  f'update_backup_add "{self.code}" core-infra plain\n'
                  f'update_backup_add "{self.data}" data data\n'
                  f'update_backup_add "{self.etc}" etc plain\n'
                  f'update_backup_add "{self.home}" homes/coa home\n'
                  f'update_backup_add "{self.tmp}/not-there" homes/ghost home\n'
                  f'{extra}\n'
                  f'update_backup_run "{stamp}" "{self.legacy if legacy else ""}"; rc=$?\n'
                  'echo "PATH=${UPDATE_BACKUP_PATH}"; echo "NOTE=${UPDATE_BACKUP_NOTE}"; exit $rc')
        done = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True)
        out = dict(line.split("=", 1) for line in done.stdout.splitlines() if "=" in line)
        return done.returncode, out.get("PATH", ""), out.get("NOTE", "")

    def backups(self):
        return sorted(name for name in os.listdir(self.root) if name.startswith("update-")) \
            if os.path.isdir(self.root) else []

    def notice_text(self):
        if not os.path.exists(self.notice):
            return ""
        with open(self.notice, encoding="utf-8") as handle:
            return handle.read()


class TestWhatIsBackedUp(BackupCase):
    def test_the_code_the_data_the_settings_and_the_home_are_all_there(self):
        rc, path, _ = self.run_backup("20261121T100000Z")
        self.assertEqual(rc, 0)
        self.assertEqual(path, os.path.join(self.root, "update-20261121T100000Z"))
        for rel in ("core-infra/lifeline.sh", "etc/setup.ini", "data/coa/cycles/result_1.json",
                    "data/coa/tasks.db", "homes/coa/coa-env/workspace/notes.md",
                    "homes/coa/.config/rclone/rclone.conf"):
            self.assertTrue(os.path.isfile(os.path.join(path, rel)), rel)
        self.assertFalse(os.path.exists(os.path.join(path, "homes", "ghost")))

    def test_rebuildable_folders_are_left_out_and_git_history_is_kept(self):
        _, path, _ = self.run_backup("20261121T100000Z")
        home = os.path.join(path, "homes", "coa")
        self.assertFalse(os.path.exists(os.path.join(home, ".cache")))
        self.assertFalse(os.path.exists(os.path.join(home, "coa-env", "venv")))
        self.assertFalse(os.path.exists(os.path.join(home, "coa-env", "workspace", "proj", "node_modules")))
        self.assertTrue(os.path.isfile(os.path.join(home, "coa-env", "workspace", "proj", ".git", "HEAD")))

    def test_a_database_is_copied_whole_while_it_is_open_for_writing(self):
        """A writer holds the database with rows only in its write-ahead log."""
        writer = sqlite3.connect(self.db)
        writer.execute("INSERT INTO tasks VALUES (2, 'still in the log')")
        writer.commit()
        try:
            _, path, _ = self.run_backup("20261121T100000Z")
        finally:
            writer.close()
        copy = os.path.join(path, "data", "coa", "tasks.db")
        # The copy is one whole file: the writer's log files are not carried over.
        self.assertEqual([n for n in os.listdir(os.path.dirname(copy)) if n.endswith(("-wal", "-shm"))], [])
        conn = sqlite3.connect(copy)
        try:
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual([r[0] for r in conn.execute("SELECT title FROM tasks ORDER BY id")],
                             ["first", "still in the log"])
        finally:
            conn.close()

    def test_the_folder_is_for_root_only(self):
        _, path, _ = self.run_backup("20261121T100000Z")
        self.assertEqual(oct(os.stat(self.root).st_mode & 0o777), "0o700")
        self.assertEqual(oct(os.stat(path).st_mode & 0o777), "0o700")

    def test_the_same_folders_are_left_out_as_in_the_full_backup(self):
        with open(os.path.join(SRC, "backup.sh"), encoding="utf-8") as handle:
            full = re.search(r'^GLOBAL_EXCLUDES="([^"]+)"', handle.read(), flags=re.M).group(1)
        with open(SCRIPT, encoding="utf-8") as handle:
            ours = re.search(r'^UPDATE_BACKUP_EXCLUDES="([^"]+)"', handle.read(), flags=re.M).group(1)
        self.assertEqual(ours, full)


class TestOneBackupIsKept(BackupCase):
    def test_each_update_replaces_the_previous_backup(self):
        self.run_backup("20261121T100000Z")
        _write(os.path.join(self.home, "coa-env", "workspace", "new.md"), "new")
        rc, path, _ = self.run_backup("20261122T100000Z")
        self.assertEqual(rc, 0)
        self.assertEqual(self.backups(), ["update-20261122T100000Z"])
        self.assertTrue(os.path.isfile(os.path.join(path, "homes", "coa", "coa-env", "workspace", "new.md")))
        self.assertTrue(os.path.isfile(os.path.join(path, "homes", "coa", "coa-env", "workspace", "notes.md")))

    def test_the_second_backup_needs_room_only_for_what_changed(self):
        def need(previous):
            env = dict(os.environ, VERSA_UPDATE_BACKUP_ROOT=self.root)
            script = (f'source "{SCRIPT}"; update_backup_reset; update_backup_add "{self.home}" homes/coa home; '
                      f'update_backup_need_bytes "{previous}"')
            return int(subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True).stdout.strip())
        first = need("")
        _, path, _ = self.run_backup("20261121T100000Z")
        self.assertGreater(first, 0)
        self.assertEqual(need(path), 0)
        _write(os.path.join(self.home, "coa-env", "workspace", "new.md"), "12345")
        self.assertEqual(need(path), 5)

    def test_a_copy_left_by_an_interrupted_update_is_not_a_backup(self):
        _write(os.path.join(self.root, ".incoming-20261120T000000Z", "core-infra", "half"), "x")
        self.run_backup("20261121T100000Z")
        self.assertEqual([n for n in os.listdir(self.root) if n.startswith(".incoming")], [])
        self.assertEqual(self.backups(), ["update-20261121T100000Z"])


class TestNotEnoughDisk(BackupCase):
    def test_no_backup_is_made_the_previous_one_stays_and_a_notice_is_left(self):
        self.run_backup("20261121T100000Z")
        rc, path, note = self.run_backup("20261122T100000Z", free=10)
        self.assertEqual(rc, 2)
        self.assertEqual(self.backups(), ["update-20261121T100000Z"])
        self.assertEqual(path, os.path.join(self.root, "update-20261121T100000Z"))
        self.assertIn("not enough free disk", note)
        text = self.notice_text()
        self.assertIn("no backup was made before the update", text)
        self.assertIn("The update went ahead.", text)
        self.assertIn("The earlier backup is still there", text)
        self.assertIn("was free", text)
        self.assertEqual([n for n in os.listdir(self.root) if n.startswith(".incoming")], [])

    def test_with_no_earlier_backup_the_notice_says_so(self):
        rc, path, _ = self.run_backup("20261121T100000Z", free=10, legacy=False)
        self.assertEqual((rc, path), (2, ""))
        self.assertIn("There is no earlier backup.", self.notice_text())

    def test_a_later_good_backup_clears_the_notice(self):
        self.run_backup("20261121T100000Z", free=10)
        self.assertTrue(self.notice_text())
        rc, _, _ = self.run_backup("20261122T100000Z")
        self.assertEqual(rc, 0)
        self.assertEqual(self.notice_text(), "")


class TestOldLocation(BackupCase):
    def _old(self, *names):
        for index, name in enumerate(names):
            _write(os.path.join(self.legacy, name, "core-infra", "f"), "old")
            os.utime(os.path.join(self.legacy, name), (1000 + index, 1000 + index))

    def test_it_is_emptied_once_the_new_backup_exists(self):
        self._old("20261101T000000Z", "20261110T000000Z", "20261119T000000Z")
        rc, _, _ = self.run_backup("20261121T100000Z")
        self.assertEqual(rc, 0)
        self.assertFalse(os.path.exists(self.legacy))

    def test_when_disk_is_short_its_older_backups_go_first_and_the_newest_stays(self):
        self._old("20261101T000000Z", "20261110T000000Z", "20261119T000000Z")
        rc, _, _ = self.run_backup("20261121T100000Z", free=10)
        self.assertEqual(rc, 2)
        self.assertEqual(os.listdir(self.legacy), ["20261119T000000Z"])
        self.assertIn(f"An earlier backup is still in {self.legacy}", self.notice_text())


class TestCopyFails(BackupCase):
    @unittest.skipIf(os.geteuid() == 0, "root can read any folder")
    def test_a_failed_copy_leaves_the_previous_backup_and_a_notice(self):
        self.run_backup("20261121T100000Z")
        locked = os.path.join(self.code, "locked")
        _write(os.path.join(locked, "f"), "x")
        os.chmod(locked, 0o000)
        rc, path, note = self.run_backup("20261122T100000Z")
        self.assertEqual(rc, 3)
        self.assertEqual(self.backups(), ["update-20261121T100000Z"])
        self.assertEqual(path, os.path.join(self.root, "update-20261121T100000Z"))
        self.assertIn("failed", note)
        self.assertIn("could not be completed", self.notice_text())
        self.assertEqual([n for n in os.listdir(self.root) if n.startswith(".incoming")], [])


class TestLifelineTellsThePrimaryUser(unittest.TestCase):
    def _send(self, sent_ok, text="⚠️ no backup was made"):
        with open(os.path.join(CORE_INFRA, "lifeline.sh"), encoding="utf-8") as handle:
            source = handle.read()
        start = source.index("# ─── Update backup notice")
        block = source[start:source.index("# ─── End update backup notice ───", start)]
        with tempfile.TemporaryDirectory() as tmp:
            notice, said = os.path.join(tmp, "notice"), os.path.join(tmp, "said")
            if text is not None:
                _write(notice, text + "\n")
            script = (f'notify_primary_user() {{ printf "%s|%s" "$1" "$2" > "{said}"; return {0 if sent_ok else 1}; }}\n'
                      + block + "\nsend_update_backup_notice\n")
            subprocess.run(["bash", "-c", script], env=dict(os.environ, VERSA_UPDATE_BACKUP_NOTICE=notice), check=True)
            told = ""
            if os.path.exists(said):
                with open(said, encoding="utf-8") as handle:
                    told = handle.read()
            return told, os.path.exists(notice)

    def test_the_notice_is_sent_once_and_removed(self):
        told, still_there = self._send(sent_ok=True)
        self.assertEqual(told, "update backup|⚠️ no backup was made")
        self.assertFalse(still_there)

    def test_a_notice_that_could_not_be_sent_waits_for_the_next_pass(self):
        told, still_there = self._send(sent_ok=False)
        self.assertTrue(told)
        self.assertTrue(still_there)

    def test_nothing_is_sent_when_there_is_no_notice(self):
        told, _ = self._send(sent_ok=True, text=None)
        self.assertEqual(told, "")

    def test_lifeline_sends_it_on_every_pass_before_the_agents(self):
        with open(os.path.join(CORE_INFRA, "lifeline.sh"), encoding="utf-8") as handle:
            source = handle.read()
        call = source.index("\nsend_update_backup_notice\n")
        self.assertLess(source.index("send_update_backup_notice() {"), call)
        self.assertLess(call, source.index("# ─── Process Each Agent"))


class TestSetupAndUninstall(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(SRC, "setup.sh"), encoding="utf-8") as handle:
            text = handle.read()
        start = text.index("# ─── U4: Pre-Deploy Backup")
        self.step = text[start:text.index("fi  # end UPDATE_MODE preamble", start)]
        self.setup = text

    def test_the_update_backs_up_code_data_settings_and_every_home(self):
        for line in ('update_backup_add "${DEPLOYED_CORE_INFRA}" "core-infra" plain',
                     'update_backup_add "/var/lib/versa-agi" "data" data',
                     'update_backup_add "/etc/versa-agi" "etc" plain',
                     'update_backup_add "/home/${COA_USER}" "homes/${COA_USER}" home',
                     'update_backup_add "/home/${_agent_user}" "homes/${_agent_user}" home'):
            self.assertIn(line, self.step)
        self.assertIn('update_backup_run "${TIMESTAMP}" "${LEGACY_BACKUP_DIR}" || _BACKUP_RC=$?', self.step)

    def test_a_backup_that_cannot_be_made_never_stops_the_update(self):
        self.assertNotIn("exit ", self.step)
        self.assertNotIn("error ", self.step)
        self.assertEqual(self.step.count("The update goes on"), 2)
        self.assertNotIn("${WATCHDOG_HOME}/backups/${TIMESTAMP}", self.setup)

    def test_the_rollback_hint_points_at_the_new_layout(self):
        self.assertIn("${BACKUP_PATH}/homes/${COA_USER}/coa-env/ ${DEPLOYED_COA_ENV}/", self.setup)
        self.assertNotIn("/home/watchdog/backups/latest", self.setup)

    def test_purge_removes_the_new_place_and_the_old_one(self):
        with open(os.path.join(SRC, "uninstall.sh"), encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn('for BACKUP_DIR in "/var/backups/versa-agi" "/home/${WATCHDOG_USER}/backups"; do', text)


if __name__ == "__main__":
    unittest.main()
