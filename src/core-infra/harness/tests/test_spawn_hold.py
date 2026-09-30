"""Quota pause list and Lift. Files stay in a temp directory.

Run:  cd core-infra
      python3 -m unittest harness.tests.test_spawn_hold
"""

import asyncio
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

_CORE_INFRA = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _CORE_INFRA)

from textual.app import App  # noqa: E402
from textual.widgets import Button, Static  # noqa: E402

from agitop.data import system_reader  # noqa: E402
from agitop.panels import system_settings_modal as ssm  # noqa: E402


class TestSpawnHoldFiles(unittest.TestCase):
    def test_lists_future_holds_and_lifts_one_agent(self):
        now = 1_700_000_000
        with tempfile.TemporaryDirectory() as directory:
            coa = os.path.join(directory, "versa_agi_coa.cooldown")
            other = os.path.join(directory, "versa_agi_other.cooldown")
            expired = os.path.join(directory, "versa_agi_old.cooldown")
            with open(coa, "w", encoding="utf-8") as handle:
                handle.write(str(now + 3600))
            with open(other, "w", encoding="utf-8") as handle:
                handle.write(str(now + 120))
            with open(expired, "w", encoding="utf-8") as handle:
                handle.write(str(now - 5))

            holds = system_reader.list_spawn_holds(directory, now=now)
            self.assertEqual([row["agent"] for row in holds], ["coa", "other"])
            self.assertEqual(holds[0]["type"], "quota")
            self.assertEqual(holds[0]["remaining_seconds"], 3600)
            self.assertEqual(holds[1]["type"], "rate_limit")

            self.assertTrue(system_reader.lift_spawn_hold("coa", directory))
            self.assertFalse(os.path.exists(coa))
            self.assertTrue(os.path.exists(other))
            left = system_reader.list_spawn_holds(directory, now=now)
            self.assertEqual([row["agent"] for row in left], ["other"])

    def test_rejects_a_path_and_a_missing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertFalse(system_reader.lift_spawn_hold("../etc", directory))
            self.assertFalse(system_reader.lift_spawn_hold("", directory))
            self.assertTrue(system_reader.lift_spawn_hold("coa", directory))
            self.assertEqual(os.listdir(directory), [])


class _Host(App):
    CSS_PATH = os.path.join(_CORE_INFRA, "agitop", "agitop.tcss")

    def on_mount(self):
        self.push_screen(ssm.SystemSettingsModal())


class TestQuotaPauseSettings(unittest.TestCase):
    def test_note_and_lift_button(self):
        holds = [{
            "agent": "coa",
            "remaining_seconds": 3600,
            "type": "quota",
        }]
        lifted = []

        def fake_list(directory="/tmp", now=None):
            return list(holds)

        def fake_lift(agent, directory="/tmp"):
            lifted.append(agent)
            holds.clear()
            return True

        async def run():
            with patch.object(system_reader, "list_spawn_holds", side_effect=fake_list), \
                    patch.object(system_reader, "lift_spawn_hold", side_effect=fake_lift):
                app = _Host()
                async with app.run_test(size=(160, 60)) as pilot:
                    await pilot.pause()
                    screen = app.screen
                    texts = [str(widget.render()) for widget in screen.query(Static)]
                    self.assertTrue(
                        any("reaches its quota" in text and "1 hour" in text for text in texts),
                        texts,
                    )
                    button = screen.query_one("#btn-lift-cooldown-coa", Button)
                    row = screen.query_one("#spawn-hold-row-coa")
                    self.assertEqual(str(button.label), "Lift")
                    self.assertGreaterEqual(button.region.x, row.region.x)
                    self.assertLessEqual(
                        button.region.x + button.region.width,
                        row.region.x + row.region.width,
                    )
                    self.assertGreaterEqual(button.region.height, 3)
                    button.press()
                    await pilot.pause()
                    self.assertEqual(lifted, ["coa"])
                    empty = screen.query_one("#spawn-hold-empty", Static)
                    self.assertIn("No pause", str(empty.render()))

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
