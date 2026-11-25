"""The link service and what it relies on (state_realtime_link.md §3.1, RL-03 to RL-08).

Run from core-infra:
  python -m unittest harness.tests.test_link_service
"""

from __future__ import annotations

import http.server
import importlib.util
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest

from harness.listening import ListeningWindow, MailSignal, wait_for_mail

CORE_INFRA = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SRC = os.path.dirname(CORE_INFRA)
LIFELINE = os.path.join(CORE_INFRA, "lifeline.sh")
RUNNER = os.path.join(CORE_INFRA, "scripts", "versa_agi_link.py")
INSTALLER = os.path.join(CORE_INFRA, "scripts", "link_service.sh")

_spec = importlib.util.spec_from_file_location("versa_agi_link", RUNNER)
link = importlib.util.module_from_spec(_spec)
sys.modules["versa_agi_link"] = link  # dataclasses look the module up while it loads
_spec.loader.exec_module(link)

COA = link.Agent("coa", "agi-coa", "/home/agi-coa/env", "sub-coa", "tok")
AMY = link.Agent("amy", "agi-amy", "/home/agi-amy/env", "sub-amy", "tok")


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def _shell_function(name: str) -> str:
    text = _read(LIFELINE)
    start = text.index(f"\n{name}() {{\n") + 1
    return text[start:text.index("\n}\n", start) + 3]


class Recorder:
    """Stand-ins for everything the service touches outside itself."""

    def __init__(self, awake=(), answers=()):
        self.awake = set(awake)
        self.answers = list(answers)
        self.calls: list[tuple] = []
        self.urls: list[str] = []
        self.states: list[tuple] = []
        self.logs: list[str] = []
        self.now = 1000.0
        self.wakes_spawn = True

    def fetch(self, url, token, timeout):
        self.urls.append(url)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    def running(self, agent):
        return agent.name in self.awake

    def wake(self, agent, lifeline):
        self.calls.append(("wake", agent.name, lifeline))
        if self.wakes_spawn:
            self.awake.add(agent.name)

    def sync(self, agent):
        self.calls.append(("sync", agent.name))

    def touch(self, agent):
        self.calls.append(("touch", agent.name))

    def service(self):
        return link.LinkService(
            "/core/lifeline.sh", fetch=self.fetch, running=self.running, wake=self.wake, sync=self.sync,
            touch=self.touch, state=lambda *a: self.states.append(a), log=self.logs.append,
            clock=lambda: self.now)


def _wait_idle(service, seconds=5.0):
    end = time.monotonic() + seconds
    while not service.deliveries.idle():
        if time.monotonic() > end:
            raise AssertionError("deliveries did not finish")
        time.sleep(0.01)


class TestAddressAndRetry(unittest.TestCase):
    def test_the_service_uses_the_one_cloud_address(self):
        base = re.search(r'^VV_API_BASE = "([^"]+)"', _read(os.path.join(CORE_INFRA, "agictl", "comms.py")), re.M)
        self.assertEqual(link.API_BASE, base.group(1))
        self.assertTrue(link.API_BASE.startswith("https://"))
        # The address is a constant: no setting or environment variable replaces it.
        self.assertNotRegex(_read(RUNNER), r"environ[^\n]*API_BASE|API_BASE\s*=\s*os\.")

    def test_wait_url_names_the_agents_and_the_cursor(self):
        url = link.wait_url(["sub-amy", "sub-coa"], "1795000000.000000001")
        self.assertTrue(url.startswith(link.API_BASE + "/agi/link/wait?"))
        self.assertIn("subAccountIds=sub-amy%2Csub-coa", url)
        self.assertIn("since=1795000000.000000001", url)
        self.assertIn("timeout=45", url)
        self.assertNotIn("since=", link.wait_url(["sub-coa"], ""))

    def test_retry_schedule(self):
        self.assertEqual(link.retry_delay(1, 0), 5)
        self.assertEqual(link.retry_delay(2, 5), 60)
        self.assertEqual(link.retry_delay(20, 29 * 60), 60)
        self.assertEqual(link.retry_delay(40, 31 * 60), 30 * 60)
        for status in (401, 403, 404):
            self.assertEqual(link.retry_delay(1, 0, status), 30 * 60)


class TestAgents(unittest.TestCase):
    def test_only_agents_with_an_account_and_a_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            def config(name, value):
                with open(os.path.join(tmp, f"{name}_config.json"), "w") as handle:
                    json.dump(value, handle)
            config("coa", {"versavoice": {"sub_account_id": "sub-coa", "api_token": "tok"}})
            config("amy", {"versavoice": {"sub_account_id": "", "api_token": "tok"}})
            config("watchdog", {"versavoice": {"sub_account_id": "sub-w", "api_token": "tok"}})
            listing = "coa|agi-coa|/home/agi-coa/env|model|30\namy|agi-amy|/home/agi-amy/env|m|30\n" \
                      "ben|agi-ben|/home/agi-ben/env|m|30\nwatchdog|watchdog|/home/watchdog|m|30\n\n"
            agents = link.parse_active_agents(listing, config_dir=tmp)
        self.assertEqual(agents, [link.Agent("coa", "agi-coa", "/home/agi-coa/env", "sub-coa", "tok")])

    def test_live_link_is_off_unless_switched_on(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "setup.ini")
            for body, expected in (("[versavoice]\nenabled=true\n[features]\nlive_link=true\n", True),
                                   ("[features]\nlive_link=true\n", True),
                                   ("[versavoice]\nenabled=true\n", False),
                                   ("[versavoice]\nenabled=true\nlink=true\n", False),
                                   ("[versavoice]\nenabled=true\n[features]\nlive_link=false\n", False),
                                   ("[versavoice]\nenabled=false\n[features]\nlive_link=true\n", False)):
                with open(path, "w") as handle:
                    handle.write(body)
                self.assertEqual(link.link_enabled(path), expected, body)
            self.assertFalse(link.link_enabled(os.path.join(tmp, "missing.ini")))

    def test_the_stock_setting_is_off_and_setup_asks(self):
        stock = _read(os.path.join(SRC, "setup.ini.stock"))
        self.assertIn("\nlive_link=false\n", stock.split("[features]")[1].split("\n[")[0])
        self.assertNotIn("\nlink=", stock.split("[versavoice]")[1].split("\n[")[0])
        acceptance = _read(os.path.join(CORE_INFRA, "install_acceptance.sh"))
        self.assertIn('"Enable Live Link?" "${link_current}")', acceptance)
        self.assertIn('link_current="$(_install_acceptance_section_get versavoice link false)"', acceptance)
        self.assertIn('_install_acceptance_features_set live_link "$(_install_acceptance_norm_bool "${VERSA_FEATURE_LIVE_LINK:-false}")"',
                      acceptance)

    def test_agitop_has_the_switch(self):
        modal = _read(os.path.join(CORE_INFRA, "agitop", "panels", "system_settings_modal.py"))
        self.assertIn('id="select-live-link"', modal)
        self.assertIn('_write_ini_value_err("features", "live_link", link)', modal)
        self.assertIn('_read_ini_value("features", "live_link", "false")', modal)


