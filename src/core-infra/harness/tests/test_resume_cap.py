"""Resume tail size from a model window (state_spawn_context.md SPWN-26)."""

import unittest

from harness.model_context import resume_message_cap


class TestResumeCap(unittest.TestCase):
    def test_table(self):
        self.assertEqual(resume_message_cap(1_000_000), 200)
        self.assertEqual(resume_message_cap(500_000), 104)
        self.assertEqual(resume_message_cap(256_000), 53)
        self.assertEqual(resume_message_cap(128_000), 27)
        self.assertEqual(resume_message_cap(64_000), 13)

    def test_unknown_placeholder_clamps_to_floor(self):
        self.assertEqual(resume_message_cap(4096), 12)
        self.assertEqual(resume_message_cap(0), 12)


if __name__ == "__main__":
    unittest.main()
