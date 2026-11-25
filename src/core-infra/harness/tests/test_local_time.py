"""Stored UTC ↔ local time (state_time_display.md §1, TIME-01)."""

import os
import sys
import time
import unittest
from datetime import datetime, timezone

CORE_INFRA = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, CORE_INFRA)

import local_time  # noqa: E402


class _Zone(unittest.TestCase):
    ZONE = "America/New_York"

    def setUp(self):
        self._old = os.environ.get("TZ")
        os.environ["TZ"] = self.ZONE
        time.tzset()

    def tearDown(self):
        if self._old is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = self._old
        time.tzset()


class TestShow(_Zone):
    def test_stored_utc_is_shown_local_with_the_zone(self):
        self.assertEqual(local_time.show("2026-11-13 16:30:00"), "2026-11-13 11:30 EST")
        self.assertEqual(local_time.show("2026-11-13 16:30:45", seconds=True), "2026-11-13 11:30:45 EST")
        self.assertEqual(local_time.show("2026-11-13 16:30:00", zone=False), "2026-11-13 11:30")

    def test_each_date_gets_its_own_offset(self):
        self.assertEqual(local_time.show("2026-07-04 16:30:00"), "2026-07-04 12:30 EDT")
        self.assertEqual(local_time.show("2027-04-01 13:00:00"), "2027-04-01 09:00 EDT")
        self.assertEqual(local_time.zone_label("2026-07-04 16:30:00"), "EDT")
        self.assertEqual(local_time.zone_label("2026-12-04 16:30:00"), "EST")

    def test_iso_forms_are_read(self):
        for stored in ("2026-11-13T16:30:00Z", "2026-11-13T16:30:00.848833+00:00", "2026-11-13 16:30",
                       "2026-11-13T11:30:00-05:00"):
            self.assertEqual(local_time.show(stored), "2026-11-13 11:30 EST", stored)

    def test_the_date_can_move_back_a_day(self):
        self.assertEqual(local_time.show("2026-11-14 02:10:00"), "2026-11-13 21:10 EST")
        self.assertEqual(local_time.show_date("2026-11-14 02:10:00"), "2026-11-13")
        # A date with no time is a calendar date (an invoice date): it is not shifted.
        self.assertEqual(local_time.show_date("2026-11-14"), "2026-11-14")
        self.assertEqual(local_time.show("2026-11-14"), "2026-11-14")
        self.assertEqual(local_time.localize({"due_date": "2026-11-14"})["due_date"], "2026-11-14")

    def test_text_that_is_not_a_time_is_left_as_it_is(self):
        self.assertEqual(local_time.show("tomorrow"), "tomorrow")
        self.assertEqual(local_time.show("2026-13-45 99:00:00"), "2026-13-45 99:00:00")
        self.assertEqual(local_time.show(""), "")
        self.assertEqual(local_time.show(None, missing="--"), "--")
        self.assertIsNone(local_time.parse_stored("soon"))

    def test_a_value_already_shown_as_local_is_not_converted_twice(self):
        shown = local_time.show("2026-11-13 16:30:00")
        self.assertEqual(local_time.show(shown), shown)