class TestWaitOnce(unittest.TestCase):
    def test_an_answer_signals_the_named_agents_and_keeps_the_cursor(self):
        rec = Recorder(awake={"coa", "amy"}, answers=[{
            "cursor": "1795000001.000000000",
            "events": [{"kind": "message", "subAccountId": "sub-coa", "channelId": "c", "particleId": "p1"},
                       {"kind": "message", "subAccountId": "sub-coa", "channelId": "c", "particleId": "p2"},
                       {"kind": "message", "subAccountId": "sub-other"},
                       {"kind": "something-new", "subAccountId": "sub-amy"}]}])
        service = rec.service()
        self.assertEqual(service.wait_once([COA, AMY]), 0.0)
        _wait_idle(service)
        self.assertEqual(service.cursor, "1795000001.000000000")
        self.assertEqual(rec.calls, [("sync", "coa"), ("touch", "coa")])
        self.assertEqual(rec.states, [("up", "2 agents")])
        self.assertIn("subAccountIds=sub-amy%2Csub-coa", rec.urls[0])

    def test_a_quiet_answer_keeps_the_link_up_and_does_nothing(self):
        rec = Recorder(answers=[{"cursor": "5.000000000", "events": []}, {"cursor": "5.000000000", "events": []}])
        service = rec.service()
        service.wait_once([COA])
        service.wait_once([COA])
        self.assertEqual(rec.calls, [])
        self.assertIn("since=5.000000000", rec.urls[1])
        self.assertEqual(rec.logs, ["up — 1 agent(s) on the link"])

    def test_a_failure_marks_the_link_down_and_backs_off(self):
        rec = Recorder(answers=[link.LinkError("no answer from the VersaVoice API (URLError)"),
                                link.LinkError("no answer from the VersaVoice API (URLError)"),
                                {"cursor": "", "events": []}])
        service = rec.service()
        self.assertEqual(service.wait_once([COA]), 5)
        rec.now += 5
        self.assertEqual(service.wait_once([COA]), 60)
        self.assertEqual([s[0] for s in rec.states], ["down", "down"])
        self.assertEqual(len([line for line in rec.logs if line.startswith("down")]), 1)
        self.assertEqual(service.wait_once([COA]), 0.0)
        self.assertEqual(service.failures, 0)
        self.assertEqual(rec.states[-1][0], "up")

    def test_a_cloud_without_the_route_is_asked_again_only_every_half_hour(self):
        rec = Recorder(answers=[link.LinkError("the VersaVoice API answered 404", 404)])
        self.assertEqual(rec.service().wait_once([COA]), 30 * 60)

    def test_a_state_file_that_cannot_be_written_does_not_stop_the_link(self):
        rec = Recorder(answers=[{"cursor": "1.000000000", "events": []}])
        service = rec.service()
        service._state = lambda *a: (_ for _ in ()).throw(OSError("read-only"))
        self.assertEqual(service.wait_once([COA]), 0.0)


class TestDeliver(unittest.TestCase):
    def test_a_sleeping_agent_is_woken_by_a_pass_for_that_agent(self):
        rec = Recorder()
        rec.service().deliver(COA)
        self.assertEqual(rec.calls, [("wake", "coa", "/core/lifeline.sh"), ("sync", "coa"), ("touch", "coa")])

    def test_a_pass_that_did_not_spawn_leaves_the_mail_to_lifeline(self):
        rec = Recorder()
        rec.wakes_spawn = False  # held, cooldown, or settle window
        rec.service().deliver(COA)
        self.assertEqual(rec.calls, [("wake", "coa", "/core/lifeline.sh")])

    def test_an_awake_agent_gets_a_fetch_and_the_mail_signal(self):
        rec = Recorder(awake={"coa"})
        rec.service().deliver(COA)
        self.assertEqual(rec.calls, [("sync", "coa"), ("touch", "coa")])

    def test_a_signal_during_a_delivery_runs_it_once_more(self):
        started, release = threading.Event(), threading.Event()
        runs = []

        def deliver(agent):
            runs.append(agent.name)
            started.set()
            release.wait(5)

        deliveries = link.Deliveries(deliver)
        deliveries.signal(COA)
        self.assertTrue(started.wait(5))
        deliveries.signal(COA)
        deliveries.signal(COA)
        release.set()
        end = time.monotonic() + 5
        while not deliveries.idle() and time.monotonic() < end:
            time.sleep(0.01)
        self.assertEqual(runs, ["coa", "coa"])

    def test_a_failed_delivery_does_not_stop_the_next(self):
        runs = []

        def deliver(agent):
            runs.append(agent.name)
            raise RuntimeError("lifeline timed out")

        original = link.log_line
        link.log_line = lambda message, path="": None
        try:
            deliveries = link.Deliveries(deliver)
            for _ in range(2):
                deliveries.signal(COA)
                end = time.monotonic() + 5
                while not deliveries.idle() and time.monotonic() < end:
                    time.sleep(0.01)
        finally:
            link.log_line = original
        self.assertEqual(runs, ["coa", "coa"])

    def test_the_token_is_not_put_on_a_command_line(self):
        source = _read(RUNNER)
        sync = source[source.index("def sync_agent("):source.index("def touch_mail_signal(")]
        self.assertIn("AGICTL_VV_TOKEN=agent.token", sync)
        self.assertNotIn('"--token"', source)
        cli = _read(os.path.join(CORE_INFRA, "agictl", "cli.py"))
        start = cli.index('@message.command("sync-inbox")')
        self.assertIn('envvar="AGICTL_VV_TOKEN"', cli[start:cli.index("def message_sync_inbox(", start)])


