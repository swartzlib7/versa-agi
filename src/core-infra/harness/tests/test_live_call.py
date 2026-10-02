"""Harness call mode with fake VersaVoice bridge, GPT-Live HTTP, and sideband socket.

Scenarios follow state_live_voice_call.md §4 test data.
"""

import dataclasses
import json
import os
import queue
import re
import threading
import time
import unittest
import unittest.mock

from langchain_core.messages import AIMessage, HumanMessage

from harness import live_call as lc
from live_call_config import LiveCallSettings

SETTINGS = LiveCallSettings(enabled=True, call_model="gemini-3.7-flash", voice_model="gpt-live-1",
                            max_minutes=15, join_timeout_seconds=45)


class FakeSocket:
    def __init__(self):
        self.inbox: queue.Queue = queue.Queue()
        self.sent: list[dict] = []
        self.closed = False
        self.voice: list[list[dict]] = []   # events pushed after each "Tell Sam now" report

    def push(self, event: dict):
        self.inbox.put(json.dumps(event))

    def recv(self, timeout=None):
        try:
            return self.inbox.get(timeout=timeout or 0.05)
        except queue.Empty:
            raise TimeoutError()

    def send(self, raw: str):
        event = json.loads(raw)
        self.sent.append(event)
        if event["type"] == "session.close":
            self.push({"type": "session.closed", "reason": "close_requested", "usage": {"seconds": 61}})
        if event["type"] == "session.instructions.append" and "Tell Sam now" in event["content"] and self.voice:
            for scripted in self.voice.pop(0):
                self.push(scripted)

    def close(self):
        self.closed = True

    def sent_types(self):
        return [e["type"] for e in self.sent]


class FakeBridge:
    def __init__(self, open_status="calling", offers=None, refuse=False):
        self.calls: list[tuple[list[str], str | None]] = []
        self.open_status = open_status
        self.refuse = refuse
        self.offers = list(offers if offers is not None else [{"status": "offered", "sdp_offer": "v=0 offer"}])
        self.phone_status = "live"

    def __call__(self, args, stdin, timeout):
        self.calls.append((args, stdin))
        op = args[0]
        if op == "open":
            if self.refuse:
                return {"success": False, "error": "Sub-account is not connected to this recipient."}
            out = {"success": True, "call_id": "call_1", "status": self.open_status}
            if "--recipient" in args:
                out["callee_uid"] = args[args.index("--recipient") + 1]
                out["callee_name"] = "Ashok Patel"
                out["callee_language"] = "en|English"
            return out
        if op == "wait-offer":
            nxt = self.offers.pop(0) if self.offers else {"status": "calling"}
            return {"success": True, **nxt}
        if op == "status":
            return {"success": True, "status": self.phone_status}
        return {"success": True}

    def ops(self):
        return [a[0] for a, _ in self.calls]


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def _fake_time():
    """Clock + sleep for speech waits: fake seconds pass, real time barely does."""
    clock = FakeClock()

    def sleep(seconds):
        clock.now += seconds
        time.sleep(0.005)

    return {"clock": clock, "sleep": sleep}


def _runtime(bridge, sock, clock=None, http_status=201, sleep=None):
    posted = []

    def http_post(url, body, headers):
        posted.append((url, body, headers))
        return http_status, json.dumps({"session": {"id": "live_abc"}, "transport": {"type": "webrtc", "sdp": "v=0 answer"}})

    rt = lc.LiveCallRuntime(
        agent_label="Versa", pu_name="Sam", api_key_resolver=lambda: "sk-test",
        bridge=bridge, sideband_factory=lambda key, sid: sock, http_post=http_post,
        log=lambda msg: None, clock=clock or time.monotonic, sleep=sleep or time.sleep,
    )
    return rt, posted


class TestTranscript(unittest.TestCase):
    def test_segments_and_cursor(self):
        t = lc.Transcript()
        t.add_delta("pu", "Can you ")
        t.add_delta("pu", "check task 12?")
        first = t.since_cursor()
        self.assertEqual(first, [{"speaker": "pu", "text": "Can you check task 12?", "start_ms": None, "end_ms": None}])
        t.add_delta("pu", "Actually task 13.")
        second = t.since_cursor()
        self.assertEqual([s["text"] for s in second], ["Actually task 13."])
        self.assertEqual(len(t.all()), 2)

    def test_clip_for_speech_sentence_boundary(self):
        text = "First sentence is here. " * 200
        clipped = lc.clip_for_speech(text, limit=100)
        self.assertLessEqual(len(clipped), 100)
        self.assertTrue(clipped.endswith("."))

    def test_message_text_from_parts(self):
        self.assertEqual(lc.message_text([{"type": "text", "text": "a"}, {"type": "image_url"}, "b"]), "a\nb")


