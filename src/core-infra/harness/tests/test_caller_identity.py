"""Who is calling agictl, and what the sudo rules let through
(state_agent_account_permissions.md §1, PERM-A1..A3, A7, A8).

The caller is taken from SUDO_USER, which sudo sets. The wrapper passes
nothing on the sudo command line, the rule for agents carries no SETENV, and
watchdog reaches root for a short list of commands only. The setup block and
the wrapper are run as the real bash text, with stand-ins for the commands
that need root.
"""

from __future__ import annotations

import os
import re
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import unittest

CORE_INFRA = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SRC = os.path.dirname(CORE_INFRA)
sys.path.insert(0, CORE_INFRA)

import caller_identity  # noqa: E402

KEPT = [
    "AGICTL_AGENT_USER", "AGICTL_AGENT_DIR", "AGICTL_CONFIG", "VERSA_AGENT_NAME",
    "VERSA_CYCLE_ID", "VERSA_COA_AUTONOMOUS", "VERSA_CALL_BRIDGE",
    "VERSA_LISTENING", "VERSA_LISTEN_MINUTES",
]


def _read(*parts: str) -> str:
    with open(os.path.join(*parts), encoding="utf-8") as fh:
        return fh.read()


def _agents(os_user: str, _db: str):
    table = {
        "coa": ("coa", "/home/coa/coa-env"),
        "agi-clerk": ("clerk", "/home/agi-clerk"),
        "agi-web-dev": ("web-dev", "/home/agi-web-dev"),
    }
    return table.get(os_user)


def _settle(env: dict, *, euid=1001, running_as="watchdog", args=("task", "list")):
    refusal = caller_identity.settle(
        env, euid=euid, running_as=running_as, args=list(args),
        watchdog="watchdog", lookup=_agents,
    )
    return refusal, env


class AgentCaller(unittest.TestCase):
    """An agent's account, arriving through sudo as watchdog."""

    def test_inner_binary_called_with_no_stamp_is_still_the_agent(self):
        # The hole: `sudo -u watchdog /usr/local/lib/versa-agi/agictl …` skips
        # the wrapper, so nothing named the caller and guards saw the owner.
        _, env = _settle({"SUDO_USER": "agi-clerk"})
        self.assertEqual(env["AGICTL_AGENT_USER"], "agi-clerk")

    def test_a_forged_stamp_is_replaced(self):
        for forged in ("", "coa", "root", "watchdog"):
            _, env = _settle({"SUDO_USER": "agi-clerk", "AGICTL_AGENT_USER": forged})
            self.assertEqual(env["AGICTL_AGENT_USER"], "agi-clerk")

    def test_it_cannot_take_another_agents_name_or_config(self):
        _, env = _settle({
            "SUDO_USER": "agi-clerk",
            "VERSA_AGENT_NAME": "coa",
            "AGICTL_CONFIG": "/etc/versa-agi/coa_config.json",
        })
        self.assertEqual(env["VERSA_AGENT_NAME"], "clerk")
        self.assertEqual(env["AGICTL_CONFIG"], "/etc/versa-agi/clerk_config.json")

    def test_database_paths_of_its_own_choosing_are_dropped(self):
        env = {"SUDO_USER": "agi-clerk"}
        env.update({var: "/tmp/mine.db" for var in caller_identity.DB_PATH_VARS})
        _settle(env)
        for var in caller_identity.DB_PATH_VARS:
            self.assertNotIn(var, env)

    def test_agent_folder_must_be_its_own(self):
        _, env = _settle({"SUDO_USER": "agi-clerk",
                          "AGICTL_AGENT_DIR": "/home/coa/coa-env/.agent"})
        self.assertEqual(env["AGICTL_AGENT_DIR"], "/home/agi-clerk/.agent")
        _, env = _settle({"SUDO_USER": "agi-clerk",
                          "AGICTL_AGENT_DIR": "/home/agi-clerk/.agent"})
        self.assertEqual(env["AGICTL_AGENT_DIR"], "/home/agi-clerk/.agent")
        _, env = _settle({"SUDO_USER": "coa",
                          "AGICTL_AGENT_DIR": "/home/coa/coa-env/.agent"})
        self.assertEqual(env["AGICTL_AGENT_DIR"], "/home/coa/coa-env/.agent")

    def test_an_account_whose_name_starts_anothers(self):
        # agi-web must not resolve to agi-web-dev's folder or name.
        _, env = _settle({"SUDO_USER": "agi-web-dev",
                          "AGICTL_AGENT_DIR": "/home/agi-web/.agent"})
        self.assertEqual(env["AGICTL_AGENT_DIR"], "/home/agi-web-dev/.agent")
        self.assertEqual(env["VERSA_AGENT_NAME"], "web-dev")