class TestRunLoop(unittest.TestCase):
    def test_switched_off_the_service_idles_and_says_so(self):
        stop = threading.Event()
        states, logs = [], []

        def state(name, detail):
            states.append((name, detail))
            stop.set()

        link.run("/core/lifeline.sh", stop=stop, agents_of=lambda: [COA], enabled=lambda: False,
                 state=state, log=logs.append)
        self.assertEqual(states, [("off", "Live Link is off")])
        self.assertEqual(logs, ["off — Live Link is off"])

    def test_no_agent_with_an_account_is_off_too(self):
        stop = threading.Event()
        states = []
        link.run("/core/lifeline.sh", stop=stop, agents_of=lambda: [], enabled=lambda: True,
                 state=lambda name, detail: (states.append(name), stop.set()), log=lambda message: None)
        self.assertEqual(states, ["off"])

    def test_the_agents_that_share_the_first_token_are_served(self):
        rec = Recorder(answers=[{"cursor": "1.000000000", "events": []}])
        service = rec.service()
        stop = threading.Event()
        served = []
        wait_once = service.wait_once

        def once(agents):
            served.append([agent.name for agent in agents])
            stop.set()
            return wait_once(agents)

        service.wait_once = once
        other = link.Agent("zed", "agi-zed", "/home/agi-zed/env", "sub-zed", "another-token")
        link.run("/core/lifeline.sh", stop=stop, agents_of=lambda: [COA, other, AMY], enabled=lambda: True,
                 service_for=lambda path: service, state=lambda *a: None, log=lambda message: None)
        self.assertEqual(served, [["coa", "amy"]])
        self.assertEqual(rec.states, [("up", "2 agents")])


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        self.server.seen.append((self.path, self.headers.get("Authorization")))
        status, body = self.server.reply
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass


class TestHeldRequest(unittest.TestCase):
    def setUp(self):
        self.server = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
        self.server.seen = []
        self.server.reply = (200, {"success": True, "data": {"events": [], "cursor": "7.000000000"}})
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def test_the_request_carries_the_token_and_returns_the_data(self):
        data = link.fetch_wait(link.wait_url(["sub-coa"], "", base=self.base), "tok", 5)
        self.assertEqual(data, {"events": [], "cursor": "7.000000000"})
        path, auth = self.server.seen[0]
        self.assertTrue(path.startswith("/agi/link/wait?subAccountIds=sub-coa"))
        self.assertEqual(auth, "Bearer tok")

    def test_a_refusal_carries_its_status(self):
        self.server.reply = (404, {"error": "not_found"})
        with self.assertRaises(link.LinkError) as caught:
            link.fetch_wait(link.wait_url(["sub-coa"], "", base=self.base), "tok", 5)
        self.assertEqual(caught.exception.status, 404)

    def test_an_answer_that_is_not_a_success_is_a_failure(self):
        self.server.reply = (200, {"success": False})
        with self.assertRaises(link.LinkError):
            link.fetch_wait(link.wait_url(["sub-coa"], "", base=self.base), "tok", 5)

    def test_no_cloud_is_a_failure_without_a_status(self):
        self.server.shutdown()
        self.server.server_close()
        with self.assertRaises(link.LinkError) as caught:
            link.fetch_wait(link.wait_url(["sub-coa"], "", base=self.base), "tok", 2)
        self.assertEqual(caught.exception.status, 0)


@unittest.skipUnless(shutil.which("bash"), "needs bash")
class TestLinkStateAndLifeline(unittest.TestCase):
    def link_up(self, state_file: str) -> bool:
        script = f'set -euo pipefail\nLINK_STATE_FILE="{state_file}"\n{_shell_function("_vv_link_up")}\n_vv_link_up\n'
        return subprocess.run(["bash", "-c", script], capture_output=True, timeout=30).returncode == 0

    def test_lifeline_reads_the_state_the_service_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "link_state")
            self.assertFalse(self.link_up(path))
            link.write_state("up", "2 agents", path=path)
            self.assertRegex(_read(path), r"^up \d+ 2 agents\n$")
            self.assertTrue(self.link_up(path))
            link.write_state("down", "no answer from the VersaVoice API (URLError)", path=path)
            self.assertFalse(self.link_up(path))
            link.write_state("up", "", path=path)
            stale = time.time() - 300
            os.utime(path, (stale, stale))
            self.assertFalse(self.link_up(path), "an answer older than two minutes is not a live link")

    def test_the_spawned_fetch_runs_one_pass_in_six_while_the_link_is_up(self):
        poll = _shell_function("_vv_inbox_while_spawned")
        self.assertIn('if _vv_link_up && [ "${quiet}" -lt 5 ]; then', poll)
        self.assertLess(poll.index("sleep 10"), poll.index("_vv_link_up"))
        self.assertLess(poll.index("_vv_link_up"), poll.index("_vv_sync_agent_inbox"))

    def test_the_spawn_reports_the_link(self):
        source = _read(LIFELINE)
        self.assertIn("ACTIVITY_LINK_ARGS=(--link)", source)
        self.assertIn('${ACTIVITY_LINK_ARGS[@]+"${ACTIVITY_LINK_ARGS[@]}"}', source)
        cli = _read(os.path.join(CORE_INFRA, "agictl", "cli.py"))
        self.assertIn('body["link"] = True', cli)

    def test_a_pass_for_one_agent_skips_the_minute_work(self):
        source = _read(LIFELINE)
        self.assertIn('if [ "${1:-}" = "--agent" ]; then', source)
        # Only the named agent is processed.
        self.assertIn('if [ -n "${ONLY_AGENT}" ] && [ "${AGENT_NAME}" != "${ONLY_AGENT}" ]; then\n    continue', source)
        # Skill sync, the weekly registration and the tick belong to the minute pass.
        self.assertRegex(source, r'SKILLS_TO_SYNC=""\nif \[ -z "\$\{ONLY_AGENT\}" \]; then')
        self.assertIn('[ -f "${SETUP_INI}" ] && [ -z "${ONLY_AGENT}" ]; then', source)
        self.assertRegex(source, r'_tick_gap=""\nif \[ -z "\$\{ONLY_AGENT\}" \]; then\n  _tick_json=')
        # It waits for a lock the minute pass holds for a moment, and leaves due scripts alone.
        self.assertIn("if ! flock -w 20 200; then", source)
        one_agent = source[source.index('if [ -n "${ONLY_AGENT}" ]; then\n    # One-agent pass'):
                           source.index("elif ! flock -n 200; then")]
        self.assertNotIn("_run_due_utility_and_scripts", one_agent)

    def test_an_unknown_argument_is_refused(self):
        for args in (["--agent"], ["--nonsense"]):
            done = subprocess.run(["bash", LIFELINE, *args], capture_output=True, text=True, timeout=30)
            self.assertEqual(done.returncode, 1, args)
            self.assertIn("usage: lifeline.sh", done.stderr)

    def test_the_harness_is_told_where_its_mail_signal_is(self):
        self.assertIn("export VERSA_MAIL_SIGNAL_FILE='/var/lib/versa-agi/${AGENT_NAME}/mail_signal'", _read(LIFELINE))
        self.assertEqual(link.MAIL_SIGNAL_NAME, "mail_signal")
        harness = _read(os.path.join(CORE_INFRA, "harness", "agent_harness.py"))
        self.assertIn('MailSignal(os.environ.get("VERSA_MAIL_SIGNAL_FILE"))', harness)
        self.assertIn("signal=mail_signal.raised if mail_signal.path else None", harness)


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class TestMailSignal(unittest.TestCase):
    def test_raised_once_per_rewrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.mkdir(os.path.join(tmp, "coa"))
            signal = MailSignal(os.path.join(tmp, "coa", "mail_signal"))
            self.assertFalse(signal.raised())
            link.touch_mail_signal(COA, state_dir=tmp)
            self.assertTrue(signal.raised())
            self.assertFalse(signal.raised())
            time.sleep(0.01)
            link.touch_mail_signal(COA, state_dir=tmp)
            self.assertTrue(signal.raised())

    def test_no_file_and_no_path_never_raise(self):
        self.assertFalse(MailSignal(None).raised())
        self.assertFalse(MailSignal("/nonexistent/dir/mail_signal").raised())

    def _window(self, clock):
        window = ListeningWindow(5, clock.now + 45 * 60, pu_ids={"pu"}, agent_ids=set(), clock=clock, env={})
        window.note_rows([{"from_user_id": "pu", "to_user_id": "sub-coa", "channel": "vv"}])
        return window

    def test_the_wait_reads_mail_at_once_when_signalled(self):
        clock = Clock()
        window = self._window(clock)
        if not window.should_wait():
            self.skipTest("window did not open with this row shape")
        checks = []

        def signal():
            checks.append(clock.now)
            return len(checks) == 2

        result = wait_for_mail(window, lambda: True, sleep=lambda s: setattr(clock, "now", clock.now + s),
                               poll_seconds=3, signal=signal, signal_seconds=0.25)
        self.assertEqual(result, "mail")
        self.assertAlmostEqual(clock.now - 1000.0, 0.5)

    def test_without_a_signal_the_wait_is_as_before(self):
        clock = Clock()
        window = self._window(clock)
        if not window.should_wait():
            self.skipTest("window did not open with this row shape")
        result = wait_for_mail(window, lambda: True, sleep=lambda s: setattr(clock, "now", clock.now + s),
                               poll_seconds=3)
        self.assertEqual(result, "mail")
        self.assertAlmostEqual(clock.now - 1000.0, 3.0)

    def test_a_quiet_signal_still_polls_every_three_seconds(self):
        clock = Clock()
        window = self._window(clock)
        if not window.should_wait():
            self.skipTest("window did not open with this row shape")
        polls = []

        def inject():
            polls.append(clock.now)
            return len(polls) == 2

        wait_for_mail(window, inject, sleep=lambda s: setattr(clock, "now", clock.now + s),
                      poll_seconds=3, signal=lambda: False, signal_seconds=0.25)
        self.assertEqual([round(p - 1000.0, 2) for p in polls], [3.0, 6.0])


