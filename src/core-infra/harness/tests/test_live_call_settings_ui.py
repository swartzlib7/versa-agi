"""Headless agitop test: System Settings → Live Call tab renders and saves.

Run:  cd core-infra
      /opt/versa-agi/venv/bin/python3 -m unittest harness.tests.test_live_call_settings_ui
"""

import asyncio
import os
import sys
import unittest
from unittest.mock import patch

_CORE_INFRA = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _CORE_INFRA)

from textual.app import App  # noqa: E402
from textual.widgets import Checkbox, Input, Select, Static, TabbedContent, TextArea  # noqa: E402

from agitop.panels import system_settings_modal as ssm  # noqa: E402

VIEW = {
    "enabled": False,
    "call_model": "",
    "max_minutes": 15,
    "join_timeout_seconds": 45,
    "calls_per_cycle": 1,
    "narrate_progress": True,
    "options": [("(none)", ""), ("Google — Gemini 3.7 Flash", "gemini-3.7-flash")],
    "status": "[bold yellow]● Not ready:[/] Live Call is off",
    "last_call": ssm._format_last_call({
        "created_at": "2026-09-26T04:41:10Z", "voice_seconds": 252, "status": "ended",
        "reason": "Task 12 is blocked", "summary": "Moved QA to Friday.",
    }),
    "last_transcript": "COA: Hi, a quick one about task 12.\nYou: Friday works.",
}


class _Host(App):
    def on_mount(self):
        self.push_screen(ssm.SystemSettingsModal())


class TestLiveCallTab(unittest.TestCase):
    def test_renders_and_saves(self):
        writes = []

        def fake_write(section, key, value):
            writes.append((section, key, value))
            return True, ""

        async def run():
            with patch.object(ssm, "_live_call_view", return_value=dict(VIEW)), \
                    patch.object(ssm, "_write_ini_value_err", side_effect=fake_write):
                app = _Host()
                async with app.run_test(size=(160, 60)) as pilot:
                    await pilot.pause()
                    screen = app.screen
                    screen.query_one("#settings-tabs", TabbedContent).active = "settings-live-call-tab"
                    await pilot.pause()
                    status = screen.query_one("#live-call-status", Static)
                    self.assertIn("Not ready", str(status.render()))
                    last = str(screen.query_one("#live-call-last", Static).render())
                    self.assertIn("4 min 12 s", last)
                    self.assertIn("Moved QA to Friday.", last)
                    transcript = screen.query_one("#live-call-transcript", TextArea)
                    self.assertTrue(transcript.read_only)
                    self.assertIn("You: Friday works.", transcript.text)
                    screen.query_one("#chk-live-call-narrate", Checkbox).value = False
                    screen.query_one("#chk-live-call-enabled", Checkbox).value = True
                    screen.query_one("#select-live-call-model", Select).value = "gemini-3.7-flash"
                    screen.query_one("#input-live-call-max-minutes", Input).value = "20"
                    per_cycle = screen.query_one("#input-live-call-per-cycle", Input)
                    self.assertEqual(per_cycle.value, "1")
                    per_cycle.value = "2"
                    self.assertTrue(screen._save_live_call())

        asyncio.run(run())
        self.assertEqual(writes, [
            ("live_call", "call_model", "gemini-3.7-flash"),
            ("live_call", "max_minutes", "20"),
            ("live_call", "join_timeout_seconds", "45"),
            ("live_call", "calls_per_cycle", "2"),
            ("live_call", "narrate_progress", "false"),
            ("features", "live_call", "true"),
        ])

    def test_no_calls_yet(self):
        self.assertEqual(ssm._format_last_call(None), "[dim]No calls yet.[/]")

    def test_enabling_without_model_is_refused(self):
        writes = []

        async def run():
            with patch.object(ssm, "_live_call_view", return_value=dict(VIEW)), \
                    patch.object(ssm, "_write_ini_value_err", side_effect=lambda *a: writes.append(a) or (True, "")):
                app = _Host()
                async with app.run_test(size=(160, 60)) as pilot:
                    await pilot.pause()
                    screen = app.screen
                    screen.query_one("#chk-live-call-enabled", Checkbox).value = True
                    self.assertFalse(screen._save_live_call())

        asyncio.run(run())
        self.assertEqual(writes, [])


if __name__ == "__main__":
    unittest.main()