class SystemAndOwner(unittest.TestCase):
    def test_lifeline_and_cron_are_left_alone(self):
        # Watchdog itself, no sudo: it may say which agent it acts for.
        _, env = _settle({"AGICTL_AGENT_USER": "agi-clerk", "AGICTL_TASKS_DB": "/x"})
        self.assertEqual(env["AGICTL_AGENT_USER"], "agi-clerk")
        self.assertEqual(env["AGICTL_TASKS_DB"], "/x")
        _, env = _settle({})
        self.assertNotIn("AGICTL_AGENT_USER", env)

    def test_root_acting_as_watchdog_is_the_system(self):
        # agitop runs `sudo -u watchdog agictl system config set-ini …`.
        _, env = _settle({"SUDO_USER": "root"})
        self.assertNotIn("AGICTL_AGENT_USER", env)

    def test_the_owners_own_login_without_sudo_is_named_and_not_an_agent(self):
        _, env = _settle({"SUDO_USER": "s7",
                          "AGICTL_CONFIG": "/etc/versa-agi/coa_config.json"})
        self.assertEqual(env["AGICTL_AGENT_USER"], "s7")
        self.assertNotIn("VERSA_AGENT_NAME", env)
        self.assertEqual(env["AGICTL_CONFIG"], "/etc/versa-agi/coa_config.json")

    def test_the_owner_with_sudo_is_not_limited(self):
        refusal, env = _settle({"SUDO_USER": "s7"}, euid=0, running_as="root",
                               args=("execute", "bash", "id"))
        self.assertIsNone(refusal)
        self.assertNotIn("AGICTL_AGENT_USER", env)


class WatchdogAsRoot(unittest.TestCase):
    """watchdog → root through its sudo rule: a short list, nothing else."""

    def _as_root(self, *args):
        return _settle({"SUDO_USER": "watchdog"}, euid=0, running_as="root", args=args)[0]

    def test_what_the_product_runs_is_allowed(self):
        for args in (
            ("agent", "deploy-skills", "clerk"),
            ("agent", "share-skill", "/home/coa/coa-env/.agent/skills/x.md"),
            ("system", "config", "apply-coa-sudoers", "--enabled", "true"),
            ("system", "config", "set-ini", "coa", "autonomous", "false"),
            ("system", "sync-instance", "--skip-coa-autonomous-apply"),
            ("system", "set-key", "brave", "abc"),
            ("pkg", "apply", "ripgrep"),
            ("model", "activate", "gemma4:26b"),
            ("model", "media", "generate", "flux1-dev"),
        ):
            self.assertIsNone(self._as_root(*args), args)

    def test_running_a_script_as_root_is_refused(self):
        for args in (
            ("execute", "bash", "id"),
            ("execute", "python", "print(1)"),
            ("bash", "id"),
            ("task", "run-due-scripts"),
            ("agent", "approve", "x", "--force"),
            ("system", "config", "set-ini", "agent", "cron_interval", "1"),
            ("pkg", "install", "ripgrep"),
            (),
        ):
            self.assertIsNotNone(self._as_root(*args), args)

    def test_every_root_call_in_the_product_is_on_the_list(self):
        """A new `sudo -n agictl …` in Lifeline or agictl must be added to
        ROOT_FOR_WATCHDOG, or it is refused on a machine."""
        calls = []
        for m in re.finditer(r"sudo -n /usr/local/bin/agictl ([a-z-]+(?: [a-z-]+)*)",
                             _read(CORE_INFRA, "lifeline.sh")):
            calls.append(tuple(m.group(1).split()))
        cli = _read(CORE_INFRA, "agictl", "cli.py")
        for m in re.finditer(r"_agictl_root_bin\(\),((?:\s*\"[a-z-]+\",)+)", cli):
            calls.append(tuple(re.findall(r"\"([a-z-]+)\"", m.group(1))))
        self.assertGreaterEqual(len(calls), 6, calls)
        for call in calls:
            self.assertTrue(caller_identity.root_command_allowed(list(call) + ["x"]), call)


class AgentLookup(unittest.TestCase):
    def test_registry_decides_and_the_naming_rule_is_the_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "agents.db")
            with sqlite3.connect(db) as conn:
                conn.execute("CREATE TABLE agents (name TEXT, os_user TEXT, workspace TEXT)")
                conn.execute("INSERT INTO agents VALUES ('Iris', 'agi-iris', '/home/agi-iris')")
            self.assertEqual(caller_identity.lookup_agent("agi-iris", db),
                             ("iris", "/home/agi-iris"))
            self.assertEqual(caller_identity.lookup_agent("agi-new", db), ("new", ""))
            self.assertIsNone(caller_identity.lookup_agent("s7", db))
            self.assertEqual(caller_identity.lookup_agent("coa", os.path.join(tmp, "none.db")),
                             ("coa", ""))