class TestFetchLock(unittest.TestCase):
    """Two fetches of one inbox at the same time store a message once (RL-03)."""

    SCHEMA = ("CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, message_id TEXT, direction TEXT, "
              "status TEXT, from_user_id TEXT, to_user_id TEXT, original_text TEXT, text TEXT, attachment_path TEXT, "
              "created_at TEXT, mode TEXT, raw_payload TEXT, display_name TEXT, channel_id TEXT, "
              "has_attachments INTEGER DEFAULT 0, channel TEXT DEFAULT 'vv')")

    def setUp(self):
        agictl_dir = os.path.join(CORE_INFRA, "agictl")
        if agictl_dir not in sys.path:
            sys.path.insert(0, agictl_dir)  # comms imports its neighbours by bare name
        try:
            import agictl.comms as comms
        except Exception as error:  # rich or click missing in this interpreter
            self.skipTest(f"agictl.comms not importable here: {error}")
        self.comms = comms

    def test_two_fetches_at_once_insert_a_message_once(self):
        comms = self.comms
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "messages.db")
            conn = sqlite3.connect(path)
            conn.execute(self.SCHEMA)
            conn.commit()
            conn.close()
            inside = []
            overlap = []

            def fake_api(endpoint, token, method="GET", body=None, timeout=None):
                inside.append(1)
                overlap.append(len(inside))
                time.sleep(0.2)
                conn = sqlite3.connect(path)
                stored = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
                conn.close()
                inside.pop()
                return {"messages": [] if stored else [
                    {"messageId": "m1", "from": {"uid": "pu"}, "text": "hello", "timestamp": "2026-11-18T10:00:00Z"}]}

            original = comms.api_request
            comms.api_request = fake_api
            results = []
            try:
                threads = [threading.Thread(
                    target=lambda: results.append(comms.fetch_inbox("coa", tmp, "sub-coa", "tok", path)))
                    for _ in range(2)]
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join(20)
            finally:
                comms.api_request = original
            conn = sqlite3.connect(path)
            rows = conn.execute("SELECT message_id FROM messages").fetchall()
            conn.close()
            self.assertEqual(rows, [("m1",)])
            self.assertEqual(sorted(results), [(True, 0), (True, 1)])
            self.assertEqual(max(overlap), 1, "the two fetches ran one after the other")
            self.assertTrue(os.path.isfile(os.path.join(tmp, ".inbox_fetch_sub-coa.lock")))

    def test_another_agents_fetch_is_not_held_up(self):
        comms = self.comms
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "messages.db")
            with comms.inbox_fetch_lock(path, "sub-coa"):
                got = []
                thread = threading.Thread(target=lambda: got.append(
                    comms.inbox_fetch_lock(path, "sub-amy").__enter__()))
                thread.start()
                thread.join(5)
                self.assertFalse(thread.is_alive())

    def test_a_lock_file_that_cannot_be_made_does_not_stop_the_fetch(self):
        with self.comms.inbox_fetch_lock("/nonexistent-dir/messages.db", "sub-coa"):
            pass