class TestPlace(unittest.TestCase):
    def test_connected_flow(self):
        bridge, sock = FakeBridge(), FakeSocket()
        rt, posted = _runtime(bridge, sock)
        result = rt.place("Task 12 is blocked on a decision.", SETTINGS)
        self.assertEqual(result["status"], "connected")
        self.assertEqual(bridge.ops(), ["open", "wait-offer", "answer", "log"])
        answer_args, answer_stdin = bridge.calls[2]
        self.assertEqual(answer_args, ["answer", "call_1", "--session-id", "live_abc"])
        self.assertEqual(answer_stdin, "v=0 answer")
        url, body, headers = posted[0]
        self.assertTrue(url.endswith("/live/sessions"))
        self.assertEqual(body["session"]["delegation"], {"type": "client"})
        self.assertEqual(body["session"]["model"], "gpt-live-1")
        self.assertEqual(body["transport"], {"type": "webrtc", "sdp": "v=0 offer"})
        self.assertIn("never", body["session"]["instructions"].lower())
        self.assertEqual(headers["Authorization"], "Bearer sk-test")
        rt.shutdown()

    def test_offline_no_session(self):
        bridge, sock = FakeBridge(open_status="offline"), FakeSocket()
        rt, posted = _runtime(bridge, sock)
        self.assertEqual(rt.place("x", SETTINGS)["status"], "offline")
        self.assertEqual(posted, [])
        self.assertIsNone(rt.session)

    def test_declined(self):
        bridge = FakeBridge(offers=[{"status": "calling"}, {"status": "declined"}])
        rt, posted = _runtime(bridge, FakeSocket())
        self.assertEqual(rt.place("x", SETTINGS)["status"], "declined")
        self.assertEqual(posted, [])

    def test_join_timeout_is_missed(self):
        clock = FakeClock()
        bridge = FakeBridge(offers=[])
        orig = bridge.__call__

        def advancing(args, stdin, timeout):
            if args[0] == "wait-offer":
                clock.now += 30
            return orig(args, stdin, timeout)

        rt, posted = _runtime(advancing, FakeSocket(), clock=clock)
        self.assertEqual(rt.place("x", SETTINGS)["status"], "missed")
        self.assertIn(["end", "call_1", "--status", "missed", "--close-reason", "join_timeout"],
                      [a for a, _ in bridge.calls])
        self.assertEqual(posted, [])

    def test_session_create_failure_ends_call(self):
        bridge = FakeBridge()
        rt, _ = _runtime(bridge, FakeSocket(), http_status=429)
        result = rt.place("x", SETTINGS)
        self.assertEqual(result["status"], "failed")
        self.assertIn("429", result["error"])
        self.assertEqual(bridge.calls[-1][0][:4], ["end", "call_1", "--status", "failed"])

    def test_one_call_per_cycle(self):
        bridge = FakeBridge(open_status="offline")
        rt, _ = _runtime(bridge, FakeSocket())
        rt.place("x", SETTINGS)
        refused = rt.place("again", SETTINGS)
        self.assertEqual(refused["status"], "refused")
        self.assertIn("1 call per cycle", refused["error"])

    def test_calls_per_cycle_setting_allows_a_second_call(self):
        two = dataclasses.replace(SETTINGS, calls_per_cycle=2)
        bridge = FakeBridge(offers=[{"status": "offered", "sdp_offer": "v=0 offer"},
                                    {"status": "offered", "sdp_offer": "v=0 offer"}])
        sock = FakeSocket()
        rt, _ = _runtime(bridge, sock)
        self.assertEqual(rt.place("first", two)["status"], "connected")
        self.assertEqual(rt.place("during", two)["status"], "refused")   # already on a call
        sock.push({"type": "session.closed", "reason": "remote_hangup"})
        self.assertIn("LIVE CALL ENDED", rt.next_injection(""))
        self.assertTrue(rt.awaiting_summary)

        no_summary = rt.place("second", two)
        self.assertEqual(no_summary["status"], "refused")
        self.assertIn("last_call_summary", no_summary["error"])

        second = rt.place("second", two, last_call_summary="Agreed QA on Friday.")
        self.assertEqual(second["status"], "connected")
        self.assertIn((["summary", "call_1"], "Agreed QA on Friday."), bridge.calls)
        self.assertFalse(rt.awaiting_summary)
        self.assertEqual(rt.reason, "second")
        self.assertEqual(rt.delegations, {})
        self.assertEqual(rt.place("third", two, last_call_summary="x")["status"], "refused")
        rt.shutdown()


class TestTurns(unittest.TestCase):
    def _connected(self):
        bridge, sock = FakeBridge(), FakeSocket()
        rt, _ = _runtime(bridge, sock)
        self.assertEqual(rt.place("Package request pending.", SETTINGS)["status"], "connected")
        return rt, bridge, sock

    def test_delegation_answer_then_hangup(self):
        rt, bridge, sock = self._connected()
        sock.push({"type": "session.output_transcript.delta", "delta": "Hi Sam, a package needs you."})
        sock.push({"type": "session.input_transcript.delta", "delta": "Yes approve it."})
        sock.push({"type": "session.delegation.created", "delegation": {"id": "item_1", "target": "client"}})

        text = rt.next_injection("Calling now.")
        self.assertIn("item_1", text)
        self.assertIn("PU: Yes approve it.", text)
        self.assertIn("never an approval", text)
        self.assertTrue(rt.in_turn())

        rt.note_tool_calls(["agictl_system"])
        self.assertIn("session.thinking.append", sock.sent_types())

        sock.push({"type": "session.closed", "reason": "remote_hangup", "usage": {"seconds": 75}})
        ended = rt.next_injection("I can't approve by voice — please use the Packages toggle in the app.")
        commentary = [e for e in sock.sent if e["type"] == "session.commentary.append"]
        self.assertEqual(commentary[0]["delegation_id"], "item_1")
        self.assertIn("Packages toggle", commentary[0]["content"])
        self.assertIn("LIVE CALL ENDED (remote_hangup", ended)
        self.assertIn("agictl_message", ended)
        self.assertFalse(rt.live)
        self.assertIsNone(rt.next_injection("Summary sent."))

        end_calls = [(a, s) for a, s in bridge.calls if a[0] == "end"]
        self.assertEqual(len(end_calls), 1)
        args, stdin = end_calls[0]
        self.assertIn("--voice-seconds", args)
        self.assertEqual(args[args.index("--voice-seconds") + 1], "75")
        snap = json.loads(stdin)
        self.assertEqual(snap["delegations"][0]["tools"], ["agictl_system"])
        self.assertTrue(snap["delegations"][0]["spoken"])

    def test_queued_delegations_merge_into_one_turn(self):
        rt, _, sock = self._connected()
        sock.push({"type": "session.delegation.created", "delegation": {"id": "item_a"}})
        sock.push({"type": "session.delegation.created", "delegation": {"id": "item_b"}})
        time.sleep(0.2)
        text = rt.next_injection("")
        self.assertIn("item_a, item_b", text)
        sock.push({"type": "session.closed", "reason": "remote_hangup"})
        rt.next_injection("Both done.")
        commentary = [e for e in sock.sent if e["type"] == "session.commentary.append"]
        self.assertEqual(commentary[-1]["delegation_id"], "item_b")
        rt.shutdown()

    def test_duration_cap_wraps_then_closes(self):
        clock = FakeClock()
        bridge, sock = FakeBridge(), FakeSocket()
        rt, _ = _runtime(bridge, sock, clock=clock)
        rt.place("x", SETTINGS)
        clock.now += SETTINGS.max_minutes * 60 - 30
        rt.session.enforce_duration()
        self.assertIn("session.instructions.append", sock.sent_types())
        clock.now += 40
        text = rt.next_injection("")
        self.assertIn("LIVE CALL ENDED (close_requested", text)
        self.assertIn("session.close", sock.sent_types())

    def test_phone_hangup_without_sideband_close_ends_call(self):
        clock = FakeClock()
        bridge, sock = FakeBridge(), FakeSocket()
        rt, _ = _runtime(bridge, sock, clock=clock)
        rt.place("x", SETTINGS, recipient_id="uid_ashok")
        bridge.phone_status = "ended"
        clock.now += lc.PHONE_STATUS_POLL_SECONDS + 1
        text = rt.next_injection("Connected to Ashok.")
        self.assertIn("LIVE CALL ENDED (phone_ended", text)
        self.assertIn("session.close", sock.sent_types())
        args = [a for a, _ in bridge.calls if a[0] == "end"][0]
        self.assertEqual(args[args.index("--close-reason") + 1], "phone_ended")
        self.assertIsNone(rt.next_injection("CALL SUMMARY: done"))

    def test_phone_status_live_keeps_waiting(self):
        clock = FakeClock()
        bridge, sock = FakeBridge(), FakeSocket()
        rt, _ = _runtime(bridge, sock, clock=clock)
        rt.place("x", SETTINGS)
        clock.now += lc.PHONE_STATUS_POLL_SECONDS + 1
        self.assertFalse(rt.session.phone_ended())
        self.assertIn("status", bridge.ops())
        self.assertNotIn("session.close", sock.sent_types())
        rt.shutdown()

    def test_shutdown_closes_open_call(self):
        rt, bridge, sock = self._connected()
        rt.shutdown("failed")
        self.assertIn("session.close", sock.sent_types())
        end_args = [a for a, _ in bridge.calls if a[0] == "end"][0]
        self.assertEqual(end_args[:4], ["end", "call_1", "--status", "failed"])