class Wrapper(unittest.TestCase):
    """bin/agictl-wrapper, run as written, with stand-ins for whoami and sudo."""

    def _run(self, extra_env: dict, args=("task", "list")):
        with tempfile.TemporaryDirectory() as tmp:
            for name, body in (
                ("whoami", "#!/bin/bash\necho agi-clerk\n"),
                ("sudo", "#!/bin/bash\nprintf '%s\\n' \"$@\" > \"${OUT}/argv\"\nenv > \"${OUT}/env\"\n"),
            ):
                path = os.path.join(tmp, name)
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(body)
                os.chmod(path, 0o755)
            env = {"PATH": tmp + ":/usr/bin:/bin", "HOME": tmp, "OUT": tmp}
            env.update(extra_env)
            subprocess.run(["bash", os.path.join(CORE_INFRA, "bin", "agictl-wrapper"), *args],
                           env=env, check=True, timeout=30)
            argv = _read(tmp, "argv").split("\n")[:-1]
            seen = dict(line.split("=", 1) for line in _read(tmp, "env").splitlines() if "=" in line)
        return argv, seen

    def test_nothing_is_passed_on_the_sudo_command_line(self):
        argv, seen = self._run({"AGICTL_CONFIG": "/etc/versa-agi/clerk_config.json",
                                "VERSA_CYCLE_ID": "clerk-1"})
        self.assertEqual(argv, ["-u", "watchdog", "/usr/local/lib/versa-agi/agictl",
                                "task", "list"])
        self.assertEqual(seen["AGICTL_AGENT_USER"], "agi-clerk")
        self.assertEqual(seen["VERSA_CYCLE_ID"], "clerk-1")

    def test_an_empty_variable_is_not_handed_on_as_empty(self):
        _, seen = self._run({"VERSA_LISTENING": "", "VERSA_CALL_BRIDGE": ""})
        self.assertNotIn("VERSA_LISTENING", seen)
        self.assertNotIn("VERSA_CALL_BRIDGE", seen)

    def test_the_wrapper_keeps_exactly_what_the_sudo_rule_names(self):
        text = _read(CORE_INFRA, "bin", "agictl-wrapper")
        loop = text[text.index("for _var in AGICTL_AGENT_DIR"):]
        loop = loop[:loop.index("; do")]
        names = re.findall(r"[A-Z][A-Z_]+", loop.replace("for _var in", ""))
        self.assertEqual(sorted(names + ["AGICTL_AGENT_USER"]), sorted(KEPT))


class SetupSudoRules(unittest.TestCase):
    """The sudoers block of setup.sh, run as written."""

    @classmethod
    def setUpClass(cls):
        text = _read(SRC, "setup.sh")
        start = text.index("# Sudoers (state_agent_account_permissions.md §1).")
        end = text.index("# COA Autonomous Mode — full sudo access")
        cls.block = text[start:end]
        cls.tmp = tempfile.mkdtemp()
        visudo = shutil.which("visudo") or "/usr/sbin/visudo"
        cls.have_visudo = os.path.exists(visudo)
        prelude = f"""
set -u
OUT={cls.tmp!r}
WATCHDOG_USER=watchdog; COA_USER=coa; LIB_DIR=/usr/local/lib/versa-agi
ok() {{ :; }}; warn() {{ echo "WARN $*" >> "$OUT/log"; }}
chown() {{ :; }}
mv() {{ cp "$1" "$OUT/$(basename "$2")"; }}
rm() {{ echo "rm $*" >> "$OUT/log"; }}
visudo() {{ {'"' + visudo + '" "$@"' if cls.have_visudo else 'return 0'}; }}
getent() {{ return 1; }}
groupadd() {{ echo "groupadd $*" >> "$OUT/log"; }}
usermod() {{ echo "usermod $*" >> "$OUT/log"; }}
id() {{ return 0; }}
sqlite3() {{ printf 'agi-clerk\\nagi-iris\\n'; }}
"""
        # agents.db must "exist" for the member loop: point the test at a file that does.
        open(os.path.join(cls.tmp, "agents.db"), "w").close()
        block = cls.block.replace('"/var/lib/versa-agi/agents.db"',
                                  '"' + os.path.join(cls.tmp, "agents.db") + '"')
        cls.result = subprocess.run(["bash", "-c", prelude + block],
                                    capture_output=True, text=True, timeout=60)
        cls.log = _read(cls.tmp, "log") if os.path.exists(os.path.join(cls.tmp, "log")) else ""

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_block_runs_and_every_file_passes_visudo(self):
        self.assertEqual(self.result.returncode, 0, self.result.stderr)
        self.assertNotIn("WARN", self.log)
        for name in ("versa_agi_agictl", "versa_agi_watchdog", "versa_agi_watchdog_root"):
            self.assertTrue(os.path.exists(os.path.join(self.tmp, name)), name)

    def test_agents_may_run_agictl_with_named_variables_only(self):
        rule = _read(self.tmp, "versa_agi_agictl")
        self.assertNotIn("SETENV", rule)
        self.assertIn("%agi_agents ALL=(watchdog) NOPASSWD: /usr/local/lib/versa-agi/agictl\n", rule)
        kept = re.search(r'env_keep \+= "([^"]+)"', rule).group(1).split()
        self.assertEqual(sorted(kept), sorted(KEPT))
        for never in ("PYTHONPATH", "BASH_ENV", "LD_PRELOAD", "SUDO_USER", "AGICTL_AGENTS_DB"):
            self.assertNotIn(never, rule)

    def test_watchdog_runs_commands_as_agents_only(self):
        rule = _read(self.tmp, "versa_agi_watchdog")
        self.assertEqual(rule.strip(), "watchdog ALL=(%agi_spawn, watchdog) NOPASSWD: ALL")
        self.assertNotIn("agi_agents", rule)
        self.assertIn("usermod -aG agi_spawn coa", self.log)
        self.assertIn("usermod -aG agi_spawn agi-clerk", self.log)
        self.assertIn("usermod -aG agi_spawn agi-iris", self.log)
        # Never the installing user or watchdog.
        self.assertNotRegex(self.log, r"usermod -aG agi_spawn (watchdog|\$\{?SUDO_USER)")
        self.assertNotIn("SUDO_USER", self.block.split("ensure_spawn_group() {")[1].split("\n}\n")[0])

    def test_no_apt_get_rule(self):
        self.assertIn("rm -f /etc/sudoers.d/versa_agi_pkg_installer", self.log)
        self.assertNotIn("apt-get", _read(self.tmp, "versa_agi_watchdog_root"))
        self.assertNotIn("apt-get install -y *", self.block)