@unittest.skipUnless(shutil.which("bash"), "needs bash")
class TestUnitInstall(unittest.TestCase):
    def install(self, tmp):
        stub = os.path.join(tmp, "bin")
        os.makedirs(stub, exist_ok=True)
        with open(os.path.join(stub, "systemctl"), "w") as handle:
            handle.write('#!/usr/bin/env bash\necho "$*" >> "$CALLS"\n')
        os.chmod(os.path.join(stub, "systemctl"), 0o755)
        env = dict(os.environ, PATH=f"{stub}:{os.environ['PATH']}", CALLS=os.path.join(tmp, "calls"),
                   LINK_RUNNER=os.path.join(tmp, "lib", "versa_agi_link.py"),
                   LINK_UNIT_FILE=os.path.join(tmp, "versa-agi-link.service"))
        script = f'source "{INSTALLER}"\ninstall_link_service "{CORE_INFRA}" watchdog /home/watchdog/core-infra\n'
        return subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True, timeout=30).returncode

    def test_the_unit_runs_the_runner_as_watchdog_and_is_rewritten_only_on_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(self.install(tmp), 0, "first install reports a change")
            unit = _read(os.path.join(tmp, "versa-agi-link.service"))
            self.assertIn("User=watchdog\n", unit)
            self.assertIn(f"ExecStart=/usr/bin/python3 {os.path.join(tmp, 'lib', 'versa_agi_link.py')}\n", unit)
            self.assertIn("Environment=VERSA_CORE_INFRA=/home/watchdog/core-infra\n", unit)
            self.assertIn("Restart=always\n", unit)
            # A woken agent's cycle lives in this unit's control group: a stop or
            # restart of the service must end the runner alone (seen 2026-11-18).
            self.assertIn("KillMode=process\n", unit)
            self.assertNotIn("Listen", unit)
            self.assertEqual(_read(os.path.join(tmp, "lib", "versa_agi_link.py")), _read(RUNNER))
            self.assertEqual(self.install(tmp), 1, "a second install changes nothing")
            self.assertIn("daemon-reload", _read(os.path.join(tmp, "calls")))

    def test_setup_installs_and_restarts_it_and_falls_back_without_systemd(self):
        setup = _read(os.path.join(SRC, "setup.sh"))
        block = setup[setup.index("# ─── Step 11b: Link Service"):setup.index("# ─── System Permissions Restabilization")]
        self.assertIn('source "${SRC_CORE_INFRA}/scripts/link_service.sh"', block)
        self.assertIn("if [ -d /run/systemd/system ]; then", block)
        self.assertIn('install_link_service "${SRC_CORE_INFRA}" "${WATCHDOG_USER}" "${WATCHDOG_HOME}/core-infra"', block)
        self.assertIn("systemctl restart versa-agi-link", block)
        self.assertIn("Lifeline's own fetch carries the mail", block)

    def test_the_runner_opens_no_listening_socket(self):
        source = _read(RUNNER)
        for word in ("socket.", ".bind(", ".listen(", "http.server", "socketserver"):
            self.assertNotIn(word, source)


class TestTokenStaysOffTheCommandLine(unittest.TestCase):
    """Any account on the machine can read another process's arguments. The API
    token travels in the environment, which only the same account can read."""

    def test_lifeline_gives_no_token_as_an_argument(self):
        source = _read(LIFELINE)
        self.assertNotIn("--token", source)
        self.assertEqual(len(re.findall(r'AGICTL_VV_TOKEN="\$\{(?:token|API_TOKEN)\}" ', source)), 3)

    def test_each_lifeline_command_takes_the_token_from_the_environment(self):
        cli = _read(os.path.join(CORE_INFRA, "agictl", "cli.py"))
        for start, end in (('@system.command("activity",', "def system_activity("),
                           ('@system.command("activity-backfill",', "def system_activity_backfill("),
                           ('@message.command("sync-inbox")', "def message_sync_inbox(")):
            begin = cli.index(start)
            decorators = cli[begin:cli.index(end, begin)]
            self.assertIn('"--token", required=True, envvar="AGICTL_VV_TOKEN"', decorators, start)

    @unittest.skipUnless(shutil.which("bash") and shutil.which("jq"), "needs bash and jq")
    def test_lifelines_own_functions_put_the_token_in_the_environment_only(self):
        """The three functions as they stand in lifeline.sh, run against a stand-in agictl."""
        with tempfile.TemporaryDirectory() as tmp:
            record = os.path.join(tmp, "calls")
            stand_in = os.path.join(tmp, "agictl")
            with open(stand_in, "w", encoding="utf-8") as out:
                out.write('#!/bin/bash\n'
                          f'printf \'args=%s env=%s\\n\' "$*" "${{AGICTL_VV_TOKEN:-}}" >> "{record}"\n'
                          'echo \'{"idleMinutes": 15, "inserted": 0}\'\n')
            os.chmod(stand_in, 0o755)
            setup_ini = os.path.join(tmp, "setup.ini")
            with open(setup_ini, "w", encoding="utf-8") as out:
                out.write("[versavoice]\nenabled=true\n")
            functions = "\n".join(_shell_function(name) for name in (
                "_vv_inbox_inserted", "_vv_sync_agent_inbox", "_vv_activity"))
            functions = functions.replace("/usr/local/bin/agictl", stand_in)
            script = (
                "set -euo pipefail\n"
                'log() { :; }\n_vv_run_instance_sync() { :; }\n'
                f'SETUP_INI="{setup_ini}"\nAGENT_NAME=coa\nSUB_ACCOUNT_ID=sub-coa\n'
                'API_TOKEN=secret-token-value\nCURRENT_CYCLE_ID=coa-1\n'
                f"{functions}\n"
                '_vv_sync_agent_inbox agi-coa coa /home/agi-coa/env sub-coa secret-token-value\n'
                '_vv_activity start --link\n'
            )
            done = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=60)
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertEqual(done.stdout.strip(), "15", "the granted minutes still come back")
            calls = _read(record).splitlines()
            self.assertEqual(len(calls), 2)
            for call in calls:
                arguments, _, environment = call.partition(" env=")
                self.assertNotIn("secret-token-value", arguments)
                self.assertNotIn("--token", arguments)
                self.assertEqual(environment, "secret-token-value")
            self.assertIn("message sync-inbox agi-coa", calls[0])
            self.assertIn("system activity start --sub-account sub-coa --cycle-id coa-1 --link", calls[1])

    def test_the_link_service_gives_no_token_as_an_argument(self):
        self.assertNotIn('"--token"', _read(RUNNER))