class TestLanguage(unittest.TestCase):
    TABLE = {
        "en": {"name": "English", "coverage": "voice"},
        "es": {"name": "Spanish", "coverage": "supported"},
        "zu": {"name": "Zulu", "coverage": "english"},
    }

    def test_supported_language_is_spoken(self):
        lang = lc.resolve_call_language("es|Spanish", self.TABLE)
        self.assertEqual((lang.code, lang.name, lang.coverage), ("es", "Spanish", "supported"))
        self.assertEqual(lc.language_rule(lang), "Speak Spanish unless the Primary User asks to switch.")

    def test_uncovered_language_falls_back_to_english(self):
        lang = lc.resolve_call_language("zu|Zulu", self.TABLE)
        self.assertEqual((lang.code, lang.requested), ("en", "Zulu"))
        self.assertIn("not available in Zulu", lc.language_rule(lang))

    def test_unknown_and_auto(self):
        self.assertEqual(lc.resolve_call_language("xx|Klingon", self.TABLE).code, "en")
        auto = lc.resolve_call_language("auto|Auto-detect", self.TABLE)
        self.assertEqual(lc.language_rule(auto), "Speak English unless the Primary User asks to switch.")

    def test_shipped_map_covers_the_93_versavoice_languages(self):
        table = lc.load_language_map()
        self.assertEqual(len(table), 93)
        self.assertEqual({k for k, v in table.items() if v["coverage"] == "voice"}, {"en", "pt"})
        app_langs = os.path.join(os.path.dirname(__file__), *[".."] * 5,
                                 "app", "lib", "features", "profile", "domain", "languages.dart")
        if os.path.isfile(app_langs):
            text = open(app_langs, encoding="utf-8").read()
            body = text[text.index("static const List<AppLanguage> all"):]
            codes = set(re.findall(r"code:\s*'([^']+)'", body))
            self.assertEqual(codes, set(table))