class DatabaseModes(unittest.TestCase):
    """Only watchdog writes a database (PERM-A1)."""

    def test_setup_audit_and_restore_agree_on_640(self):
        setup = _read(SRC, "setup.sh")
        for line in setup.splitlines():
            if "chmod 660" in line and ".db" in line.lower() and "agent_memory.db" not in line:
                self.fail("a database is still made group-writable: " + line.strip())
        audit = _read(SRC, "audit_permissions.sh")
        for db in ("agents.db", "messages.db", "coa/tasks.db", "coa/cycles.db", "organization.db"):
            self.assertRegex(audit, rf'check "/var/lib/versa-agi/{re.escape(db)}"\s+"watchdog:coa"\s+"640"')
        self.assertNotIn('chmod 660 "${db}"', _read(SRC, "restore.sh"))

    @unittest.skipIf(os.geteuid() == 0, "root is not held by file modes")
    @unittest.skipUnless(shutil.which("sqlite3"), "sqlite3 is not installed")
    def test_a_file_without_write_permission_holds_against_a_journal_in_memory(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = os.path.join(tmp, "data")
            os.mkdir(folder)
            db = os.path.join(folder, "a.db")
            subprocess.run(["sqlite3", db, "CREATE TABLE t(v INTEGER); INSERT INTO t VALUES (1);"],
                           check=True)
            write = "PRAGMA journal_mode=MEMORY; UPDATE t SET v = 2;"
            try:
                # As the install was: the file writable, its folder not. The
                # folder alone does not hold.
                os.chmod(db, 0o660)
                os.chmod(folder, 0o555)
                subprocess.run(["sqlite3", db, write], capture_output=True, text=True)
                self.assertEqual(subprocess.run(["sqlite3", db, "SELECT v FROM t;"],
                                                capture_output=True, text=True).stdout.strip(), "2")
                # As it is now: no write permission on the file.
                os.chmod(db, 0o440)
                refused = subprocess.run(["sqlite3", db, write.replace("2", "3")],
                                         capture_output=True, text=True)
                self.assertIn("readonly", refused.stderr + refused.stdout)
                self.assertEqual(subprocess.run(["sqlite3", db, "SELECT v FROM t;"],
                                                capture_output=True, text=True).stdout.strip(), "2")
            finally:
                os.chmod(folder, stat.S_IRWXU)
                os.chmod(db, 0o600)


if __name__ == "__main__":
    unittest.main()


class CycleFiles(unittest.TestCase):
    """Lifeline's per-cycle files are in the agent's own data folder, not /tmp
    (state_agent_account_permissions.md §2.4, PERM-B1)."""

    def setUp(self):
        self.text = _read(CORE_INFRA, "lifeline.sh")

    def test_no_prompt_or_environment_file_is_written_under_tmp(self):
        for name in ("SYSTEM_FILE", "WAKE_FILE", "TASKS_FILE", "CONVO_FILE", "GAMES_FILE",
                     "ENV_SCRIPT", "ROUTING_FILE", "ROUTING_ATTACHMENTS_FILE"):
            line = re.search(rf"^\s*{name}=(.*)$", self.text, re.M).group(1)
            self.assertIn("${CYCLE_RUN_DIR}/", line, name)
        self.assertNotRegex(self.text, r"chmod 644 [^\n]*(SYSTEM_FILE|ENV_SCRIPT|ROUTING_FILE)")

    def test_setup_clears_what_earlier_versions_left_under_tmp(self):
        setup = _read(SRC, "setup.sh")
        block = setup[setup.index("# Prompt, context and environment files that earlier versions wrote under"):][:900]
        for name in ("_system.txt", "_wake.txt", "_tasks.txt", "_convo.txt", "_games.txt",
                     "_env.sh", "_routing.json", "_routing_attachments.txt"):
            self.assertIn("/tmp/versa_agi_*" + name, block, name)
        # Locks and rate-limit markers stay: they hold nothing.
        self.assertNotIn(".lock", block)
        self.assertNotIn(".cooldown", block)

    def test_every_one_is_removed_when_the_cycle_ends(self):
        cleanup = self.text[self.text.index('rm -f "${ENV_SCRIPT}" "${TASKS_FILE}"'):]
        cleanup = cleanup[:cleanup.index("\n\n")]
        for name in ("SYSTEM_FILE", "WAKE_FILE", "TASKS_FILE", "CONVO_FILE", "GAMES_FILE",
                     "ENV_SCRIPT", "ROUTING_FILE", "ROUTING_ATTACHMENTS_FILE"):
            self.assertIn("${" + name + "}", cleanup, name)

    def test_the_folder_is_made_and_what_older_versions_left_is_removed(self):
        start = self.text.index("# ── cycle files: begin ──")
        end = self.text.index("# ── cycle files: end ──")
        with tempfile.TemporaryDirectory() as tmp:
            agent = "t" + os.path.basename(tmp)[-8:].lower().replace("_", "x")
            data = os.path.join(tmp, "data")
            os.makedirs(os.path.join(data, agent))
            legacy = f"/tmp/versa_agi_{agent}_system.txt"
            with open(legacy, "w", encoding="utf-8") as fh:
                fh.write("an old prompt")
            block = self.text[start:end].replace("/var/lib/versa-agi/", data + "/")
            script = (f"AGENT_NAME={agent}; AGENT_USER=$(id -un); log() {{ echo \"$*\"; }}\n"
                      + block + "\necho \"DIR=${CYCLE_RUN_DIR}\"\n")
            try:
                out = subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                                     timeout=30, check=True).stdout
                run_dir = os.path.join(data, agent, "run")
                self.assertIn("DIR=" + run_dir, out)
                # setgid, so a file written there takes the folder's group.
                self.assertEqual(stat.S_IMODE(os.stat(run_dir).st_mode), 0o2750)
                self.assertFalse(os.path.exists(legacy))
            finally:
                if os.path.exists(legacy):
                    os.remove(legacy)

    def test_the_agent_reads_its_files_through_the_group_every_agent_is_in(self):
        """watchdog's own group is one no agent is in on a normal install. A run
        folder left with it gave every cycle exit code 1 (3.10.5 as first shipped)."""
        start = self.text.index("# ── cycle files: begin ──")
        block = self.text[start:self.text.index("# ── cycle files: end ──")]
        self.assertIn('chgrp agi_agents "${CYCLE_RUN_DIR}"', block)
        self.assertIn('chmod 2750 "${CYCLE_RUN_DIR}"', block)
        self.assertNotIn('chmod 750 "${CYCLE_RUN_DIR}"', block)
        setup = _read(SRC, "setup.sh")
        self.assertIn('chown -R "${WATCHDOG_USER}:agi_agents" "${_run}" && chmod 2750 "${_run}"', setup)
        self.assertIn('mkdir -p "${vdata}/run"', setup)
        self.assertIn('chown -R "${WATCHDOG_USER}:agi_agents" "${vdata}/run" && chmod 2750 "${vdata}/run"', setup)
        self.assertIn("mkdir -p /var/lib/versa-agi/coa/run", setup)

    def test_a_file_written_in_the_folder_takes_the_folders_group(self):
        """The mechanism the fix relies on, tried with a group this account is in."""
        groups = [g for g in os.getgroups() if g != os.getegid()]
        if not groups:
            self.skipTest("this account is in one group only")
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = os.path.join(tmp, "run")
            os.mkdir(run_dir)
            os.chown(run_dir, -1, groups[0])
            os.chmod(run_dir, 0o2750)
            path = os.path.join(run_dir, "env.sh")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("x")
            os.chmod(path, 0o640)
            self.assertEqual(os.stat(path).st_gid, groups[0])


