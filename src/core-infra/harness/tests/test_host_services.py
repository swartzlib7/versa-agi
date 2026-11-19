"""Host services setup installs: SSH tunnel retry schedule, log rotation,
startup settle, the shutdown guard and the proof of life.

state_host_services.md. Run from core-infra:
  python -m unittest harness.tests.test_host_services
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import textwrap
import unittest

CORE_INFRA = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SRC = os.path.dirname(CORE_INFRA)
RUNNER = os.path.join(CORE_INFRA, "scripts", "versa_agi_tunnel.sh")
TUNNEL_LIB = os.path.join(CORE_INFRA, "scripts", "tunnel_service.sh")
SETUP = os.path.join(SRC, "setup.sh")
GUARD = os.path.join(CORE_INFRA, "scripts", "shutdown_guard.sh")
GUARD_LIB = os.path.join(CORE_INFRA, "scripts", "shutdown_guard_service.sh")
SLEEP_LIB = os.path.join(CORE_INFRA, "scripts", "sleep_hold.sh")
LIFELINE = os.path.join(CORE_INFRA, "lifeline.sh")


def _write_exec(path: str, body: str) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("#!/usr/bin/env bash\n" + textwrap.dedent(body))
    os.chmod(path, 0o755)


class TestTunnelRunner(unittest.TestCase):
    def _run(self, attempts: int, connected_on: int = 0):
        """Fake ssh fails at once, except attempt ``connected_on`` stays up 400 s."""
        tmp = tempfile.mkdtemp()
        clock = os.path.join(tmp, "clock")
        waits = os.path.join(tmp, "waits")
        count = os.path.join(tmp, "count")
        for path, value in ((clock, "0"), (count, "0")):
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(value)
        _write_exec(os.path.join(tmp, "ssh"), f"""
            n=$(( $(cat {count}) + 1 )); echo $n > {count}
            if [ "$n" = "{connected_on}" ]; then echo $(( $(cat {clock}) + 400 )) > {clock}; fi
            exit 255
        """)
        _write_exec(os.path.join(tmp, "sleep"), f"""
            echo "$1" >> {waits}
            echo $(( $(cat {clock}) + $1 )) > {clock}
        """)
        env = os.environ.copy()
        env.update({
            "TUNNEL_SSH_BIN": os.path.join(tmp, "ssh"),
            "TUNNEL_SLEEP_BIN": os.path.join(tmp, "sleep"),
            "TUNNEL_CLOCK_FILE": clock,
            "TUNNEL_MAX_ATTEMPTS": str(attempts),
        })
        result = subprocess.run(
            ["bash", RUNNER, "-L", "8080", "-i", "/tmp/key", "watchdog@192.0.2.1"],
            env=env, capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        with open(waits, encoding="utf-8") as handle:
            return [int(line) for line in handle.read().split()], result.stdout

    def test_every_minute_for_30_minutes_then_every_30_minutes(self):
        waits, out = self._run(attempts=34)
        self.assertEqual(waits, [5] + [60] * 30 + [1800, 1800])
        self.assertEqual(out.count("Retrying every minute for 30 minutes"), 1)
        self.assertEqual(out.count("Retrying every 30 minutes"), 1)

    def test_a_connection_that_stayed_up_starts_the_schedule_again(self):
        waits, out = self._run(attempts=6, connected_on=3)
        self.assertEqual(waits, [5, 60, 5, 60, 60])
        self.assertEqual(out.count("Retrying every minute for 30 minutes"), 2)

    def test_missing_arguments_fail(self):
        result = subprocess.run(["bash", RUNNER, "-L", "8080"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)


class TestTunnelUnitRead(unittest.TestCase):
    def _read(self, unit_text: str) -> str:
        with tempfile.NamedTemporaryFile("w", suffix=".service", delete=False) as handle:
            handle.write(unit_text)
            unit = handle.name
        result = subprocess.run(
            ["bash", "-c", f'source "{TUNNEL_LIB}"; TUNNEL_UNIT_FILE="{unit}"; read_tunnel_service'],
            capture_output=True, text=True,
        )
        os.unlink(unit)
        return result.stdout.strip()

    def test_reads_the_old_ssh_unit(self):
        old = (
            "[Service]\nUser=watchdog\n"
            "ExecStart=/usr/bin/ssh -N -L 8080:localhost:8080 \\\n"
            "  -o ServerAliveInterval=30 \\\n"
            "  -i /home/watchdog/.ssh/versa_agi_ed25519 \\\n"
            "  watchdog@192.168.4.114\n"
            "Restart=always\nRestartSec=5\n"
        )
        self.assertEqual(self._read(old), "192.168.4.114 8080 /home/watchdog/.ssh/versa_agi_ed25519 watchdog")

    def test_reads_the_runner_unit(self):
        new = (
            "[Service]\nUser=watchdog\n"
            "ExecStart=/usr/local/lib/versa-agi/versa_agi_tunnel.sh -L 8080 "
            "-i /home/watchdog/.ssh/versa_agi_ed25519 watchdog@192.168.4.114\n"
            "Restart=always\n"
        )
        self.assertEqual(self._read(new), "192.168.4.114 8080 /home/watchdog/.ssh/versa_agi_ed25519 watchdog")


class TestLogRotation(unittest.TestCase):
    def setUp(self):
        with open(SETUP, encoding="utf-8") as handle:
            self.setup = handle.read()

    def test_old_weekly_mv_line_is_gone_and_stripped(self):
        self.assertNotIn("mv ${log_file}", self.setup)
        self.assertNotIn("LOG_ROTATION=", self.setup)
        self.assertIn('grep -v "versa-agi-archive" | crontab -u "${WATCHDOG_USER}" -', self.setup)

    def test_logrotate_stanza_never_writes_in_var_log(self):
        self.assertIn("copytruncate", self.setup)
        self.assertIn("maxsize 200M", self.setup)
        self.assertIn("su ${WATCHDOG_USER} ${WATCHDOG_USER}", self.setup)
        self.assertIn("olddir ${archive_dir}", self.setup)

    def test_fresh_and_update_both_apply_it(self):
        self.assertGreaterEqual(self.setup.count("\nensure_log_rotation\n"), 1)
        self.assertGreaterEqual(self.setup.count("\n    ensure_log_rotation\n"), 1)


class TestStartupSettle(unittest.TestCase):
    """Lifeline's settle block, run on its own with a fake uptime and settle file."""

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(CORE_INFRA, "lifeline.sh"), encoding="utf-8") as handle:
            text = handle.read()
        start = text.index("# ─── Startup settle window")
        cls.block = text[start:text.index("# Pinned conclusions", start)]

    def _run(self, uptime: int, gap: str = "", settle_until: str | None = None, minutes: int = 5):
        tmp = tempfile.mkdtemp()
        uptime_file = os.path.join(tmp, "uptime")
        settle_file = os.path.join(tmp, "settle_until")
        ini = os.path.join(tmp, "setup.ini")
        with open(uptime_file, "w", encoding="utf-8") as handle:
            handle.write(f"{uptime}.42 100.00\n")
        with open(ini, "w", encoding="utf-8") as handle:
            handle.write(f"[agent]\nstartup_settle_minutes={minutes}\n")
        if settle_until is not None:
            with open(settle_file, "w", encoding="utf-8") as handle:
                handle.write(settle_until)
        script = (
            "set -euo pipefail\nlog() { echo \"LOG $*\"; }\n"
            f"SETUP_INI={ini}\n_tick_gap='{gap}'\n{self.block}\n"
            "echo \"SETTLING=${SETTLING}\"\n"
        )
        env = {**os.environ, "VERSA_UPTIME_FILE": uptime_file, "VERSA_SETTLE_FILE": settle_file}
        out = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True, check=True).stdout
        if not os.path.exists(settle_file):
            return out, None
        with open(settle_file, encoding="utf-8") as handle:
            return out, handle.read().strip()

    def test_holds_in_the_first_minutes_after_boot(self):
        out, stored = self._run(uptime=120)
        self.assertIn("SETTLING=true", out)
        self.assertIn("LOG SETTLE:", out)
        self.assertIsNone(stored)

    def test_runs_normally_once_booted_long_enough(self):
        out, _ = self._run(uptime=301)
        self.assertIn("SETTLING=false", out)
        self.assertNotIn("SETTLE:", out)

    def test_outage_gap_starts_a_window_that_later_ticks_keep(self):
        out, stored = self._run(uptime=99999, gap="2026-11-05 03:57 to 2026-11-05 11:47 UTC")
        self.assertIn("SETTLING=true", out)
        self.assertIsNotNone(stored)
        out, _ = self._run(uptime=99999, settle_until=stored)
        self.assertIn("SETTLING=true", out)

    def test_expired_or_bad_settle_file_is_ignored(self):
        for value in ("1", "garbage"):
            out, _ = self._run(uptime=99999, settle_until=value)
            self.assertIn("SETTLING=false", out)

    def test_zero_minutes_turns_it_off(self):
        out, stored = self._run(uptime=10, gap="x to y UTC", minutes=0)
        self.assertIn("SETTLING=false", out)
        self.assertIsNone(stored)

    def test_hold_comes_before_work_detection_and_skips_force(self):
        with open(os.path.join(CORE_INFRA, "lifeline.sh"), encoding="utf-8") as handle:
            text = handle.read()
        hold = text.index('log "SETTLE: ${AGENT_NAME} — spawn held')
        self.assertLess(hold, text.index("# ─── Work Detection"))
        self.assertIn('[ "${SETTLING}" = true ] && [ "${FORCE_RUN}" != "true" ] && [ "${IDE_GENERATE}" != "true" ]', text)
        self.assertIn('[ "${SETTLING:-false}" = true ] && [ "${FORCE_RUN}" != "true" ] && return 0', text)