class TestTyped(_Zone):
    def test_no_zone_means_local(self):
        self.assertEqual(local_time.typed_to_store("2026-11-13 11:30"), "2026-11-13 16:30:00")
        self.assertEqual(local_time.typed_to_store("2026-11-13 11:30:15"), "2026-11-13 16:30:15")
        self.assertEqual(local_time.typed_to_store("2026-11-13T11:30:00"), "2026-11-13 16:30:00")

    def test_a_date_alone_is_the_start_of_that_local_day(self):
        self.assertEqual(local_time.typed_to_store("2026-11-13"), "2026-11-13 05:00:00")

    def test_the_offset_is_the_typed_dates_not_todays(self):
        # F8: typed in November for a date after the March change.
        self.assertEqual(local_time.typed_to_store("2027-04-01 09:00"), "2027-04-01 13:00:00")
        self.assertEqual(local_time.show(local_time.typed_to_store("2027-04-01 09:00")), "2027-04-01 09:00 EDT")
        self.assertEqual(local_time.local_to_store("2027-04-01 09:00:00"), "2027-04-01 13:00:00")

    def test_an_explicit_zone_is_that_instant(self):
        self.assertEqual(local_time.typed_to_store("2026-11-13T16:30:00Z"), "2026-11-13 16:30:00")
        self.assertEqual(local_time.typed_to_store("2026-11-13 16:30 UTC"), "2026-11-13 16:30:00")
        self.assertEqual(local_time.typed_to_store("2026-11-13 08:30 -08:00"), "2026-11-13 16:30:00")
        self.assertEqual(local_time.typed_to_store("2026-11-13 22:00+0530"), "2026-11-13 16:30:00")

    def test_a_shown_value_can_be_typed_back(self):
        shown = local_time.show("2026-11-13 16:30:00")           # "2026-11-13 11:30 EST"
        self.assertEqual(local_time.typed_to_store(shown), "2026-11-13 16:30:00")
        self.assertEqual(local_time.typed_to_store("2026-07-04 12:30 EDT"), "2026-07-04 16:30:00")

    def test_what_is_not_a_date_is_refused_with_the_formats(self):
        for bad in ("tomorrow", "in 2 hours", "11:30", "13/11/2026 11:30", "", "2026-11-13 25:00", "2026-02-30 10:00"):
            with self.assertRaises(ValueError, msg=bad) as ctx:
                local_time.parse_typed(bad)
            self.assertTrue(str(ctx.exception))
        with self.assertRaises(ValueError) as ctx:
            local_time.parse_typed("2026-11-13 11:30 PST")
        self.assertIn("Unknown time zone", str(ctx.exception))
        self.assertIn("YYYY-MM-DD HH:MM", str(self._error("next week")))

    def _error(self, text):
        try:
            local_time.parse_typed(text)
        except ValueError as exc:
            return exc
        self.fail("no error")

    def test_form_field_helper_never_raises(self):
        self.assertEqual(local_time.local_to_store("not a date"), "not a date")
        self.assertEqual(local_time.local_to_store(""), "")

    def test_stored_text_sorts_and_compares_like_the_database_expects(self):
        stored = local_time.typed_to_store("2026-11-13 11:30")
        self.assertRegex(stored, r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")


class TestBoundariesAndRows(_Zone):
    def test_month_starts_at_local_midnight(self):
        # 2026-11-30 21:00 EST is already December in UTC; the local month is still November.
        now = datetime(2026, 12, 1, 2, 0, tzinfo=timezone.utc)
        self.assertEqual(local_time.month_start_store(now), "2026-11-01 04:00:00")   # 1 Nov 00:00 EDT
        now = datetime(2026, 12, 1, 6, 0, tzinfo=timezone.utc)
        self.assertEqual(local_time.month_start_store(now), "2026-12-01 05:00:00")   # 1 Dec 00:00 EST

    def test_rows_are_converted_by_column_name(self):
        row = {"id": 7, "title": "Report 2026-11-13 16:30:00", "due_date": "2026-11-13 16:30:00",
               "wake_after": None, "created_at": "2026-11-13 02:10:00",
               "recent_progress": [{"created_at": "2026-11-13 16:31:00", "entry": "DONE"}]}
        out = local_time.localize(row)
        self.assertEqual(out["due_date"], "2026-11-13 11:30 EST")
        self.assertEqual(out["created_at"], "2026-11-12 21:10 EST")
        self.assertEqual(out["recent_progress"][0]["created_at"], "2026-11-13 11:31 EST")
        self.assertEqual(out["title"], "Report 2026-11-13 16:30:00")     # not a time column
        self.assertIsNone(out["wake_after"])
        self.assertEqual(row["due_date"], "2026-11-13 16:30:00")          # the input is not changed

    def test_a_hand_written_value_in_a_time_column_survives(self):
        self.assertEqual(local_time.localize({"due_date": "tomorrow"})["due_date"], "tomorrow")


class TestOtherZones(unittest.TestCase):
    """The same code east of UTC and on UTC itself."""

    def _with(self, zone, fn):
        old = os.environ.get("TZ")
        os.environ["TZ"] = zone
        time.tzset()
        try:
            return fn()
        finally:
            if old is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = old
            time.tzset()

    def test_johannesburg(self):
        self.assertEqual(self._with("Africa/Johannesburg", lambda: local_time.show("2026-11-13 16:30:00")),
                         "2026-11-13 18:30 SAST")
        self.assertEqual(self._with("Africa/Johannesburg", lambda: local_time.typed_to_store("2026-11-13 18:30")),
                         "2026-11-13 16:30:00")

    def test_utc_machine(self):
        self.assertEqual(self._with("UTC", lambda: local_time.show("2026-11-13 16:30:00")), "2026-11-13 16:30 UTC")
        self.assertEqual(self._with("UTC", lambda: local_time.typed_to_store("2026-11-13 16:30")),
                         "2026-11-13 16:30:00")


if __name__ == "__main__":
    unittest.main()