@unittest.skipUnless(shutil.which("bash"), "needs bash")
class TestOfflineHold(unittest.TestCase):
    """RL-11: one network check per minute pass; while the cloud cannot be reached,
    fetches stay quiet and cloud-model agents are not started."""

    FUNCTIONS = ("_vv_net_down", "_vv_net_reachable", "_vv_net_check")

    def setUp(self):
        import socket
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.flag = os.path.join(self.tmp, "net_down")
        self.log = os.path.join(self.tmp, "log")
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(8)
        self.addCleanup(self.listener.close)
        self.open_port = self.listener.getsockname()[1]
        closed = socket.socket()
        closed.bind(("127.0.0.1", 0))
        self.closed_port = closed.getsockname()[1]
        closed.close()

    def run_pass(self, port, body="_vv_net_check\necho \"${NET_DOWN} ${NET_WENT_DOWN_NOW}\"\n", more=""):
        functions = "\n".join(_shell_function(name) for name in self.FUNCTIONS)
        script = (f'set -euo pipefail\nlog() {{ echo "$*" >> "{self.log}"; }}\n'
                  f'NET_DOWN_FILE="{self.flag}"\nNET_PROBE_HOST=127.0.0.1\nNET_PROBE_PORT={port}\n'
                  f"{functions}\n{more}\n{body}")
        done = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)
        return done.stdout.strip()

    def lines(self):
        return _read(self.log).splitlines() if os.path.exists(self.log) else []

    def test_reachable_and_no_outage_says_nothing(self):
        self.assertEqual(self.run_pass(self.open_port), "false false")
        self.assertEqual(self.lines(), [])
        self.assertFalse(os.path.exists(self.flag))

    def test_an_outage_is_logged_once_when_it_starts_and_once_when_it_ends(self):
        self.assertEqual(self.run_pass(self.closed_port), "true true")
        self.assertEqual(self.run_pass(self.closed_port), "true false")
        self.assertEqual(self.run_pass(self.closed_port), "true false")
        self.assertEqual(len(self.lines()), 1)
        self.assertTrue(self.lines()[0].startswith("OFFLINE: the VersaVoice API cannot be reached"))
        started = time.time() - 600
        with open(self.flag, "w", encoding="utf-8") as out:
            out.write(f"{int(started)}\n")
        self.assertEqual(self.run_pass(self.open_port), "false false")
        self.assertEqual(self.run_pass(self.open_port), "false false")
        self.assertEqual(len(self.lines()), 2)
        self.assertRegex(self.lines()[1], r"^ONLINE: the VersaVoice API is reachable again after 1[01] min")
        self.assertFalse(os.path.exists(self.flag))

    def test_the_flag_counts_only_while_a_pass_keeps_it_fresh(self):
        check = "if _vv_net_down; then echo down; else echo up; fi\n"
        self.assertEqual(self.run_pass(self.open_port, check), "up")
        with open(self.flag, "w", encoding="utf-8") as out:
            out.write("1\n")
        self.assertEqual(self.run_pass(self.open_port, check), "down")
        stale = time.time() - 400
        os.utime(self.flag, (stale, stale))
        self.assertEqual(self.run_pass(self.open_port, check), "up", "Lifeline stopped checking: not an outage")

    def test_fetch_and_activity_stay_quiet_while_offline(self):
        record = os.path.join(self.tmp, "calls")
        stand_in = os.path.join(self.tmp, "agictl")
        with open(stand_in, "w", encoding="utf-8") as out:
            out.write(f'#!/bin/bash\necho called >> "{record}"\necho \'{{"idleMinutes": 15}}\'\n')
        os.chmod(stand_in, 0o755)
        setup_ini = os.path.join(self.tmp, "setup.ini")
        with open(setup_ini, "w", encoding="utf-8") as out:
            out.write("[versavoice]\nenabled=true\n")
        more = "\n".join(_shell_function(name) for name in (
            "_vv_inbox_inserted", "_vv_sync_agent_inbox", "_vv_activity")).replace("/usr/local/bin/agictl", stand_in)
        more = (f'_vv_run_instance_sync() {{ :; }}\nSETUP_INI="{setup_ini}"\nAGENT_NAME=coa\nSUB_ACCOUNT_ID=sub-coa\n'
                f"API_TOKEN=tok\nCURRENT_CYCLE_ID=coa-1\n{more}")
        body = "_vv_sync_agent_inbox agi-coa coa /home/agi-coa/env sub-coa tok\n_vv_activity start\n"
        with open(self.flag, "w", encoding="utf-8") as out:
            out.write(f"{int(time.time())}\n")
        self.assertEqual(self.run_pass(self.open_port, body, more), "0")
        self.assertFalse(os.path.exists(record), "nothing was sent while offline")
        self.assertEqual(self.lines(), [])
        os.unlink(self.flag)
        self.assertEqual(self.run_pass(self.open_port, body, more), "15")
        self.assertEqual(len(_read(record).splitlines()), 2)

    def test_the_spawn_hold_comes_before_anything_an_outage_would_cost(self):
        source = _read(LIFELINE)
        gate = source.index('if [ "${NET_DOWN}" = true ] && [ "${IS_LOCAL_MODEL}" != true ] && [ "${FORCE_RUN}" != "true" ]; then')
        self.assertLess(source.index("# ─── Early Model Classification"), gate)
        self.assertLess(gate, source.index("# ─── Circuit Breaker — Consecutive Failure Guard"))
        self.assertLess(gate, source.index("SET spawn_attempts = spawn_attempts + 1"))
        block = source[gate:source.index("# ─── Concurrency Cap Check", gate)]
        self.assertIn("flock -u 200\n    continue", block)
        self.assertEqual(block.count("log "), 1)
        self.assertIn('if [ "${NET_WENT_DOWN_NOW}" = true ]; then\n      log "OFFLINE: ${AGENT_NAME}', block)

    def test_the_check_runs_in_the_minute_pass_only_and_after_its_functions(self):
        source = _read(LIFELINE)
        call = source.index("\n    _vv_net_check\n")
        self.assertLess(source.index("\n_vv_net_check() {\n"), call)
        self.assertLess(source.rindex('if [ -z "${ONLY_AGENT}" ]; then', 0, call), call)
        self.assertLess(call, source.index("\nwhile IFS='|' read -r AGENT_NAME"))