class TestVoiceCardAndSummary(unittest.TestCase):
    def test_voice_card_sections(self):
        card = lc.voice_instructions("Versa", "Sam", "Task 12 needs a day.",
                                     language=lc.CallLanguage("es", "Spanish", "supported", "Spanish"),
                                     style_notes="prefers short updates")
        for part in ("Delegation policy:", "Backend tools:", "Delegate to the backend when:",
                     "Do not delegate to the backend when:", "Approvals cannot be given by voice",
                     "Speak Spanish", "prefers short updates", "not a simulated personality",
                     "Purpose:", "Duty: safeguard Sam", "Understanding Sam:", "Plain language:",
                     "Stay within what is true:"):
            self.assertIn(part, card)
        self.assertNotRegex(card, r"\{[A-Z_]+\}")
        self.assertNotIn("<!--", card)
        self.assertIn("full name, Sam", card)
        self.assertIn("Never say a placeholder", card)
        self.assertLess(len(card.split()), 520)   # ≈ 700 tokens

    def test_missing_voice_card_fails_before_ringing(self):
        bridge, sock = FakeBridge(), FakeSocket()
        rt, posted = _runtime(bridge, sock)
        with unittest.mock.patch.object(lc, "VOICE_CARD_PATH", "/nonexistent/live_call_voice.md"):
            result = rt.place("x", SETTINGS)
        self.assertEqual(result["status"], "failed")
        self.assertIn("voice card", result["error"])
        self.assertEqual(bridge.calls, [])
        self.assertEqual(posted, [])

    def test_start_context_brief_profile_games(self):
        items = lc.call_start_context(
            pu_name="Sam", brief="Need a yes or no on moving QA to Friday; web-dev is free then.",
            profile="timezone: America/New_York; role: founder",
            games=[{"name": "Ship 2.4", "postulate": "Live calls in the stores", "posture": "aggressive"},
                   {"name": "Health", "postulate": "", "posture": "defensive"}],
            last_call="2026-09-26 (4.2 min, ended) — Chose Friday.",
        )
        self.assertEqual(len(items), 2)
        self.assertTrue(items[0].startswith("COA's brief for this call: Need a yes or no"))
        self.assertIn("What you know about Sam: timezone", items[1])
        self.assertIn("Ship 2.4 (Live calls in the stores) — tone: brisk and decisive", items[1])
        self.assertIn("Health — tone: calm, reassuring", items[1])
        self.assertIn("Your last call with Sam", items[1])
        self.assertNotIn("posture", " ".join(items).lower())
        self.assertEqual(lc.call_start_context(pu_name="Sam"), [])

    def test_account_profile_from_versavoice(self):
        abilities = [{"name": f"Skill {i}", "level": i} for i in range(1, 12)]
        abilities.append({"name": "Cooking", "level": 9})
        line = lc.account_profile_line({
            "dateOfBirth": "1980-05-12T00:00:00.000Z",
            "countryOfBirth": "South Africa",
            "nearestCity": "Austin",
            "stateOrProvince": "Texas",
            "countryOfResidence": "United States",
            "chromosome": "X",
            "abilities": abilities,
        })
        self.assertIn("born 12 May 1980 in South Africa", line)
        self.assertIn("lives in Austin, Texas, United States", line)
        self.assertIn("voice setting: male voice", line)
        self.assertNotIn("chromosome", line.lower())
        self.assertNotRegex(line, r"\bX\b")
        shown = line.split("abilities: ", 1)[1].split(", ")
        self.assertEqual(len(shown), 10)
        self.assertEqual(shown[0], "Skill 10 (strong)")
        self.assertIn("Cooking (strong)", shown)
        self.assertNotIn("Skill 1 (some)", line)
        self.assertNotIn("Skill 11", line)
        self.assertEqual(lc.account_profile_line({"chromosome": "Y"}), "voice setting: female voice")
        self.assertEqual(lc.account_profile_line({"chromosome": "Reflective"}), "voice setting: your voice")
        self.assertEqual(lc.account_profile_line({}), "")
        self.assertEqual(lc.account_profile_line({"abilities": "not json"}), "")

    def test_context_drives_session_and_last_call(self):
        bridge, sock = FakeBridge(), FakeSocket()
        posted = []

        def http_post(url, body, headers):
            posted.append(body)
            return 201, json.dumps({"session": {"id": "live_x"}, "transport": {"sdp": "ans"}})

        rt = lc.LiveCallRuntime(
            agent_label="Versa", pu_name="Sam", api_key_resolver=lambda: "k", bridge=bridge,
            sideband_factory=lambda k, s: sock, http_post=http_post, log=lambda m: None,
            context_provider=lambda: {
                "language": lc.CallLanguage("fr", "French", "supported", "French"),
                "style_notes": "", "last_call": "2026-09-26 04:41 UTC (4.2 min, ended) — Chose Friday.",
                "pu_profile": "role: founder",
                "games": [{"name": "Ship 2.4", "postulate": "", "posture": "steady"}],
            },
        )
        self.assertEqual(rt.place("x", SETTINGS, brief="QA date: Friday or Monday?")["status"], "connected")
        self.assertIn("Speak French", posted[0]["session"]["instructions"])
        thinking = [e["content"] for e in sock.sent if e["type"] == "session.thinking.append"]
        self.assertEqual(thinking[0], "COA's brief for this call: QA date: Friday or Monday?")
        self.assertIn("role: founder", thinking[1])
        self.assertIn("Ship 2.4 — tone: calm and methodical", thinking[1])
        self.assertIn("Chose Friday", thinking[1])
        rt.shutdown()

    def test_connection_call_omits_pu_profile(self):
        bridge, sock = FakeBridge(), FakeSocket()
        posted = []

        def http_post(url, body, headers):
            posted.append(body)
            return 201, json.dumps({"session": {"id": "live_x"}, "transport": {"sdp": "ans"}})

        rt = lc.LiveCallRuntime(
            agent_label="Versa", pu_name="Sam", api_key_resolver=lambda: "k", bridge=bridge,
            sideband_factory=lambda k, s: sock, http_post=http_post, log=lambda m: None,
            context_provider=lambda: {
                "language": lc.CallLanguage("fr", "French", "supported", "French"),
                "style_notes": "short updates", "last_call": "Chose Friday.",
                "pu_profile": "role: founder",
                "games": [{"name": "Ship 2.4", "postulate": "", "posture": "steady"}],
            },
        )
        result = rt.place("Checking in.", SETTINGS, brief="The invoice is ready.",
                          recipient_id="contact_1")
        self.assertEqual(result["status"], "connected")
        self.assertEqual(bridge.calls[0][0],
                         ["open", "--reason", "Checking in.", "--recipient", "contact_1"])
        instructions = posted[0]["session"]["instructions"]
        self.assertIn("Ashok Patel", instructions)
        self.assertIn("Do not mention the Primary User", instructions)
        self.assertNotIn("role: founder", instructions)
        self.assertNotIn("Speak French", instructions)
        thinking = [e["content"] for e in sock.sent if e["type"] == "session.thinking.append"]
        self.assertEqual(len(thinking), 1)
        self.assertIn("The invoice is ready.", thinking[0])
        self.assertNotIn("founder", thinking[0])
        rt.shutdown()

    def test_unconnected_recipient_does_not_ring(self):
        bridge, sock = FakeBridge(refuse=True), FakeSocket()
        rt, posted = _runtime(bridge, sock)
        result = rt.place("Hello.", SETTINGS, recipient_id="stranger")
        self.assertEqual(result["status"], "failed")
        self.assertIn("not connected", result["error"])
        self.assertEqual(bridge.ops(), ["open"])
        self.assertEqual(posted, [])
        self.assertIsNone(rt.session)

    def test_summary_captured_after_call_ended(self):
        bridge, sock = FakeBridge(), FakeSocket()
        rt, _ = _runtime(bridge, sock)
        rt.place("x", SETTINGS)
        sock.push({"type": "session.closed", "reason": "remote_hangup", "usage": {"seconds": 30}})
        ended = rt.next_injection("")
        self.assertIn("CALL SUMMARY:", ended)
        self.assertIn("Create a task", ended)
        self.assertTrue(rt.awaiting_summary)
        rt.capture_summary("Sent the summary.\nCALL SUMMARY: Discussed task 12; QA moved to Friday. Next: web-dev runs QA.")
        args, stdin = bridge.calls[-1]
        self.assertEqual(args, ["summary", "call_1"])
        self.assertEqual(stdin, "Discussed task 12; QA moved to Friday. Next: web-dev runs QA.")
        self.assertFalse(rt.awaiting_summary)
        rt.capture_summary("again")
        self.assertEqual(bridge.calls[-1][0], ["summary", "call_1"])
        self.assertEqual(len([a for a, _ in bridge.calls if a[0] == "summary"]), 1)

    def _narrating_runtime(self, narrate=True):
        clock = FakeClock()
        bridge, sock = FakeBridge(), FakeSocket()
        rt, _ = _runtime(bridge, sock, clock=clock)
        settings = LiveCallSettings(**{**SETTINGS.__dict__, "narrate_progress": narrate})
        rt.place("x", settings)
        sock.push({"type": "session.delegation.created", "delegation": {"id": "item_n"}})
        text = rt.next_injection("")
        return rt, sock, clock, text

    def test_narration_is_spoken_with_pacing(self):
        rt, sock, clock, text = self._narrating_runtime()
        self.assertIn("spoken while they wait", text)
        rt.note_tool_calls(["agictl_task"], "Let me look at your tasks.")          # too soon
        clock.now += 4
        rt.note_tool_calls(["agictl_task"], "Let me pull up the QA schedule.")     # spoken
        clock.now += 3
        rt.note_tool_calls(["agictl_project"], "Checking the project too.")        # gap too short
        clock.now += 9
        rt.note_tool_calls(["agictl_task"], [{"type": "text", "text": "Almost there."}])  # spoken
        rt.note_tool_calls(["agictl_task"], "")                                    # no text → quiet
        spoken = [e["content"] for e in sock.sent if e["type"] == "session.commentary.append"]
        quiet = [e["content"] for e in sock.sent if e["type"] == "session.thinking.append"]
        self.assertEqual(spoken, ["Let me pull up the QA schedule.", "Almost there."])
        self.assertIn("Let me look at your tasks.", quiet)
        self.assertIn("Checking the project too.", quiet)
        self.assertIn("Working on it: agictl_task.", quiet)
        rt.shutdown()

    def test_narration_off_keeps_progress_quiet(self):
        rt, sock, clock, text = self._narrating_runtime(narrate=False)
        self.assertNotIn("spoken while they wait", text)
        clock.now += 10
        rt.note_tool_calls(["agictl_task"], "Let me pull up the QA schedule.")
        self.assertFalse([e for e in sock.sent if e["type"] == "session.commentary.append"])
        rt.shutdown()

    def test_parse_reply(self):
        reply = lc.parse_reply(
            "Friday is set.\nNOTE: QA owner is web-dev\n`STEER: ask whether Friday also works for web-dev`\n"
            "FOLLOW UP: check web-dev's queue and report back"
        )
        self.assertEqual(reply.spoken, "Friday is set.")
        self.assertEqual(reply.note, "QA owner is web-dev")
        self.assertEqual(reply.steer, "ask whether Friday also works for web-dev")
        self.assertEqual(reply.follow_up, "check web-dev's queue and report back")
        self.assertEqual(lc.parse_reply("Plain answer.").spoken, "Plain answer.")

    def _one_delegation(self, rt, sock, did="item_s"):
        sock.push({"type": "session.delegation.created", "delegation": {"id": did}})
        return rt.next_injection("")

    def test_steer_note_and_unprompted_follow_up(self):
        bridge, sock = FakeBridge(), FakeSocket()
        rt, _ = _runtime(bridge, sock, **_fake_time())
        rt.place("x", SETTINGS)
        first = self._one_delegation(rt, sock)
        self.assertIn("FOLLOW UP:", first)
        follow = rt.next_injection(
            "One moment, I'll check with web-dev.\nNOTE: web-dev owns QA\n"
            "STEER: keep them company briefly while you check\nFOLLOW UP: check web-dev's queue"
        )
        sent = sock.sent
        commentary = [e for e in sent if e["type"] == "session.commentary.append"]
        self.assertEqual(commentary[-1], {**commentary[-1], "delegation_id": "item_s",
                                          "content": "One moment, I'll check with web-dev."})
        self.assertTrue(any(e["type"] == "session.thinking.append" and e["content"] == "web-dev owns QA"
                            for e in sent))
        steer = [e for e in sent if e["type"] == "session.instructions.append"]
        self.assertEqual(steer[-1]["content"],
                         "Guidance from COA (the backend): keep them company briefly while you check")
        self.assertIn("follow-up you promised", follow)
        self.assertIn("check web-dev's queue", follow)
        self.assertTrue(rt.in_turn())
        rt.note_tool_calls(["agictl_task"], "")
        self.assertIsNone([e for e in sock.sent if e["type"] == "session.thinking.append"][-1]["delegation_id"])

        threading.Timer(0.3, sock.push, args=({"type": "session.closed", "reason": "remote_hangup"},)).start()
        rt.next_injection("web-dev can run QA Friday morning.")
        report = [e for e in sock.sent if e["type"] == "session.instructions.append"][-1]
        self.assertIsNone(report["delegation_id"])
        self.assertIn("Tell Sam now", report["content"])
        self.assertIn("web-dev can run QA Friday morning.", report["content"])
        self.assertNotIn("web-dev can run QA Friday morning.",
                         [e["content"] for e in sock.sent if e["type"] == "session.commentary.append"])
        self.assertTrue(rt.delegations["follow_up_1"].spoken)
        snap = json.loads([s for a, s in bridge.calls if a[0] == "end"][0])
        self.assertEqual([d["delegation_id"] for d in snap["delegations"]], ["item_s", "follow_up_1"])
        self.assertEqual(snap["delegations"][0]["follow_up"], "check web-dev's queue")

    def _hang_up_soon(self, sock, delay=0.3):
        threading.Timer(delay, sock.push, args=({"type": "session.closed", "reason": "remote_hangup"},)).start()

    def test_follow_ups_are_capped_at_two(self):
        bridge, sock = FakeBridge(), FakeSocket()
        rt, _ = _runtime(bridge, sock, **_fake_time())
        rt.place("x", SETTINGS)
        self._one_delegation(rt, sock, "item_1")
        nxt = rt.next_injection("Checking.\nFOLLOW UP: look at the build logs")
        self.assertIn("Follow-up: look at the build logs", nxt)
        self.assertIn("FOLLOW UP:", nxt)
        nxt = rt.next_injection("Logs are clean.\nFOLLOW UP: second")
        self.assertIn("Follow-up: second", nxt)
        self.assertNotIn("FOLLOW UP:", nxt)                # last one: this reply is the report
        self.assertIn("No follow-ups are left on this call", nxt)
        self._hang_up_soon(sock)
        ended = rt.next_injection("Second done.\nFOLLOW UP: third")
        self.assertIn("LIVE CALL ENDED", ended)           # third not run: cap of 2
        self.assertIn("did not run: third", ended)
        self.assertEqual(rt.follow_ups_used, 2)

    def test_delegation_offers_follow_up_only_while_under_cap(self):
        self.assertIn("FOLLOW UP:", lc.delegation_message(["d"], [], "x", follow_ups_left=1))
        spent = lc.delegation_message(["d"], [], "x", follow_ups_left=0)
        self.assertNotIn("FOLLOW UP:", spent)
        self.assertIn("No follow-ups are left on this call", spent)

    def test_correction_mid_turn_keeps_stale_answer_quiet(self):
        bridge, sock = FakeBridge(), FakeSocket()
        rt, _ = _runtime(bridge, sock, **_fake_time())
        rt.place("x", SETTINGS)
        self._one_delegation(rt, sock, "item_1")
        sock.push({"type": "session.input_transcript.delta", "delta": "Actually, make it Thursday."})
        sock.push({"type": "session.delegation.created", "delegation": {"id": "item_2"}})
        time.sleep(0.2)
        nxt = rt.next_injection("QA is set for Friday.\nFOLLOW UP: tell web-dev")
        self.assertIn("item_2", nxt)
        self.assertIn("was NOT spoken", nxt)
        self.assertIn("QA is set for Friday.", nxt)
        self.assertIn("PU: Actually, make it Thursday.", nxt)
        self.assertIn("Delivery check — your report \"QA is set for Friday.\"", nxt)
        spoken = [e["content"] for e in sock.sent if e["type"] == "session.commentary.append"]
        self.assertNotIn("QA is set for Friday.", spoken)
        handed = [e["content"] for e in sock.sent if e["type"] == "session.instructions.append"]
        self.assertTrue(any("If this still answers" in c and "QA is set for Friday." in c for c in handed))
        self.assertEqual(rt.pending_follow_up, "")
        self._hang_up_soon(sock)
        rt.next_injection("QA is set for Thursday.")
        spoken = [e["content"] for e in sock.sent if e["type"] == "session.commentary.append"]
        self.assertEqual(spoken[-1], "QA is set for Thursday.")

    def test_repeat_delegation_is_answered_quietly(self):
        bridge, sock = FakeBridge(), FakeSocket()
        rt, _ = _runtime(bridge, sock)
        rt.place("x", SETTINGS)
        sock.push({"type": "session.input_transcript.delta", "delta": "What's task 12 about?"})
        self._one_delegation(rt, sock, "item_1")
        sock.push({"type": "session.delegation.created", "delegation": {"id": "item_1b"}})
        self._hang_up_soon(sock, delay=2.5)
        ended = rt.next_injection("Task 12 is the Live Call POC.")
        self.assertIn("LIVE CALL ENDED", ended)
        repeat = [e for e in sock.sent if e["type"] == "session.thinking.append" and e["delegation_id"] == "item_1b"]
        self.assertIn("already answered: Task 12 is the Live Call POC.", repeat[0]["content"])
        self.assertTrue(rt.delegations["item_1b"].duplicate)
        spoken = [e["content"] for e in sock.sent if e["type"] == "session.commentary.append"]
        self.assertEqual(spoken.count("Task 12 is the Live Call POC."), 1)

    def test_prompt_block_and_last_call_line(self):
        line = lc.last_call_line({"created_at": "2026-09-26T04:41:10Z", "voice_seconds": 252,
                                  "status": "ended", "summary": "Chose Friday.", "reason": "x"})
        self.assertEqual(line, "2026-09-26 04:41 UTC (4.2 min, ended) — Chose Friday.")
        self.assertEqual(lc.last_call_line(None), "")
        ready = lc.prompt_block(ready=True, reasons="ready", call_model="Google — Gemini 3.7 Flash",
                                language=lc.ENGLISH, last_call=line)
        self.assertIn("## ── LIVE CALL", ready)
        self.assertIn("agictl_call_pu", ready)
        self.assertIn("Last live call: 2026-09-26", ready)
        self.assertIn("up to 1 call per cycle", ready)
        self.assertIn("up to 3 calls per cycle",
                      lc.prompt_block(ready=True, reasons="ready", call_model="m", language=lc.ENGLISH,
                                      last_call="", calls_per_cycle=3))
        off = lc.prompt_block(ready=False, reasons="no call model set", call_model="none",
                              language=lc.ENGLISH, last_call="")
        self.assertIn("Do not offer calls", off)
        self.assertIn("Last live call: none yet", off)