class AgentHomes(unittest.TestCase):
    """An agent's home is closed to the other agents; COA and watchdog can
    enter (state_agent_account_permissions.md §1 item 3, PERM-B2, PERM-B3)."""

    def setUp(self):
        self.setup = _read(SRC, "setup.sh")

    def test_the_governing_group_holds_coa_watchdog_and_the_installer_only(self):
        body = self.setup.split("ensure_govern_group() {")[1].split("\n}\n")[0]
        members = re.findall(r'usermod -aG agi_govern "\$\{(\w+)[:}]', body)
        self.assertEqual(sorted(members), ["COA_USER", "SUDO_USER", "WATCHDOG_USER"])

    def test_membership_exists_before_the_link_service_is_restarted(self):
        # That service runs as watchdog and starts Lifeline passes itself; it
        # gets its groups when it starts.
        self.assertLess(self.setup.index("\nensure_govern_group\n"),
                        self.setup.index("systemctl restart versa-agi-link"))

    def test_setup_approval_and_audit_agree_on_the_home(self):
        self.assertIn('chown "${os_user}:agi_govern" "${ahome}" && chmod 770 "${ahome}"', self.setup)
        cli = _read(CORE_INFRA, "agictl", "cli.py")
        self.assertIn('["chown", f"{os_user}:agi_govern", agent_root]', cli)
        audit = _read(SRC, "audit_permissions.sh")
        self.assertRegex(audit, r'check "\$\{AHOME\}"\s+"\$\{os_user\}:agi_govern"\s+"770"')

    def test_coas_agent_folder_is_closed_to_the_other_agents(self):
        self.assertNotIn('"${DEPLOYED_COA_ENV}/.agent" && chmod 775', self.setup)
        self.assertEqual(self.setup.count('chmod 770 "${DEPLOYED_COA_ENV}/.agent"'), 2)
        self.assertIn('chown "${COA_USER}:${COA_USER}" "${DEPLOYED_COA_ENV}/.agent" && chmod 770', self.setup)
        audit = _read(SRC, "audit_permissions.sh")
        self.assertRegex(audit, r'check "\$\{COA\}/\.agent"\s+"coa:coa"\s+"770"')

    def test_coas_workspace_and_the_shared_tools_project_stay_shared(self):
        # AGi-Tools and the local projects live there and every agent works in them.
        self.assertIn('chown "${COA_USER}:agi_agents" "${DEPLOYED_COA_ENV}/workspace"', self.setup)
        audit = _read(SRC, "audit_permissions.sh")
        self.assertRegex(audit, r'check "\$\{COA\}/workspace"\s+"coa:agi_agents"\s+"2770"')

    def test_no_agent_account_stays_in_watchdogs_own_group(self):
        body = self.setup.split("drop_agents_from_watchdog_group() {")[1].split("\n}\n")[0]
        with tempfile.TemporaryDirectory() as tmp:
            script = f"""
WATCHDOG_USER=watchdog; COA_USER=coa
ok() {{ :; }}
getent() {{ [ "$1" = group ] && echo "watchdog:x:1002:coa,agi-clerk,s7"; }}
gpasswd() {{ echo "$*" >> {tmp}/log; }}
drop_agents_from_watchdog_group() {{{body}
}}
drop_agents_from_watchdog_group
"""
            subprocess.run(["bash", "-c", script], check=True, timeout=30)
            log = _read(tmp, "log").splitlines()
        # The agents go; the person who installed stays.
        self.assertEqual(log, ["-d coa watchdog", "-d agi-clerk watchdog"])
        # It runs with the other group work, and in the final pass.
        self.assertEqual(self.setup.count("\ndrop_agents_from_watchdog_group\n"), 1)
        self.assertIn("  drop_agents_from_watchdog_group\n", self.setup)
        audit = _read(SRC, "audit_permissions.sh")
        self.assertIn("no agent account is in group watchdog", audit)

    def test_removed_agents_archive_is_for_its_owner_only(self):
        self.assertIn("chmod 700 /var/lib/versa-agi/archive", self.setup)
        self.assertNotIn("chmod 755 /var/lib/versa-agi/archive", self.setup)
        cli = _read(CORE_INFRA, "agictl", "cli.py")
        block = cli[cli.index('archive_dir = "/var/lib/versa-agi/archive"'):][:1200]
        self.assertIn("os.umask(0o077)", block)


