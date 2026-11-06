"""Host services setup installs: SSH tunnel retry schedule and log rotation.

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


if __name__ == "__main__":
    unittest.main()