def _read(path: str) -> str:
    with open(path, encoding="utf-8") as handle:
        return handle.read()


class TestShutdownGuard(unittest.TestCase):
    """shutdown_guard.sh with fake systemctl, pgrep, pkill, agictl, sleep and clock (§1.4.1)."""

    PROC = {
        "coa": "4101 /usr/local/lib/versa-agi/venv/bin/python -m harness.agent_harness --agent coa --system-file /x",
        "outreach": "4102 /usr/local/lib/versa-agi/venv/bin/python3.12 -m harness.agent_harness --agent outreach --model m",
    }
    PATTERN = r"^[^ ]*python[0-9.]* -m harness\.agent_harness --agent "

    def _run(self, state="stopping", agents=("coa",), ini="", closes_at=None, end_reported_at=None,
             markers=True):
        """``closes_at``: fake-clock second at which every harness is gone on its own.
        ``end_reported_at``: second at which Lifeline removes the activity_open markers."""
        tmp = tempfile.mkdtemp()
        state_dir = os.path.join(tmp, "state")
        os.makedirs(state_dir)
        procs, clock, calls = (os.path.join(tmp, n) for n in ("procs", "clock", "calls"))
        with open(procs, "w", encoding="utf-8") as handle:
            handle.write("".join(self.PROC[a] + "\n" for a in agents))
        with open(clock, "w", encoding="utf-8") as handle:
            handle.write("1000")
        open(calls, "w").close()
        for agent in agents:
            os.makedirs(os.path.join(state_dir, agent))
            if markers:
                with open(os.path.join(state_dir, agent, "activity_open"), "w") as handle:
                    handle.write(f"{agent}-1 990\n")
        ini_path = os.path.join(tmp, "setup.ini")
        with open(ini_path, "w", encoding="utf-8") as handle:
            handle.write("[agent]\nstartup_settle_minutes=5\n" + ini)
        _write_exec(os.path.join(tmp, "systemctl"), f"""
            echo "{state}"
        """)
        _write_exec(os.path.join(tmp, "pgrep"), f"""
            echo "pgrep $2" >> {tmp}/patterns
            if [ "$1" = "-af" ]; then cat {procs}; fi
            [ -s {procs} ]
        """)
        _write_exec(os.path.join(tmp, "pkill"), f"""
            echo "pkill $* at $(cat {clock})" >> {calls}
            : > {procs}
        """)
        _write_exec(os.path.join(tmp, "agictl"), f"""
            echo "agictl $*" >> {calls}
        """)
        _write_exec(os.path.join(tmp, "sleep"), f"""
            now=$(( $(cat {clock}) + $1 )); echo $now > {clock}
            if [ -n "{closes_at or ''}" ] && [ $now -ge "{closes_at or 0}" ]; then : > {procs}; fi
            if [ -n "{end_reported_at or ''}" ] && [ $now -ge "{end_reported_at or 0}" ]; then rm -f {state_dir}/*/activity_open; fi
        """)
        log = os.path.join(tmp, "lifeline.log")
        env = {**os.environ, "VERSA_STATE_DIR": state_dir, "VERSA_SETUP_INI": ini_path, "VERSA_LIFELINE_LOG": log,
               "VERSA_GUARD_CLOCK_FILE": clock}
        for name in ("systemctl", "pgrep", "pkill", "agictl", "sleep"):
            env[f"VERSA_{name.upper()}_BIN"] = os.path.join(tmp, name)
        result = subprocess.run(["bash", GUARD], env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        if os.path.exists(os.path.join(tmp, "patterns")):
            self.assertEqual(set(_read(os.path.join(tmp, "patterns")).splitlines()), {"pgrep " + self.PATTERN})
        return {
            "calls": _read(calls).splitlines(),
            "log": _read(log) if os.path.exists(log) else "",
            "flag": os.path.exists(os.path.join(state_dir, "shutdown_pending")),
            "clock": int(_read(clock)),
        }

    def test_it_does_nothing_unless_the_system_is_stopping(self):
        for state in ("running", "degraded", ""):
            out = self._run(state=state)
            self.assertEqual((out["calls"], out["log"], out["flag"]), ([], "", False), state)

    def test_no_agent_in_a_cycle_only_sets_the_spawn_hold(self):
        out = self._run(agents=())
        self.assertTrue(out["flag"])
        self.assertEqual(out["calls"], [])
        self.assertIn("no agent is in a cycle", out["log"])

    def test_each_running_agent_is_asked_once_with_the_deadline(self):
        out = self._run(agents=("coa", "outreach"), closes_at=1030, end_reported_at=1032)
        self.assertTrue(out["flag"])
        self.assertEqual(out["calls"], [
            "agictl agent chat-close coa --reason shutdown --deadline-epoch 1120",
            "agictl agent chat-close outreach --reason shutdown --deadline-epoch 1120",
        ])
        self.assertIn("asking coa outreach to hand off within 120 s", out["log"])
        self.assertIn("every agent closed its cycle", out["log"])
        self.assertIn("Lifeline reported every cycle's end", out["log"])
        self.assertLess(out["clock"], 1040, "it does not wait out the grace once the agents are done")

    def test_an_agent_that_does_not_close_is_stopped_at_the_end_of_the_grace(self):
        out = self._run(end_reported_at=1125)
        self.assertEqual(out["calls"][1], f"pkill -TERM -f {self.PATTERN} at 1120")
        self.assertIn("coa did not close within 120 s", out["log"])
        self.assertIn("Lifeline reported every cycle's end", out["log"])

    def test_a_missing_end_report_is_waited_for_20_seconds_and_logged(self):
        out = self._run(closes_at=1010)
        self.assertIn("Lifeline did not report the end of coa within 20 s", out["log"])
        self.assertEqual(out["clock"], 1030)

    def test_the_grace_setting(self):
        for ini, deadline in (("shutdown_grace_seconds=10\n", 1030), ("shutdown_grace_seconds=999\n", 1300),
                              ("shutdown_grace_seconds=45\n", 1045), ("shutdown_grace_seconds=soon\n", 1120)):
            out = self._run(ini=ini, closes_at=1002, end_reported_at=1002)
            self.assertTrue(out["calls"][0].endswith(f"--deadline-epoch {deadline}"), (ini, out["calls"]))

    def test_only_the_harness_python_process_counts(self):
        """A shell, `timeout` or `sudo` that started it, or an editor that mentions the
        module, must not hold a shutdown for the whole grace."""
        import re
        pattern = re.compile(self.PATTERN)
        self.assertTrue(pattern.search(self.PROC["coa"].split(" ", 1)[1]))
        self.assertTrue(pattern.search(self.PROC["outreach"].split(" ", 1)[1]))
        for other in (
            "sudo -u agi-coa timeout 95m bash -c source '/tmp/e' && /v/bin/python -m harness.agent_harness --agent 'coa'",
            "bash -c source '/tmp/e' && cd '/w' && /v/bin/python -m harness.agent_harness --agent 'coa' --model m",
            "vim /home/watchdog/core-infra/harness/agent_harness.py",
            "grep -r harness.agent_harness .",
            "/usr/bin/python3 -m harness.tests.run_tests",
        ):
            self.assertIsNone(pattern.search(other), other)

    def test_zero_switches_the_guard_off(self):
        out = self._run(ini="shutdown_grace_seconds=0\n")
        self.assertEqual((out["calls"], out["flag"]), ([], False))
        self.assertIn("switched off", out["log"])


class TestShutdownGuardUnit(unittest.TestCase):
    def install(self, tmp):
        stub = os.path.join(tmp, "bin")
        os.makedirs(stub, exist_ok=True)
        _write_exec(os.path.join(stub, "systemctl"), """
            echo "$*" >> "$CALLS"
        """)
        env = dict(os.environ, PATH=f"{stub}:{os.environ['PATH']}", CALLS=os.path.join(tmp, "calls"),
                   SHUTDOWN_GUARD_SCRIPT=os.path.join(tmp, "lib", "shutdown_guard.sh"),
                   SHUTDOWN_GUARD_UNIT_FILE=os.path.join(tmp, "versa-agi-shutdown-guard.service"))
        script = f'source "{GUARD_LIB}"\ninstall_shutdown_guard "{CORE_INFRA}"\n'
        return subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True, timeout=30).returncode

    def test_the_unit_works_at_stop_and_outlives_what_agents_need(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(self.install(tmp), 0, "first install reports a change")
            unit = _read(os.path.join(tmp, "versa-agi-shutdown-guard.service"))
            self.assertIn("Type=oneshot\n", unit)
            self.assertIn("RemainAfterExit=yes\n", unit)
            self.assertIn(f"ExecStop={os.path.join(tmp, 'lib', 'shutdown_guard.sh')}\n", unit)
            self.assertIn("ExecStart=/bin/rm -f /var/lib/versa-agi/shutdown_pending\n", unit)
            self.assertIn("TimeoutStopSec=360\n", unit)
            after = next(line for line in unit.splitlines() if line.startswith("After="))
            # Stopped before these, so they are still up while agents hand off.
            for needed in ("network-online.target", "NetworkManager.service", "systemd-resolved.service",
                           "cron.service", "versa-agi-link.service", "versa-agi-tunnel.service", "ollama.service"):
                self.assertIn(needed, after)
            self.assertEqual(_read(os.path.join(tmp, "lib", "shutdown_guard.sh")), _read(GUARD))
            self.assertTrue(os.access(os.path.join(tmp, "lib", "shutdown_guard.sh"), os.X_OK))
            self.assertEqual(self.install(tmp), 1, "a second install changes nothing")
            self.assertEqual(set(_read(os.path.join(tmp, "calls")).split()), {"daemon-reload"})

    def test_setup_starts_it_and_never_restarts_it(self):
        setup = _read(SETUP)
        block = setup[setup.index("# ─── Step 11c: Shutdown Guard"):setup.index("# ─── System Permissions Restabilization")]
        self.assertIn('source "${SRC_CORE_INFRA}/scripts/shutdown_guard_service.sh"', block)
        self.assertIn("if [ -d /run/systemd/system ]; then", block)
        self.assertIn("systemctl enable versa-agi-shutdown-guard", block)
        self.assertIn("systemctl start versa-agi-shutdown-guard", block)
        # A restart would run ExecStop.
        self.assertNotIn("restart versa-agi-shutdown-guard", setup)

    def test_uninstall_and_backup_know_the_unit(self):
        self.assertIn("for _unit in versa-agi-link versa-agi-shutdown-guard; do", _read(os.path.join(SRC, "uninstall.sh")))
        backup = _read(os.path.join(SRC, "backup.sh"))
        self.assertIn("/etc/systemd/system/versa-agi-shutdown-guard.service", backup)
        self.assertIn("/etc/systemd/system/versa-agi-link.service", backup)

    def test_the_setting_is_in_both_settings_files(self):
        self.assertRegex(_read(os.path.join(SRC, "setup.ini")), r"\nshutdown_grace_seconds=\d+\n")
        # The stock file is what a fresh install gets and what --update adds missing keys from.
        self.assertIn("\nshutdown_grace_seconds=120\n", _read(os.path.join(SRC, "setup.ini.stock")))


class TestShutdownHold(unittest.TestCase):
    """Lifeline's hold block, run on its own with a fake uptime and flag (§1.4.1 rule 4)."""

    @classmethod
    def setUpClass(cls):
        text = _read(LIFELINE)
        start = text.index("# ─── Shutdown hold")
        cls.block = text[start:text.index("# ─── End shutdown hold", start)]
        cls.text = text

    def _run(self, flag_age: int | None, uptime: int = 5000, ide: bool = False):
        tmp = tempfile.mkdtemp()
        uptime_file = os.path.join(tmp, "uptime")
        flag = os.path.join(tmp, "shutdown_pending")
        with open(uptime_file, "w", encoding="utf-8") as handle:
            handle.write(f"{uptime}.42 100.00\n")
        if flag_age is not None:
            open(flag, "w").close()
            import time
            stamp = time.time() - flag_age
            os.utime(flag, (stamp, stamp))
        script = (
            "set -euo pipefail\nlog() { echo \"LOG $*\"; }\n"
            f"IDE_GENERATE={'true' if ide else 'false'}\n{self.block}\necho CONTINUED\n"
        )
        env = {**os.environ, "VERSA_UPTIME_FILE": uptime_file, "VERSA_SHUTDOWN_FLAG": flag}
        result = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout, os.path.exists(flag)

    def test_a_flag_from_this_boot_stops_the_run(self):
        out, kept = self._run(flag_age=30)
        self.assertIn("LOG SHUTDOWN: this computer is shutting down", out)
        self.assertNotIn("CONTINUED", out)
        self.assertTrue(kept)

    def test_a_flag_from_before_the_boot_is_dropped(self):
        out, kept = self._run(flag_age=9000, uptime=600)
        self.assertEqual(out.strip(), "CONTINUED")
        self.assertFalse(kept)

    def test_no_flag_and_ide_prompt_runs_continue(self):
        self.assertEqual(self._run(flag_age=None)[0].strip(), "CONTINUED")
        out, kept = self._run(flag_age=30, ide=True)
        self.assertEqual(out.strip(), "CONTINUED")
        self.assertTrue(kept)

    def test_the_hold_comes_before_any_work(self):
        self.assertLess(self.text.index("# ─── Shutdown hold"), self.text.index("# ─── Cycle Monitor"))
        self.assertLess(self.text.index("Lifeline disabled ("), self.text.index("# ─── Shutdown hold"))


class TestProofOfLifeFetch(unittest.TestCase):
    """The spawn's inbox poll, run with a fake clock: which passes name the cycle (§1.4.2 rule 2)."""

    @classmethod
    def setUpClass(cls):
        text = _read(LIFELINE)
        start = text.index('PROOF_OF_LIFE_SECONDS="${VERSA_PROOF_OF_LIFE_SECONDS:-55}"')
        cls.block = text[start:text.index("_vv_run_instance_sync() {", start)]

    def _run(self, passes: int, link_up: bool = False, cycle: str = "coa-7", fail_on: tuple = ()):
        """Returns, per fetch, the fake-clock second and the cycle id it named ("-" for none)."""
        script = f"""
            set -uo pipefail
            NOW=1000; PASS=0; FETCH=0
            date() {{ echo "$NOW"; }}
            sleep() {{ NOW=$(( NOW + $1 )); PASS=$(( PASS + 1 )); [ "$PASS" -gt {passes} ] && exit 0; return 0; }}
            _vv_link_up() {{ {'true' if link_up else 'false'}; }}
            _vv_sync_agent_inbox() {{
              FETCH=$(( FETCH + 1 ))
              echo "$NOW ${{7:--}}"
              case " {' '.join(str(n) for n in fail_on)} " in *" $FETCH "*) _VV_INBOX_FETCHED=false ;; *) _VV_INBOX_FETCHED=true ;; esac
            }}
            {self.block}
            _vv_inbox_while_spawned agi-coa coa /w sub_coa tok "{cycle}"
        """
        out = subprocess.run(["bash", "-c", textwrap.dedent(script)], capture_output=True, text=True, timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        return [tuple(line.split()) for line in out.stdout.splitlines()]

    def test_link_down_every_pass_fetches_and_one_a_minute_is_the_proof(self):
        fetches = self._run(passes=13)
        self.assertEqual(len(fetches), 13)
        self.assertEqual([at for at, named in fetches if named == "coa-7"], ["1010", "1070", "1130"])
        self.assertTrue(all(named in ("coa-7", "-") for _, named in fetches))

    def test_link_up_the_one_fetch_a_minute_is_always_the_proof(self):
        fetches = self._run(passes=18, link_up=True)
        self.assertEqual(fetches, [("1060", "coa-7"), ("1120", "coa-7"), ("1180", "coa-7")])

    def test_a_fetch_that_got_no_answer_is_tried_again_on_the_next_pass(self):
        fetches = self._run(passes=4, fail_on=(1, 2))
        self.assertEqual(fetches, [("1010", "coa-7"), ("1020", "coa-7"), ("1030", "coa-7"), ("1040", "-")])

    def test_without_a_cycle_id_no_fetch_is_a_proof(self):
        self.assertEqual({named for _, named in self._run(passes=8, cycle="")}, {"-"})

    def test_the_proof_stays_well_inside_the_three_minutes_versavoice_waits(self):
        text = _read(LIFELINE)
        self.assertIn('PROOF_OF_LIFE_SECONDS="${VERSA_PROOF_OF_LIFE_SECONDS:-55}"', text)
        # Link up: one fetch in six passes of 10 s. Two missed proofs still leave it under 180 s.
        self.assertIn('if _vv_link_up && [ "${quiet}" -lt 5 ]; then', text)



def _shell_function(text: str, name: str) -> str:
    start = text.index(f"{name}() {{")
    return text[start:text.index("\n}\n", start) + 3]


class TestSleepHoldInLifeline(unittest.TestCase):
    """_sleep_hold_start with a stand-in for systemd-inhibit (§1.6.1)."""

    def _run(self, ini="", inhibit="ok"):
        """``inhibit``: ok, refused, or missing. Returns (stdout, log, the stand-in's calls)."""
        tmp = tempfile.mkdtemp()
        calls = os.path.join(tmp, "calls")
        open(calls, "w").close()
        fake = os.path.join(tmp, "systemd-inhibit")
        if inhibit != "missing":
            _write_exec(fake, f"""
                echo "$*" >> {calls}
                exit {0 if inhibit == "ok" else 1}
            """)
        ini_path = os.path.join(tmp, "setup.ini")
        with open(ini_path, "w", encoding="utf-8") as handle:
            handle.write("[agent]\nshutdown_grace_seconds=120\n" + ini)
        script = (
            "set -uo pipefail\nlog() { echo \"LOG $*\" >&2; }\n"
            f"SETUP_INI={ini_path}\nAGENT_NAME=coa\nSLEEP_HOLD_BIN={fake}\n"
            + _shell_function(_read(LIFELINE), "_sleep_hold_start")
            # The hold is started in the background: give its stand-in a moment to record its call.
            + "pid=$(_sleep_hold_start 4242)\nsleep 0.4\necho \"PID=${pid}\"\n"
        )
        out = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        return out.stdout, out.stderr, _read(calls).splitlines()

    def test_the_hold_follows_the_spawn_and_names_itself_plainly(self):
        out, log, calls = self._run()
        self.assertRegex(out, r"PID=\d+")
        self.assertEqual(log, "")
        self.assertEqual(calls, [
            "--what=sleep --mode=block --who=Versa AGi --why=An agent is working true",
            "--what=sleep --mode=block --who=Versa AGi --why=An agent is working tail --pid=4242 -f /dev/null",
        ])

    def test_a_refused_hold_never_stops_the_cycle(self):
        out, log, calls = self._run(inhibit="refused")
        self.assertEqual(out.strip(), "PID=")
        self.assertIn("LOG SLEEP HOLD: coa — not taken", log)
        self.assertEqual(len(calls), 1, "only the try, no hold")

    def test_no_systemd_inhibit_and_the_setting_off_are_silent(self):
        for kwargs in ({"inhibit": "missing"}, {"ini": "sleep_hold=false\n"}):
            out, log, calls = self._run(**kwargs)
            self.assertEqual((out.strip(), log, calls), ("PID=", "", []), kwargs)

    def test_the_hold_runs_beside_the_cycle_not_around_it(self):
        """Around it, a child ended by a signal would come back as exit code 1,
        which counts toward the circuit breaker (checked 2026-11-19)."""
        text = _read(LIFELINE)
        marker = text.index('echo "${CURRENT_CYCLE_ID:-} ${SPAWN_EPOCH}" > "${ACTIVITY_OPEN_MARKER}"')
        start = text.index('SLEEP_HOLD_PID=$(_sleep_hold_start "${BASHPID}")')
        spawn = text.index('sudo -u "${AGENT_USER}" \\\n    timeout "${TIMEOUT_DURATION}"')
        stop = text.index('kill "${SLEEP_HOLD_PID}" 2>/dev/null || true')
        end = text.index("_vv_activity end --outcome", stop)
        self.assertLess(marker, start)
        self.assertLess(start, spawn)
        self.assertLess(spawn, stop)
        self.assertLess(stop, end)
        self.assertNotIn("systemd-inhibit", text[spawn:stop])


class TestCutCycleCheckpoint(unittest.TestCase):
    """_cut_cycle_checkpoint with stand-ins for agictl, sqlite3 and the activity report (§1.6.3)."""

    def _run(self, marker=None, alive_ago=0, uptime=600, state="closed"):
        import time
        tmp = tempfile.mkdtemp()
        os.makedirs(os.path.join(tmp, "coa"))
        marker_path = os.path.join(tmp, "coa", "activity_open")
        now = int(time.time())
        if marker is not None:
            with open(marker_path, "w", encoding="utf-8") as handle:
                handle.write(marker.replace("SPAWN", str(now - alive_ago - 300)))
            os.utime(marker_path, (now - alive_ago, now - alive_ago))
        uptime_file = os.path.join(tmp, "uptime")
        with open(uptime_file, "w", encoding="utf-8") as handle:
            handle.write(f"{uptime}.11 9.9\n")
        block = _shell_function(_read(LIFELINE), "_cut_cycle_checkpoint").replace(
            "/usr/local/bin/agictl", f"_agictl")
        script = (
            "set -euo pipefail\nlog() { echo \"LOG $*\"; }\n"
            f"_agictl() {{ echo \"AGICTL $*\" >> {tmp}/calls; echo '{{\"state\": \"{state}\"}}'; }}\n"
            f"sqlite3() {{ echo \"SQLITE $2\" >> {tmp}/calls; }}\n"
            f"_vv_activity() {{ echo \"ACTIVITY cycle=${{CURRENT_CYCLE_ID}} $*\" >> {tmp}/calls; echo 0; }}\n"
            "AGENT_NAME=coa\nAGENTS_DB=/x/agents.db\nSUB_ACCOUNT_ID=sub\nAPI_TOKEN=tok\n"
            f"touch {tmp}/calls\n{block}\n_cut_cycle_checkpoint\necho \"AFTER cycle=${{CURRENT_CYCLE_ID:-unset}}\"\n"
        )
        env = {**os.environ, "VERSA_STATE_DIR": tmp, "VERSA_UPTIME_FILE": uptime_file}
        out = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        return out.stdout, _read(os.path.join(tmp, "calls")), os.path.exists(marker_path), now - alive_ago

    def test_no_marker_nothing_happens(self):
        out, calls, _, _ = self._run()
        self.assertEqual((out.strip(), calls), ("AFTER cycle=unset", ""))

    def test_a_cycle_cut_by_the_computer_going_off(self):
        # Last alive an hour ago; the machine has been up ten minutes.
        out, calls, left, alive = self._run(marker="coa-77 SPAWN\n", alive_ago=3600, uptime=600)
        self.assertIn(f"AGICTL cycle close-cut coa --cycle-id coa-77 --ended-epoch {alive} --exit-code 98", calls)
        self.assertIn("SQLITE UPDATE agents SET status='idle'", calls)
        self.assertIn("WHERE name='coa' AND status='active'", calls)
        self.assertIn(f"ACTIVITY cycle=coa-77 end --outcome shutdown --duration-seconds 300 --ended-epoch {alive}", calls)
        self.assertFalse(left, "the marker is removed")
        self.assertIn("LOG CUT: coa — cycle coa-77 was cut off at", out)
        self.assertIn("(the computer went off)", out)
        self.assertIn("AFTER cycle=unset", out, "the cut cycle's id does not leak into this pass")

    def test_a_cycle_cut_while_the_computer_stayed_on(self):
        out, calls, _, alive = self._run(marker="coa-77 SPAWN\n", alive_ago=120, uptime=90000)
        self.assertIn("--exit-code 96", calls)
        self.assertIn("end --outcome crash", calls)
        self.assertIn("(Lifeline was stopped)", out)

    def test_a_cycle_the_agent_had_closed_is_a_normal_end(self):
        _, calls, left, _ = self._run(marker="coa-77 SPAWN\n", alive_ago=3600, uptime=600, state="already_ended")
        self.assertIn("end --outcome ok", calls)
        self.assertFalse(left)

    def test_an_empty_marker_is_dropped_without_a_report(self):
        out, calls, left, _ = self._run(marker="\n", alive_ago=3600)
        self.assertEqual(calls, "")
        self.assertFalse(left)
        self.assertNotIn("CUT:", out)

    def test_it_runs_before_mail_and_before_any_spawn_and_not_for_an_ide_prompt(self):
        text = _read(LIFELINE)
        call = text.index('  if [ "${IDE_GENERATE}" != "true" ]; then\n    _cut_cycle_checkpoint\n  fi')
        self.assertLess(text.index("# Secondary guard: check if a harness process is already running"), call)
        self.assertLess(call, text.index("# Step 1: Fetch inbox"))
        self.assertLess(call, text.index("# ─── Startup settle — before work detection"))

    def test_neither_cut_code_counts_toward_the_circuit_breaker(self):
        self.assertIn("exit_code IN (1, 42, 55, 97, 99)", _read(LIFELINE))


class TestMonitorAliveAndSleep(unittest.TestCase):
    """The cycle monitor keeps the marker's time current and does not read a sleep as a stall."""

    def _start(self, stall_limit=0, with_alive=True):
        tmp = tempfile.mkdtemp()
        paths = {n: os.path.join(tmp, n) for n in ("result", "heartbeat", "log", "alive", "clock")}
        name = f"t-{os.path.basename(tmp)}"
        open(paths["result"], "w").close()
        open(paths["heartbeat"], "w").close()
        os.utime(paths["heartbeat"], (5000, 5000))
        with open(paths["clock"], "w") as handle:
            handle.write("5000")
        if with_alive:
            open(paths["alive"], "w").close()
            os.utime(paths["alive"], (1000, 1000))
        script = (
            f"log() {{ echo \"LOG $*\" >> '{paths['log']}'; }}\n"
            f"pkill() {{ echo \"PKILL $*\" >> '{paths['log']}'; }}\n"
            f"date() {{ if [ \"${{1:-}}\" = \"+%s\" ]; then cat '{paths['clock']}'; else command date \"$@\"; fi; }}\n"
            + _shell_function(_read(LIFELINE), "monitor_runaway")
            + f"monitor_runaway '{paths['result']}' agi-x '{name}' 100000 2048 "
              f"'{paths['heartbeat']}' {stall_limit} '{paths['alive']}'\n"
        )
        env = dict(os.environ, VERSA_MONITOR_POLL_SECONDS="0.05", VERSA_MONITOR_SAMPLE_POLLS="2")
        proc = subprocess.Popen(["bash", "-c", script], env=env)
        stall = f"/tmp/versa-agi-stall-{name}"

        def close():
            if proc.poll() is None:
                proc.kill()
            proc.wait()
            for path in (stall, f"/tmp/versa-agi-runaway-{name}"):
                if os.path.exists(path):
                    os.remove(path)
        self.addCleanup(close)
        return paths, stall

    def _set_clock(self, paths, value):
        tmp = paths["clock"] + ".new"
        with open(tmp, "w") as handle:
            handle.write(str(value))
        os.replace(tmp, paths["clock"])

    def _wait(self, check, seconds=4.0):
        import time
        end = time.time() + seconds
        while time.time() < end:
            if check():
                return True
            time.sleep(0.05)
        return False

    def test_the_marker_is_touched_on_every_poll_and_never_created(self):
        paths, _ = self._start()
        self.assertTrue(self._wait(lambda: os.stat(paths["alive"]).st_mtime > 1000))
        paths, _ = self._start(with_alive=False)
        import time
        time.sleep(0.5)
        self.assertFalse(os.path.exists(paths["alive"]))

    def test_time_asleep_is_not_a_stall_and_the_timer_starts_again_at_wake(self):
        import time
        paths, stall = self._start(stall_limit=100)
        time.sleep(0.3)
        self.assertFalse(os.path.exists(stall))
        self._set_clock(paths, 9000)   # an hour and more between two polls: the machine slept
        self.assertTrue(self._wait(lambda: os.path.exists(paths["log"]) and "the machine slept" in _read(paths["log"])))
        time.sleep(0.3)
        self.assertFalse(os.path.exists(stall), "4,000 s without a heartbeat, but asleep")
        self._set_clock(paths, 9050)   # 50 s after wake, still quiet: inside the limit
        time.sleep(0.3)
        self.assertFalse(os.path.exists(stall))
        self._set_clock(paths, 9120)   # 120 s after wake with no heartbeat: a stall
        self.assertTrue(self._wait(lambda: os.path.exists(stall)))
        self.assertIn("no heartbeat for 120s (limit 100s)", _read(stall))


class TestSleepHoldSetup(unittest.TestCase):
    """scripts/sleep_hold.sh: laptop detection, the permission rule, the lid setting (§1.6.1, §1.6.2)."""

    def _sh(self, body, tmp, chassis="desktop", lid=False):
        stub = os.path.join(tmp, "bin")
        os.makedirs(stub, exist_ok=True)
        _write_exec(os.path.join(stub, "systemctl"), """
            echo "$*" >> "$CALLS"
        """)
        _write_exec(os.path.join(stub, "hostnamectl"), f"""
            echo "{chassis}"
        """)
        lid_dir = os.path.join(tmp, "lid")
        os.makedirs(lid_dir, exist_ok=True)
        if lid:
            os.makedirs(os.path.join(lid_dir, "LID0"), exist_ok=True)
        env = dict(os.environ, PATH=f"{stub}:{os.environ['PATH']}", CALLS=os.path.join(tmp, "calls"),
                   VERSA_HOSTNAMECTL_BIN=os.path.join(stub, "hostnamectl"), VERSA_LID_DIR=lid_dir,
                   SLEEP_HOLD_RULE_FILE=os.path.join(tmp, "rules.d", "50-versa-agi-sleep.rules"),
                   LID_DROPIN_FILE=os.path.join(tmp, "logind.conf.d", "50-versa-agi-lid.conf"))
        return subprocess.run(["bash", "-c", f'source "{SLEEP_LIB}"\n{body}\n'], env=env,
                              capture_output=True, text=True, timeout=30)

    def test_laptop_detection(self):
        with tempfile.TemporaryDirectory() as tmp:
            for chassis, lid, expected in (("laptop", False, 0), ("convertible", False, 0), ("tablet", False, 0),
                                           ("desktop", False, 1), ("server", False, 1), ("vm", False, 1),
                                           ("", False, 1), ("desktop", True, 0)):
                with tempfile.TemporaryDirectory() as each:
                    result = self._sh("machine_is_laptop", each, chassis=chassis, lid=lid)
                    self.assertEqual(result.returncode, expected, (chassis, lid))

    def test_the_rule_names_the_watchdog_account_and_one_action(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "rules.d"))
            rule = os.path.join(tmp, "rules.d", "50-versa-agi-sleep.rules")
            self.assertEqual(self._sh("install_sleep_hold_rule watchdog true", tmp).returncode, 0)
            text = _read(rule)
            self.assertIn('action.id == "org.freedesktop.login1.inhibit-block-sleep"', text)
            self.assertIn('subject.user == "watchdog"', text)
            self.assertIn("return polkit.Result.YES;", text)
            self.assertEqual(text.count("polkit.addRule"), 1)
            self.assertEqual(self._sh("install_sleep_hold_rule watchdog true", tmp).returncode, 1, "already current")
            self.assertEqual(self._sh("install_sleep_hold_rule watchdog false", tmp).returncode, 0)
            self.assertFalse(os.path.exists(rule))
            self.assertEqual(self._sh("install_sleep_hold_rule watchdog false", tmp).returncode, 1)

    def test_no_polkit_rules_folder_is_reported_not_created(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(self._sh("install_sleep_hold_rule watchdog true", tmp).returncode, 2)
            self.assertFalse(os.path.exists(os.path.join(tmp, "rules.d")))

    def test_the_lid_setting_is_written_and_removed_and_logind_is_reloaded_never_restarted(self):
        with tempfile.TemporaryDirectory() as tmp:
            dropin = os.path.join(tmp, "logind.conf.d", "50-versa-agi-lid.conf")
            self.assertEqual(self._sh("apply_lid_setting false", tmp).returncode, 1, "nothing to remove")
            self.assertFalse(os.path.exists(os.path.join(tmp, "calls")))
            self.assertEqual(self._sh("apply_lid_setting true", tmp).returncode, 0)
            self.assertIn("[Login]\nLidSwitchIgnoreInhibited=no\n", _read(dropin))
            self.assertEqual(self._sh("apply_lid_setting true", tmp).returncode, 1)
            self.assertEqual(self._sh("apply_lid_setting false", tmp).returncode, 0)
            self.assertFalse(os.path.exists(dropin))
            calls = _read(os.path.join(tmp, "calls")).splitlines()
            self.assertEqual(calls, ["reload systemd-logind", "reload systemd-logind"])

    def test_setup_step_applies_the_rule_and_asks_the_lid_only_on_a_laptop(self):
        setup = _read(SETUP)
        block = setup[setup.index("# ─── Step 11d: Sleep Hold"):setup.index("# ─── System Permissions Restabilization")]
        self.assertIn('source "${SRC_CORE_INFRA}/scripts/sleep_hold.sh"', block)
        self.assertIn('install_sleep_hold_rule "${WATCHDOG_USER}" "${_SLEEP_HOLD}"', block)
        self.assertIn('if machine_is_laptop; then\n    _LID_WAITS="$(ini_get agent lid_waits_for_agent false)"', block)
        self.assertIn('[ "${_SLEEP_HOLD}" = "true" ] || _LID_WAITS="false"', block)
        self.assertNotIn("restart systemd-logind", setup + _read(SLEEP_LIB))

    def test_setup_says_laptop_detected_then_asks_default_no(self):
        text = _read(os.path.join(CORE_INFRA, "install_acceptance.sh"))
        start = text.index("# The lid (state_host_services.md §1.6.2)")
        block = text[start:text.index("\n}\n", start)]
        self.assertLess(block.index("machine_is_laptop; then"), block.index('info "Laptop detected."'))
        self.assertLess(block.index('info "Laptop detected."'),
                        block.index('"Keep the laptop awake with the lid shut while an agent works?" "${lid_current}"'))
        self.assertIn('local lid_current="false"', block)
        self.assertIn("_install_acceptance_feature_supplied VERSA_FEATURE_LID_WAITS", block)
        self.assertIn('else\n    export VERSA_FEATURE_LID_WAITS="false"', block)
        self.assertIn('_install_acceptance_section_set agent lid_waits_for_agent '
                      '"$(_install_acceptance_norm_bool "${VERSA_FEATURE_LID_WAITS:-false}")"', text)

    def test_settings_uninstall_and_backup(self):
        # The stock file carries the defaults. setup.ini is this site's own file: setup
        # writes the owner's answers back into it, so only the keys are checked there.
        stock = _read(os.path.join(SRC, "setup.ini.stock"))
        self.assertIn("\nsleep_hold=true\n", stock)
        self.assertIn("\nlid_waits_for_agent=false\n", stock)
        site = _read(os.path.join(SRC, "setup.ini"))
        self.assertRegex(site, r"\nsleep_hold=(true|false)\n")
        self.assertRegex(site, r"\nlid_waits_for_agent=(true|false)\n")
        uninstall = _read(os.path.join(SRC, "uninstall.sh"))
        backup = _read(os.path.join(SRC, "backup.sh"))
        for path in ("/etc/polkit-1/rules.d/50-versa-agi-sleep.rules", "/etc/systemd/logind.conf.d/50-versa-agi-lid.conf"):
            self.assertIn(path, uninstall)
            self.assertIn(path, backup)



class TestCheckpointPrune(unittest.TestCase):
    """Lifeline's checkpoint prune, run on a real WAL database (found 2026-11-19: it never ran)."""

    SCHEMA = """
        CREATE TABLE checkpoints (thread_id TEXT NOT NULL, checkpoint_ns TEXT NOT NULL DEFAULT '',
            checkpoint_id TEXT NOT NULL, parent_checkpoint_id TEXT, type TEXT, checkpoint BLOB, metadata BLOB,
            PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id));
        CREATE TABLE writes (thread_id TEXT NOT NULL, checkpoint_ns TEXT NOT NULL DEFAULT '',
            checkpoint_id TEXT NOT NULL, task_id TEXT NOT NULL, idx INTEGER NOT NULL, channel TEXT NOT NULL,
            type TEXT, value BLOB, PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id, task_id, idx));
    """

    @classmethod
    def setUpClass(cls):
        text = _read(LIFELINE)
        start = text.index("  # ─── Checkpoint DB Pruning")
        cls.block = text[start:text.index("  # Release lock (fd 200 closes when subshell exits)", start)]

    def _db(self, tmp):
        import sqlite3
        path = os.path.join(tmp, "checkpoints.db")
        conn = sqlite3.connect(path)
        conn.executescript("PRAGMA journal_mode=WAL;" + self.SCHEMA)
        for n in range(120):                      # the live thread: 120 checkpoints, two writes each
            conn.execute("INSERT INTO checkpoints (thread_id, checkpoint_id, checkpoint) VALUES ('1-19', ?, ?)",
                         (f"c{n:04d}", b"x" * 2000))
            for idx in (0, 1):
                conn.execute("INSERT INTO writes VALUES ('1-19', '', ?, 't', ?, 'messages', NULL, ?)",
                             (f"c{n:04d}", idx, b"y" * 2000))
        # An old thread with one checkpoint left and the writes of checkpoints that are gone.
        conn.execute("INSERT INTO checkpoints (thread_id, checkpoint_id, checkpoint) VALUES ('1-5', 'keep', x'00')")
        conn.execute("INSERT INTO writes VALUES ('1-5', '', 'keep', 't', 0, 'messages', NULL, x'00')")
        for n in range(30):
            conn.execute("INSERT INTO writes VALUES ('1-5', '', ?, 't', 0, 'messages', NULL, ?)",
                         (f"gone{n}", b"z" * 2000))
        conn.commit()
        conn.close()                              # last connection closed: no -wal, no -shm
        return path

    def _run(self, db, sudo="sudo() { shift 2; \"$@\"; }"):
        script = (
            "set -uo pipefail\nlog() { echo \"LOG $*\"; }\n" + sudo + "\n"
            "AGENT_NAME=coa\nAGENT_USER=coa\n"
            + self.block.replace('CHECKPOINT_DB="/var/lib/versa-agi/${AGENT_NAME}/cycles/checkpoints.db"',
                                 f'CHECKPOINT_DB="{db}"')
        )
        out = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        return out.stdout

    def _counts(self, db):
        import sqlite3
        conn = sqlite3.connect(db)
        rows = {
            "live": conn.execute("SELECT COUNT(*) FROM checkpoints WHERE thread_id='1-19'").fetchone()[0],
            "live_writes": conn.execute("SELECT COUNT(*) FROM writes WHERE thread_id='1-19'").fetchone()[0],
            "old": conn.execute("SELECT COUNT(*) FROM checkpoints WHERE thread_id='1-5'").fetchone()[0],
            "old_writes": conn.execute("SELECT COUNT(*) FROM writes WHERE thread_id='1-5'").fetchone()[0],
            "newest": conn.execute("SELECT MAX(checkpoint_id) FROM checkpoints WHERE thread_id='1-19'").fetchone()[0],
        }
        conn.close()
        return rows

    def test_it_keeps_the_newest_fifty_and_clears_leftover_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = self._db(tmp)
            before = os.path.getsize(db)
            out = self._run(db)
            self.assertEqual(self._counts(db), {"live": 50, "live_writes": 100, "old": 1, "old_writes": 1,
                                                "newest": "c0119"})
            self.assertIn("LOG CHECKPOINT PRUNE: coa — removed 70 old rows and 30 leftover writes, kept last 50 per thread", out)
            self.assertLess(os.path.getsize(db), before, "the space is given back")
            self.assertEqual(self._run(db), "", "nothing to do the second time, and no log line")

    def test_every_statement_runs_as_the_agent_user(self):
        """Read as watchdog, a WAL database nobody has open cannot be opened at all:
        the count came back empty and the prune was skipped without a word."""
        self.assertNotIn('$(sqlite3 "${CHECKPOINT_DB}"', self.block)
        self.assertIn('sudo -u "${AGENT_USER}" sqlite3 "${CHECKPOINT_DB}" "$1" 2>/dev/null', self.block)
        self.assertNotIn('$(sqlite3 "${CHECKPOINT_DB}"', _read(LIFELINE))

    def test_a_database_that_cannot_be_read_is_logged_not_skipped_in_silence(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = self._db(tmp)
            out = self._run(db, sudo="sudo() { return 8; }")
            self.assertIn("LOG WARN: checkpoint prune skipped for coa", out)
            self.assertEqual(self._counts(db)["live"], 120)

    def test_the_mechanism_a_reader_without_write_access_cannot_open_a_closed_wal_database(self):
        if os.geteuid() == 0:
            self.skipTest("root can always write")
        with tempfile.TemporaryDirectory() as tmp:
            db = self._db(tmp)
            os.chmod(db, 0o444)
            os.chmod(tmp, 0o555)
            try:
                result = subprocess.run(["sqlite3", db, "SELECT COUNT(*) FROM checkpoints;"],
                                        capture_output=True, text=True)
            finally:
                os.chmod(tmp, 0o755)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("readonly database", result.stderr)


if __name__ == "__main__":
    unittest.main()
