"""A streamed reply touches the heartbeat as it arrives (state_long_cycles.md §1.2)."""

from __future__ import annotations

import os
import sys
import unittest

CORE_INFRA = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, CORE_INFRA)

try:
    from harness.stream_heartbeat import StreamHeartbeat
except ImportError as exc:  # the system interpreter has no LangChain
    raise unittest.SkipTest(f"LangChain not available: {exc}")


class TestStreamHeartbeat(unittest.TestCase):
    def test_beats_on_the_first_piece_then_at_most_every_gap(self):
        now = [100.0]
        beats = []
        handler = StreamHeartbeat(lambda: beats.append(now[0]), min_gap=5.0, clock=lambda: now[0])
        for step in (0.0, 1.0, 4.9, 5.0, 6.0, 10.1):
            now[0] = 100.0 + step
            handler.on_llm_new_token("x")
        self.assertEqual(beats, [100.0, 105.0, 110.1])
        self.assertEqual(handler.pieces, 6)

    def test_a_long_reply_keeps_beating(self):
        """Twenty minutes of pieces, one every 20 s: the file is never 10 minutes old."""
        now = [0.0]
        beats = []
        handler = StreamHeartbeat(lambda: beats.append(now[0]), clock=lambda: now[0])
        for second in range(0, 1200, 20):
            now[0] = float(second)
            handler.on_llm_new_token("")
        gaps = [b - a for a, b in zip(beats, beats[1:])]
        self.assertEqual(len(beats), 60)
        self.assertLessEqual(max(gaps), 20.0)

    def test_the_harness_gives_a_streaming_client_this_callback(self):
        """Source check: the wiring needs a model to run."""
        with open(os.path.join(CORE_INFRA, "harness", "agent_harness.py"), encoding="utf-8") as handle:
            source = handle.read()
        self.assertIn('if native.get("streaming"):', source)
        self.assertIn("callbacks = [StreamHeartbeat(lambda: _HEARTBEAT.beat())]", source)
        self.assertIn("callbacks=callbacks,", source)


if __name__ == "__main__":
    unittest.main()