class TestDraftSender(unittest.TestCase):
    """Reply drafts: the agent's file, read as untrusted, passed to the API (§2.2)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.path = os.path.join(self.tmp, "reply_draft")
        self.clock = Clock()
        self.posts = []
        self.status = 200
        self.sender = link.DraftSender(post=self.post, path_of=lambda agent: self.path, clock=self.clock)

    def post(self, agent, to, record):
        self.posts.append((agent.name, to, record["text"], record["done"]))
        return self.status

    def write(self, text="Hello", to="pu_1", done=False, seq=1, draft=5):
        with open(self.path, "w", encoding="utf-8") as out:
            json.dump({"v": 1, "to": to, "text": text, "draft": draft, "seq": seq, "done": done,
                       "cycle": "coa-1", "at": time.time()}, out)
        stamp = time.time() + seq / 1000.0   # a new time for every write, whatever the clock's grain
        os.utime(self.path, (stamp, stamp))

    def tick(self, seconds=0.3):
        self.clock.now += seconds
        self.sender.tick([COA])

    def test_no_file_no_request(self):
        self.tick()
        self.assertEqual(self.posts, [])

    def test_a_growing_draft_is_sent_at_most_once_a_second_and_the_end_always(self):
        for seq, text in enumerate(("He", "Hello", "Hello the", "Hello there", "Hello there."), start=1):
            self.write(text, seq=seq)
            self.tick(0.3)
        self.assertEqual([post[2] for post in self.posts], ["He", "Hello there."], "five writes, two requests")
        self.write("Hello there. One more.", seq=6)
        self.tick(0.3)
        self.assertEqual(len(self.posts), 2, "under a second since the last request")
        self.tick(1.0)
        self.assertEqual(self.posts[-1][2], "Hello there. One more.")
        self.write("", to="", done=True, seq=9)
        self.tick(0.3)
        self.assertEqual(self.posts[-1], ("coa", "pu_1", "", True))
        count = len(self.posts)
        self.tick(30)
        self.assertEqual(len(self.posts), count, "a closed draft is left alone")

    def test_an_open_draft_is_confirmed_while_its_message_is_sent(self):
        self.write("Speaking this takes a while.")
        self.tick()
        self.tick(3)
        self.assertEqual(len(self.posts), 1)
        self.tick(link.DRAFT_KEEPALIVE_SECONDS)
        self.assertEqual(len(self.posts), 2)
        self.assertEqual(self.posts[-1][3], False)

    def test_an_open_draft_nobody_touches_is_closed(self):
        self.write("The cycle died here")
        self.tick()
        old = time.time() - link.DRAFT_MAX_OPEN_SECONDS - 5
        os.utime(self.path, (old, old))
        self.tick(1)
        self.assertEqual(self.posts[-1], ("coa", "pu_1", "", True))

    def test_an_old_open_file_found_at_start_is_not_sent(self):
        self.write("left over")
        old = time.time() - 3600
        os.utime(self.path, (old, old))
        self.tick()
        self.assertEqual(self.posts, [])

    def test_a_draft_to_another_person_closes_the_first(self):
        self.write("For the first person", to="pu_1", seq=1)
        self.tick()
        self.write("For the second", to="pu_2", seq=2, draft=6)
        self.tick(1.5)
        self.assertEqual(self.posts[-2:], [("coa", "pu_1", "", True), ("coa", "pu_2", "For the second", False)])

    def test_a_missing_route_or_refused_token_stops_the_requests(self):
        self.status = 404
        self.write("Hello")
        self.tick()
        self.write("Hello again", seq=2)
        self.tick(60)
        self.assertEqual(len(self.posts), 1)
        self.status = 200
        self.tick(link.DRAFT_HOLD_SECONDS)
        self.assertEqual(self.posts[-1][2], "Hello again")

    def test_a_failed_request_is_tried_again_and_never_raises(self):
        self.status = 0
        self.write("Hello")
        self.tick()
        self.tick(0.3)
        self.status = 200
        self.tick(1.0)
        self.assertEqual([post[2] for post in self.posts], ["Hello", "Hello"])
        self.sender = link.DraftSender(post=lambda *a: 1 / 0, path_of=lambda agent: self.path, clock=self.clock)
        self.write("x", seq=7)
        self.tick()

    def test_the_file_is_read_as_untrusted(self):
        def stored(text):
            with open(self.path, "w", encoding="utf-8") as out:
                out.write(text)
            return link.read_draft(self.path)
        good = {"v": 1, "to": "pu_1", "text": "Hello", "draft": 1, "seq": 1, "done": False, "cycle": "coa-1"}
        self.assertEqual(stored(json.dumps(good))["text"], "Hello")
        for change in ({"to": "../../etc"}, {"to": "a b"}, {"to": 5}, {"text": 5}, {"text": "   "}, {"v": 2},
                       {"draft": "1"}, {"seq": -1}, {"draft": True}):
            self.assertIsNone(stored(json.dumps({**good, **change})), change)
        for raw in ("", "not json", "[]", '"text"', "{"):
            self.assertIsNone(stored(raw), raw)
        self.assertIsNone(stored(json.dumps({**good, "text": "x" * (link.DRAFT_MAX_FILE_BYTES + 10)})))
        long_text = stored(json.dumps({**good, "text": "y" * (link.DRAFT_MAX_TEXT_CHARS + 500)}))
        self.assertEqual(len(long_text["text"]), link.DRAFT_MAX_TEXT_CHARS)
        self.assertEqual(stored(json.dumps({**good, "cycle": "bad cycle id!"}))["cycle"], "")
        self.assertIsNone(link.read_draft(os.path.join(self.tmp, "missing")))

    def test_the_request_carries_the_token_in_a_header_and_goes_to_the_fixed_address(self):
        seen = {}

        class Answer:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *_): return False

        def opened(request, timeout=None):
            seen["url"], seen["auth"] = request.full_url, request.get_header("Authorization")
            seen["body"], seen["method"] = json.loads(request.data), request.get_method()
            return Answer()

        original = link.urllib.request.urlopen
        link.urllib.request.urlopen = opened
        try:
            status = link.post_draft(COA, "pu_1", {"text": "Hi", "draft": 3, "seq": 2, "done": False, "cycle": "coa-1"})
        finally:
            link.urllib.request.urlopen = original
        self.assertEqual(status, 200)
        self.assertEqual((seen["url"], seen["method"], seen["auth"]), (link.API_BASE + "/agi/draft", "POST", "Bearer tok"))
        self.assertEqual(seen["body"], {"subAccountId": "sub-coa", "recipientId": "pu_1", "text": "Hi", "draftId": 3,
                                        "seq": 2, "done": False, "cycleId": "coa-1"})

    def test_the_harness_file_and_the_sender_agree(self):
        from harness import reply_draft
        folder = os.path.join(self.tmp, "coa", "cycles")
        os.makedirs(folder)
        self.assertEqual(link.draft_path(COA, self.tmp), os.path.join(folder, "reply_draft"))
        draft = reply_draft.ReplyDraft(link.draft_path(COA, self.tmp), "coa-1")
        draft.start_call()
        draft.tool_chunk(1, reply_draft.TOOL_NAME, json.dumps({"command": 'message send pu_1 "Written by the harness'}))
        draft.end_call()
        record = link.read_draft(link.draft_path(COA, self.tmp))
        self.assertEqual((record["to"], record["text"], record["done"]), ("pu_1", "Written by the harness", False))
        draft.finish()
        self.assertTrue(link.read_draft(link.draft_path(COA, self.tmp))["done"])

    def test_lifeline_gives_the_file_only_when_drafts_are_on_and_the_link_is_up(self):
        source = _read(LIFELINE)
        self.assertIn("""    if _vv_reply_drafts_on && _vv_link_up; then
      echo "export VERSA_REPLY_DRAFT_FILE='${CYCLES_DIR}/reply_draft'"
    fi""", source)


@unittest.skipUnless(shutil.which("bash"), "needs bash")
class TestReplyDraftsAreParked(unittest.TestCase):
    """Primary User, 2026-11-18: off by default, the code stays (state_realtime_link.md D14)."""

    def lifeline_says(self, ini_text):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "setup.ini")
            if ini_text is not None:
                with open(path, "w", encoding="utf-8") as out:
                    out.write(ini_text)
            script = f'set -euo pipefail\nSETUP_INI="{path}"\n{_shell_function("_vv_reply_drafts_on")}\n_vv_reply_drafts_on\n'
            return subprocess.run(["bash", "-c", script], capture_output=True, timeout=30).returncode == 0

    def test_off_unless_the_setting_says_true(self):
        cases = ((None, False), ("[features]\nlive_link=true\n", False),
                 ("[features]\nlive_link=true\nreply_drafts=false\n", False),
                 ("[versavoice]\nreply_drafts=true\n[features]\nlive_link=true\n", False),
                 ("[features]\nlive_link=true\nreply_drafts=true\n", True))
        for text, expected in cases:
            self.assertEqual(self.lifeline_says(text), expected, text)
            with tempfile.TemporaryDirectory() as tmp:
                path = os.path.join(tmp, "setup.ini")
                if text is not None:
                    with open(path, "w", encoding="utf-8") as out:
                        out.write(text)
                self.assertEqual(link.drafts_enabled(path), expected, text)

    def test_the_stock_setting_is_off_and_setup_does_not_ask(self):
        features = _read(os.path.join(SRC, "setup.ini.stock")).split("[features]")[1].split("\n[")[0]
        self.assertIn("\nreply_drafts=false\n", features)
        self.assertNotIn("reply_drafts", _read(os.path.join(CORE_INFRA, "install_acceptance.sh")))

    def test_the_link_service_hands_the_sender_no_agents_while_off(self):
        for on, expected in ((False, []), (True, ["coa"])):
            stop = threading.Event()
            seen = []

            class Sender:
                def serve(self, stop_event, agents_now):
                    while not stop_event.is_set():
                        seen.append([agent.name for agent in agents_now()])
                        stop_event.wait(0.01)

            class Service:
                def wait_once(self, agents):
                    time.sleep(0.08)
                    stop.set()
                    return 0.0

            link.run("/nowhere/lifeline.sh", stop=stop, agents_of=lambda: [COA], enabled=lambda: True,
                     service_for=lambda path: Service(), state=lambda *a: None, log=lambda *a: None,
                     drafts=Sender(), drafts_on=lambda: on)
            self.assertTrue(seen)
            self.assertEqual(seen[-1], expected, f"drafts on={on}")

    def test_the_code_is_kept_and_says_why(self):
        for path in (os.path.join(CORE_INFRA, "harness", "reply_draft.py"), RUNNER, LIFELINE):
            text = _read(path)
            self.assertIn("PARKED, 2026-11-18 (Primary User)", text, path)
            self.assertIn("Do not remove it.", text, path)
        self.assertTrue(hasattr(link, "DraftSender") and hasattr(link, "post_draft"))
        harness = _read(os.path.join(CORE_INFRA, "harness", "agent_harness.py"))
        self.assertIn("reply_draft_mod.from_environment(cycle_id)", harness)


class TestAgitopLinkLine(unittest.TestCase):
    """agitop reads the same report Lifeline reads, by the same rule."""

    def setUp(self):
        if CORE_INFRA not in sys.path:
            sys.path.insert(0, CORE_INFRA)
        from agitop.data import system_reader
        self.reader = system_reader
        self.folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.folder, True)
        self.path = os.path.join(self.folder, "link_state")

    def _report(self, state, detail, age=0):
        link.write_state(state, detail, self.path)
        stamp = time.time() - age
        os.utime(self.path, (stamp, stamp))

    def test_no_report_is_none(self):
        self.assertEqual(self.reader.read_link_state(self.path), {"state": "none", "detail": ""})

    def test_a_fresh_up_report_is_up_with_its_detail(self):
        self._report("up", "3 agents")
        self.assertEqual(self.reader.read_link_state(self.path), {"state": "up", "detail": "3 agents"})

    def test_an_up_report_that_went_stale_is_down(self):
        self._report("up", "3 agents", age=self.reader.LINK_FRESH_SECONDS + 200)
        status = self.reader.read_link_state(self.path)
        self.assertEqual(status["state"], "down")
        self.assertIn("no report for", status["detail"])

    def test_down_and_off_keep_their_reason_at_any_age(self):
        self._report("down", "HTTP 503", age=4000)
        self.assertEqual(self.reader.read_link_state(self.path), {"state": "down", "detail": "HTTP 503"})
        self._report("off", "switched off in setup.ini", age=4000)
        self.assertEqual(self.reader.read_link_state(self.path),
                         {"state": "off", "detail": "switched off in setup.ini"})

    def test_an_unreadable_report_is_none(self):
        with open(self.path, "w", encoding="utf-8") as out:
            out.write("something else\n")
        self.assertEqual(self.reader.read_link_state(self.path)["state"], "none")

    def test_the_freshness_limit_is_lifelines(self):
        self.assertIn(f"-le {self.reader.LINK_FRESH_SECONDS} ]", _shell_function("_vv_link_up"))
        self.assertEqual(self.reader.LINK_STATE_FILE, link.STATE_FILE)

    def test_the_line_in_the_system_column(self):
        try:
            from agitop.panels.system import link_status_markup
        except ImportError as error:  # textual lives in agitop's own Python
            self.skipTest(f"agitop panel not importable here: {error}")
        self.assertIn("up", link_status_markup({"state": "up", "detail": "3 agents"}))
        self.assertIn("3 agents", link_status_markup({"state": "up", "detail": "3 agents"}))
        self.assertIn("down", link_status_markup({"state": "down", "detail": "HTTP 503"}))
        self.assertIn("off", link_status_markup({"state": "off", "detail": ""}))
        self.assertEqual(link_status_markup({"state": "none", "detail": ""}), "")
        long_reason = link_status_markup({"state": "down", "detail": "x" * 80 + " [bold]"})
        self.assertIn("…", long_reason)
        self.assertNotIn("[bold]", long_reason.replace("[bold red]", ""))


if __name__ == "__main__":
    unittest.main()