class TestAgentHangup(unittest.TestCase):
    """§3.6 P3-D: COA ends the call with END CALL once the voice has finished."""

    def _connected(self):
        clock = FakeClock()

        def sleep(seconds):
            clock.now += seconds
            time.sleep(0.005)

        bridge, sock = FakeBridge(), FakeSocket()
        rt, _ = _runtime(bridge, sock, clock=clock, sleep=sleep)
        self.assertEqual(rt.place("QA date needs confirming.", SETTINGS)["status"], "connected")
        sock.push({"type": "session.input_transcript.delta", "delta": "That's all, thanks. Bye!"})
        sock.push({"type": "session.delegation.created", "delegation": {"id": "item_bye"}})
        self.assertIn("END CALL", rt.next_injection(""))
        return rt, bridge, sock, clock

    def _end_args(self, bridge):
        return [a for a, _ in bridge.calls if a[0] == "end"][0]

    def test_parse_end_call_variants(self):
        for text in ("Bye Sam!\nEND CALL", "Bye Sam!\n`END CALL`", "Bye Sam!\nend call.", "Bye Sam!\nEND CALL:"):
            reply = lc.parse_reply(text)
            self.assertTrue(reply.end_call, text)
            self.assertEqual(reply.spoken, "Bye Sam!")
        self.assertFalse(lc.parse_reply("I'll end call notes later.").end_call)

    def test_voice_card_says_only_backend_hangs_up(self):
        card = lc.voice_instructions("Versa", "Sam", "x")
        self.assertIn('delegate "end the call"', card)
        self.assertIn("never say you will hang up", card)

    def test_goodbye_then_close_as_agent_hangup(self):
        rt, bridge, sock, clock = self._connected()
        ended = rt.next_injection("Great, talk soon Sam!\nEND CALL")
        spoken = [e["content"] for e in sock.sent if e["type"] == "session.commentary.append"]
        self.assertEqual(spoken[-1], "Great, talk soon Sam!")
        self.assertIn("session.close", sock.sent_types())
        self.assertIn("LIVE CALL ENDED (agent_hangup", ended)
        self.assertGreaterEqual(clock.now - 1000.0, lc.HANGUP_SPEECH_START_SECONDS)
        args = self._end_args(bridge)
        self.assertEqual(args[args.index("--close-reason") + 1], "agent_hangup")
        snap = json.loads([s for a, s in bridge.calls if a[0] == "end"][0])
        self.assertTrue(snap["delegations"][-1]["end_call"])
        self.assertIsNone(rt.next_injection("CALL SUMMARY: done"))

    def test_bare_end_call_closes_without_speaking(self):
        rt, bridge, sock, clock = self._connected()
        before = len([e for e in sock.sent if e["type"] == "session.commentary.append"])
        ended = rt.next_injection("END CALL")
        after = len([e for e in sock.sent if e["type"] == "session.commentary.append"])
        self.assertEqual(before, after)
        self.assertTrue(any(e["type"] == "session.thinking.append" and "ending the call" in e["content"]
                            for e in sock.sent))
        self.assertIn("agent_hangup", ended)
        self.assertLess(clock.now - 1000.0, lc.HANGUP_SPEECH_START_SECONDS)

    def test_waits_for_goodbye_to_trail_off(self):
        rt, bridge, sock, clock = self._connected()
        sock.push({"type": "session.output_transcript.delta", "delta": "Bye for now,"})
        time.sleep(0.1)
        rt.next_injection("END CALL")
        self.assertGreaterEqual(clock.now - 1000.0, lc.HANGUP_QUIET_SECONDS - 0.01)
        self.assertLess(clock.now - 1000.0, lc.HANGUP_MAX_WAIT_SECONDS)

    def test_end_call_waits_for_follow_up_report(self):
        rt, bridge, sock, clock = self._connected()
        nxt = rt.next_injection("One moment, I'll check the logs.\nFOLLOW UP: check the build logs\nEND CALL")
        self.assertIn("Follow-up: check the build logs", nxt)
        self.assertIn("last report before the hang-up", nxt)
        self.assertNotIn("FOLLOW UP:", nxt)
        self.assertNotIn("session.close", sock.sent_types())
        self.assertTrue(rt.end_after_follow_up)
        ended = rt.next_injection("The build logs are clean.\nFOLLOW UP: rerun the suite")
        report = [e for e in sock.sent if e["type"] == "session.instructions.append"][-1]
        self.assertIn("The build logs are clean.", report["content"])
        self.assertIn("session.close", sock.sent_types())
        self.assertIn("LIVE CALL ENDED (agent_hangup", ended)
        self.assertIn("did not run: rerun the suite", ended)
        self.assertEqual(rt.follow_ups_used, 1)

    def test_follow_up_goes_to_chat_when_pu_hangs_up_first(self):
        rt, bridge, sock, clock = self._connected()
        sock.push({"type": "session.closed", "reason": "remote_hangup"})
        time.sleep(0.2)
        ended = rt.next_injection("I'll check the logs and message you.\nFOLLOW UP: check the build logs\nEND CALL")
        self.assertIn("did not run: check the build logs", ended)
        self.assertEqual(rt.follow_ups_used, 0)

    def test_report_ready_after_close_goes_to_chat(self):
        bridge, sock = FakeBridge(), FakeSocket()
        rt, _ = _runtime(bridge, sock)
        rt.place("x", SETTINGS)
        sock.push({"type": "session.delegation.created", "delegation": {"id": "item_1"}})
        rt.next_injection("")
        rt.next_injection("Checking.\nFOLLOW UP: look at the build logs")
        sock.push({"type": "session.closed", "reason": "remote_hangup"})
        time.sleep(0.2)
        ended = rt.next_injection("The build logs are clean.")
        self.assertIn("they did not hear it: The build logs are clean.", ended)

    def test_superseded_end_call_is_ignored(self):
        rt, bridge, sock, clock = self._connected()
        sock.push({"type": "session.input_transcript.delta", "delta": "Oh wait, one more thing."})
        sock.push({"type": "session.delegation.created", "delegation": {"id": "item_more"}})
        time.sleep(0.2)
        nxt = rt.next_injection("Bye!\nEND CALL")
        self.assertIn("item_more", nxt)
        self.assertNotIn("session.close", sock.sent_types())
        self.assertFalse(rt.hangup_requested)
        rt.shutdown()