class SettingsView(unittest.TestCase):
    """setup.ini holds keys and the Primary User's VersaVoice API token and is
    for watchdog only. An agent's process reads a copy with the secret values
    blanked, written for each cycle (PERM-A9)."""

    def _view(self, text: str) -> str:
        lifeline = _read(CORE_INFRA, "lifeline.sh")
        fn = lifeline[lifeline.index("# ── settings view: begin ──"):
                      lifeline.index("# ── settings view: end ──")]
        with tempfile.TemporaryDirectory() as tmp:
            src, dest = os.path.join(tmp, "in.ini"), os.path.join(tmp, "out.ini")
            with open(src, "w", encoding="utf-8") as fh:
                fh.write(text)
            subprocess.run(["bash", "-c", fn + f'\nwrite_settings_view "{src}" "{dest}"\n'],
                           check=True, timeout=30)
            return _read(dest)

    def test_every_secret_in_the_stock_file_is_blanked_and_nothing_else(self):
        import configparser
        stock = _read(SRC, "setup.ini.stock")
        # Give every key a value, so a secret that stays would show.
        filled = "\n".join(
            (line.split("=", 1)[0] + "=VALUE-" + line.split("=", 1)[0].strip())
            if (line and not line.lstrip().startswith(("#", ";", "[")) and "=" in line) else line
            for line in stock.splitlines()
        ) + "\n"
        before = configparser.ConfigParser(delimiters=("=",), strict=False, interpolation=None)
        before.read_string(filled)
        after = configparser.ConfigParser(delimiters=("=",), strict=False, interpolation=None)
        after.read_string(self._view(filled))
        secret = re.compile(r"(key|token|secret|password|credential)")
        blanked, kept = [], 0
        for section in before.sections():
            for key, value in before.items(section):
                seen = after.get(section, key)
                if seen == "":
                    blanked.append(f"{section}.{key}")
                else:
                    self.assertEqual(seen, value, f"{section}.{key}")
                    kept += 1
                    # A setting that keeps its value must not look like a secret.
                    self.assertIsNone(secret.search(key), f"{section}.{key} looks like a secret and is not blanked")
        self.assertEqual(sorted(blanked), sorted([
            "versavoice.api_token", "system.vv_agent_key", "local_ai.hf_token",
            "local_ai.inference_master_key", "third_party.xai_api_key",
            "third_party.openai_api_key", "third_party.anthropic_api_key",
            "third_party.openrouter_api_key", "third_party.google_api_key",
            "gcp.service_account_key", "search.brave_api_key", "search.exa_api_key",
        ]))
        self.assertGreater(kept, 50)

    def test_a_secret_written_over_several_lines_goes_whole(self):
        out = self._view('[gcp]\nservice_account_key={"a":\n  "PRIVATE"}\nproject=demo\n'
                         '[agent]\nreasoning_max_tokens=4000\ntoken_budget=7\n')
        self.assertNotIn("PRIVATE", out)
        self.assertIn("project=demo", out)
        self.assertIn("reasoning_max_tokens=4000", out)
        self.assertIn("token_budget=7", out)

    def test_the_cycle_is_given_the_copy_and_its_account_id(self):
        lifeline = _read(CORE_INFRA, "lifeline.sh")
        self.assertIn("echo \"export AGICTL_SETUP_INI='${SETTINGS_VIEW}'\"", lifeline)
        self.assertIn("export VERSA_SUB_ACCOUNT_ID=", lifeline)
        cleanup = lifeline[lifeline.index('rm -f "${ENV_SCRIPT}" "${TASKS_FILE}"'):][:400]
        self.assertIn("${SETTINGS_VIEW}", cleanup)

    def test_the_copy_is_not_handed_on_to_agictl(self):
        # agictl runs as watchdog and must read the real file.
        self.assertNotIn("AGICTL_SETUP_INI", KEPT)
        self.assertNotIn("AGICTL_SETUP_INI", _read(CORE_INFRA, "bin", "agictl-wrapper"))

    def _agent_process_modules(self):
        """Every module an agent's own process can load: the harness, the
        model drivers, and the shared modules setup copies beside them."""
        setup = _read(SRC, "setup.sh")
        shared = setup[setup.index("_VERSA_LIB_SHARED_PY=("):]
        shared = re.findall(r"[a-z_]+\.py", shared[:shared.index(")")])
        self.assertIn("live_call_config.py", shared)
        files = [os.path.join(CORE_INFRA, name) for name in shared]
        for folder in ("harness", "model_drivers"):
            base = os.path.join(CORE_INFRA, folder)
            files += [os.path.join(base, f) for f in sorted(os.listdir(base)) if f.endswith(".py")]
        return [f for f in files if os.path.isfile(f)]

    def test_no_reader_in_the_agents_process_names_the_system_file_alone(self):
        # In an agent's process the system file is closed, and code that reads
        # it gets nothing and falls back without a word. live_call_config did:
        # Live Call looked switched off and COA lost its calling tool on the
        # first live update (2026-11-24). Every module such a process can
        # load is scanned, not a list of names.
        fallback_lists = {"image_processing.py", "audio_processing.py"}   # env is tried first
        for path in self._agent_process_modules():
            name = os.path.basename(path)
            text = _read(path)
            if name != "model_catalog.py":
                self.assertNotIn("SETUP_INI_CANONICAL", text,
                                 f"{name}: use model_catalog.setup_ini_path()")
            for number, line in enumerate(text.splitlines(), 1):
                if '/etc/versa-agi/setup.ini"' not in line or line.lstrip().startswith("#"):
                    continue
                if "AGICTL_SETUP_INI" in line or name in fallback_lists:
                    continue
                if name == "model_catalog.py" and line.startswith("SETUP_INI_CANONICAL ="):
                    continue
                self.fail(f"{name}:{number} reads the system settings file directly: {line.strip()}")
        for name in fallback_lists:
            self.assertIn('os.environ.get("AGICTL_SETUP_INI")',
                          _read(CORE_INFRA, "model_drivers", name), name)

    def test_live_call_is_seen_as_on_from_the_cycles_copy(self):
        import importlib
        sys.path.insert(0, CORE_INFRA)
        with tempfile.TemporaryDirectory() as tmp:
            view = os.path.join(tmp, "setup.ini")
            with open(view, "w", encoding="utf-8") as fh:
                fh.write("[features]\nlive_call=true\n[live_call]\ncall_model=some-model\n"
                         "[third_party]\nopenai_api_key=\n")
            old = os.environ.get("AGICTL_SETUP_INI")
            os.environ["AGICTL_SETUP_INI"] = view
            try:
                import live_call_config
                importlib.reload(live_call_config)
                settings = live_call_config.read_settings()
                self.assertTrue(settings.enabled)
                self.assertEqual(settings.call_model, "some-model")
            finally:
                if old is None:
                    os.environ.pop("AGICTL_SETUP_INI", None)
                else:
                    os.environ["AGICTL_SETUP_INI"] = old

    def test_the_files_with_keys_are_for_watchdog_alone(self):
        # Owner only, 600. A group is not relied on: on an older install the
        # COA account is in watchdog's group, and watchdog's primary group can
        # be agi_agents (found on the first live update, 2026-11-24).
        setup = _read(SRC, "setup.sh")
        for line in setup.splitlines():
            if "agi_agents" in line and ("setup.ini" in line or "INI_FILE" in line
                                         or "install-acceptance" in line):
                if line.lstrip().startswith("#"):
                    continue
                self.fail("the settings file is put in the agents' group: " + line.strip())
        # Every place that sets the deployed settings file's mode sets 600.
        for match in re.finditer(r'chmod (\d+) ("?/etc/versa-agi/setup\.ini"?|"\$\{INI_FILE\}")', setup):
            self.assertEqual(match.group(1), "600", match.group(0))
        self.assertGreaterEqual(len(re.findall(r'chmod 600 "\$\{INI_FILE\}"', setup)), 3)
        self.assertIn('/etc/versa-agi/coa_config.json && chmod 600', setup)
        self.assertIn('"${sa_config}" && chmod 600 "${sa_config}"', setup)
        self.assertIn('"${env_file}" && chmod 600 "${env_file}"', setup)
        audit = _read(SRC, "audit_permissions.sh")
        for path in ("setup.ini", "coa_config.json", "coa.env"):
            self.assertRegex(audit, rf'check "/etc/versa-agi/{re.escape(path)}"\s+"watchdog:watchdog"\s+"600"')
        cli = _read(CORE_INFRA, "agictl", "cli.py")
        self.assertNotIn('f"watchdog:{os_user}", agent_config_path', cli)
        self.assertNotIn('["chmod", "640", agent_config_path]', cli)

    def test_the_link_service_is_restarted_on_every_setup_run(self):
        # It runs as watchdog and takes its groups when it starts.
        setup = _read(SRC, "setup.sh")
        step = setup[setup.index("Step 11b: Link Service"):setup.index("Step 11c")]
        self.assertEqual(step.count("systemctl restart versa-agi-link"), 2)
        self.assertNotIn("systemctl start versa-agi-link", step)

    def test_health_check_expects_the_database_modes_setup_sets(self):
        health = _read(CORE_INFRA, "scripts", "health_checks.sh")
        self.assertNotRegex(health, r'perms_check "[^"]*\.db" "660"')

    def test_the_harness_takes_its_account_id_from_the_cycle(self):
        sys.path.insert(0, os.path.join(CORE_INFRA))
        from harness import agent_mailbox
        old = os.environ.get("VERSA_SUB_ACCOUNT_ID")
        os.environ["VERSA_SUB_ACCOUNT_ID"] = "acct_123"
        try:
            self.assertEqual(agent_mailbox.identity_ids("clerk"), ["acct_123", "clerk"])
        finally:
            if old is None:
                os.environ.pop("VERSA_SUB_ACCOUNT_ID", None)
            else:
                os.environ["VERSA_SUB_ACCOUNT_ID"] = old