def _voice(text):
    return {"type": "session.output_transcript.delta", "delta": text}


def _pu(text):
    return {"type": "session.input_transcript.delta", "delta": text}


class TestDeliveryCheck(unittest.TestCase):
    """G25: a report handed to the voice is checked against what the voice said."""

    def _call(self, voice):
        bridge, sock = FakeBridge(), FakeSocket()
        sock.voice = voice
        rt, _ = _runtime(bridge, sock, **_fake_time())
        rt.place("x", SETTINGS)
        sock.push({"type": "session.delegation.created", "delegation": {"id": "item_1"}})
        rt.next_injection("")
        return rt, sock

    def _later(self, sock, *events, delay=0.4):
        def push():
            for event in events:
                sock.push(event)
        threading.Timer(delay, push).start()

    def _reports(self, sock):
        return [e for e in sock.sent
                if e["type"] == "session.instructions.append" and "Tell Sam now" in e["content"]]

    def test_spoken_report_is_confirmed_to_coa(self):
        rt, sock = self._call([[_voice("Build logs are clean, Sam. Does that answer it?")]])
        rt.next_injection("Checking.\nFOLLOW UP: look at the build logs")
        self._later(sock, _pu("Great. And QA?"),
                    {"type": "session.delegation.created", "delegation": {"id": "item_2"}})
        nxt = rt.next_injection("The build logs are clean.")
        self.assertIn("item_2", nxt)
        self.assertIn('your report "The build logs are clean.": the voice spoke after it was sent '
                      "and was not interrupted", nxt)
        self.assertIn('The voice said: "Build logs are clean, Sam. Does that answer it?"', nxt)
        self.assertIn("check briefly that it answers", self._reports(sock)[0]["content"])
        self.assertEqual(len(self._reports(sock)), 1)
        self.assertEqual(rt.delegations["follow_up_1"].delivery, lc.DELIVERY_SPOKEN)
        rt.shutdown()

    def test_unspoken_report_is_sent_again_then_flagged(self):
        rt, sock = self._call([[], []])
        rt.next_injection("Checking.\nFOLLOW UP: look at the build logs")
        self._later(sock, _pu("Hello?"),
                    {"type": "session.delegation.created", "delegation": {"id": "item_2"}})
        nxt = rt.next_injection("The build logs are clean.")
        self.assertEqual(len(self._reports(sock)), 2)
        self.assertIn("the voice said nothing after it was sent", nxt)
        self.assertIn("restate it first in your reply", nxt)
        self.assertEqual(rt.delegations["follow_up_1"].delivery, lc.DELIVERY_NOT_SPOKEN)
        rt.shutdown()

    def test_cut_off_report_goes_back_to_coa(self):
        rt, sock = self._call([[_voice("The build logs are"), _pu("Wait, which build?")]])
        rt.next_injection("Checking.\nFOLLOW UP: look at the build logs")
        self._later(sock, {"type": "session.delegation.created", "delegation": {"id": "item_2"}})
        nxt = rt.next_injection("The build logs are clean.")
        self.assertEqual(len(self._reports(sock)), 1)       # the PU spoke: COA decides, no resend
        self.assertIn("they spoke over it", nxt)
        self.assertIn('The voice said: "The build logs are"', nxt)
        self.assertIn("PU: Wait, which build?", nxt)
        rt.shutdown()

    def test_unheard_closing_report_goes_to_chat(self):
        rt, sock = self._call([[], []])
        nxt = rt.next_injection("One moment.\nFOLLOW UP: check the build logs\nEND CALL")
        self.assertIn("last report before the hang-up", nxt)
        ended = rt.next_injection("The build logs are clean.")
        self.assertEqual(len(self._reports(sock)), 2)
        self.assertIn("LIVE CALL ENDED (agent_hangup", ended)
        self.assertIn("they did not hear it: The build logs are clean.", ended)

    def test_spoken_closing_report_hangs_up_without_chat_note(self):
        rt, sock = self._call([[_voice("Build logs are clean. Bye, Sam!")]])
        rt.next_injection("One moment.\nFOLLOW UP: check the build logs\nEND CALL")
        ended = rt.next_injection("The build logs are clean.")
        self.assertIn("LIVE CALL ENDED (agent_hangup", ended)
        self.assertNotIn("did not hear it", ended)


class TestSanitize(unittest.TestCase):
    def test_text_only_copies(self):
        human = HumanMessage(content=[{"type": "text", "text": "look"}, {"type": "image_url", "image_url": "data:..."}])
        ai = AIMessage(content=[{"type": "thinking", "thinking": "secret"}, {"type": "text", "text": "done"}])
        plain = HumanMessage(content="hi")
        out = lc.sanitize_for_call_model([human, ai, plain])
        self.assertEqual(out[0].content, "look\n[media omitted during call]")
        self.assertEqual(out[1].content, "done")
        self.assertIs(out[2], plain)
        self.assertIsInstance(human.content, list)


if __name__ == "__main__":
    unittest.main()
