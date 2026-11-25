"""Ambient atrium display — rain animation + README feature ticker."""

from __future__ import annotations

import random
from typing import Optional

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Static

# Segments from versa-agi/README.md § Key Features & Benefits (+ platform headlines)
_FEATURE_SEGMENTS = [
    "OS-Level Sandboxing",
    "Deterministic Cognitive Ledger",
    "Real-World Execution",
    "Human Communication Simulation",
    "Genuine Agent-Human Collaboration",
    "Native Emotional Intelligence",
    "Cross-Cultural AI Synchronization",
    "Compute-Zero Efficiency",
    "LangGraph Agent Harness",
    "agitop Mission Control",
    "Lifeline Scheduling",
    "Hybrid Local & Cloud AI",
    "uGPN Cross-Lingual Collaboration",
    "Task Triage",
    "Hybrid Skill Injection",
    "Circuit Breaker & Safety Gates",
]

_RAIN_CHARS = ("v", "V", "░", "▒", "▓")
_HEAD_STYLES = ("cyan", "dark cyan")
_TOTAL_LINES = 10
_RAIN_ROWS = 7  # lines 1–7 rain; line 8 ticker; lines 9–10 fall room in 10-line box
_TICK_SECONDS = 0.48
_TICKER_GAP = "          ·          "
_TICKER_CHARS_PER_FRAME = 4.0  # baseline 0.5 (1 char / 2 frames); +50% scroll speed


class _Drop:
    __slots__ = ("col", "y", "char", "head_style", "trail_len")

    def __init__(self, col: int, char: str, head_style: str, trail_len: int) -> None:
        self.col = col
        self.y = 0
        self.char = char
        self.head_style = head_style
        self.trail_len = trail_len


class _AtriumLink(Static):
    """Clickable centered link (footer ResetLink pattern)."""

    can_focus = False


class _AtriumStartLink(_AtriumLink):
    def on_click(self) -> None:
        self.app.query_one("#sys-atrium", AtriumPanel)._start_rain()


class _AtriumStopLink(_AtriumLink):
    def on_click(self) -> None:
        self.app.query_one("#sys-atrium", AtriumPanel)._stop_rain()


class AtriumPanel(Vertical):
    """Bordered atrium: idle prompt → rain + ticker; stop link resets."""

    can_focus = False

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._running = False
        self._timer = None
        self._tick_count = 0
        self._ticker_offset = 0
        self._ticker_scroll_accum = 0.0
        self._width = 48
        self._rain_rows = _RAIN_ROWS
        self._drops: list[_Drop] = []
        self._ticker_cycle = self._build_ticker_cycle()

    def compose(self) -> ComposeResult:
        with Vertical(classes="sys-atrium-box", id="sys-atrium-box"):
            yield Static("", id="sys-atrium-rain", markup=True)
            yield Static("", id="sys-atrium-ticker", markup=True)
            yield _AtriumStartLink("Make it rain...", id="atrium-start-link", classes="atrium-center-link")
        yield _AtriumStopLink("No more rain...", id="atrium-stop-link", classes="atrium-center-link atrium-stop-link")

    def on_mount(self) -> None:
        self._set_idle_view()

    def on_resize(self, event) -> None:
        if self._running and self.size.width > 0:
            self._init_rain(max(24, self.size.width - 2), _RAIN_ROWS)
            self._refresh_frame()

    @staticmethod
    def _build_ticker_cycle() -> str:
        parts: list[str] = []
        for segment in _FEATURE_SEGMENTS:
            parts.append(segment)
            parts.append(_TICKER_GAP)
        return "".join(parts)

    def _color_at(self, index: int) -> str:
        cycle_len = len(self._ticker_cycle)
        pos = index % cycle_len
        scan = 0
        for i, segment in enumerate(_FEATURE_SEGMENTS):
            end = scan + len(segment)
            if scan <= pos < end:
                return "cyan" if i % 2 == 0 else "bright_white"
            scan = end + len(_TICKER_GAP)
        return "dim"

    def _marquee_colored(self, width: int) -> str:
        cycle = self._ticker_cycle
        cycle_len = len(cycle)
        start = self._ticker_offset % cycle_len
        doubled = cycle + cycle
        window = doubled[start : start + width]
        if len(window) < width:
            window += doubled[: width - len(window)]

        parts: list[str] = []
        for i, ch in enumerate(window):
            color = self._color_at(start + i)
            parts.append(f"[{color}]{ch}[/]")
        return "".join(parts)

    def _init_rain(self, width: int, rain_lines: int) -> None:
        self._width = width
        self._rain_rows = rain_lines
        self._drops = []
        occupied: set[int] = set()
        for _ in range(min(width // 4, rain_lines)):
            col = random.randrange(width)
            while col in occupied and len(occupied) < width:
                col = random.randrange(width)
            occupied.add(col)
            self._drops.append(self._spawn_drop(col))

    @staticmethod
    def _spawn_drop(col: int) -> _Drop:
        return _Drop(
            col=col,
            char=random.choice(_RAIN_CHARS),
            head_style=random.choice(_HEAD_STYLES),
            trail_len=random.randint(2, 5),
        )

    @staticmethod
    def _trail_style(head_style: str, distance: int) -> str:
        if distance == 0:
            return head_style
        if distance == 1:
            return "dark cyan" if head_style == "cyan" else "dim"
        return "dim"

    def _tick_drops(self) -> None:
        for drop in self._drops:
            drop.y += 1
        self._drops = [
            d for d in self._drops if d.y - d.trail_len < self._rain_rows
        ]
        active_cols = {d.col for d in self._drops}
        for col in range(self._width):
            if col in active_cols:
                continue
            if random.random() < 0.045:
                self._drops.append(self._spawn_drop(col))

    def _render_rain(self) -> str:
        cells: list[list[Optional[tuple[str, str]]]] = [
            [None] * self._width for _ in range(self._rain_rows)
        ]
        for drop in self._drops:
            for dist in range(drop.trail_len):
                row = drop.y - dist
                if row < 0 or row >= self._rain_rows:
                    continue
                style = self._trail_style(drop.head_style, dist)
                cells[row][drop.col] = (drop.char, style)

        lines: list[str] = []
        for row in cells:
            parts: list[str] = []
            for cell in row:
                if cell is None:
                    parts.append(" ")
                else:
                    char, style = cell
                    parts.append(f"[{style}]{char}[/]")
            lines.append("".join(parts))
        return "\n".join(lines)

    def _refresh_frame(self) -> None:
        rain_view = self.query_one("#sys-atrium-rain", Static)
        ticker_view = self.query_one("#sys-atrium-ticker", Static)
        width = max(24, (rain_view.size.width or self.size.width or 48) - 2)
        if width != self._width:
            self._init_rain(width, _RAIN_ROWS)

        self._tick_count += 1
        self._ticker_scroll_accum += _TICKER_CHARS_PER_FRAME
        while self._ticker_scroll_accum >= 1.0:
            self._ticker_offset += 1
            self._ticker_scroll_accum -= 1.0

        self._tick_drops()
        rain_view.update(self._render_rain())
        ticker_view.update(self._marquee_colored(width))

    def _set_running_widgets(self, running: bool) -> None:
        rain_view = self.query_one("#sys-atrium-rain", Static)
        ticker_view = self.query_one("#sys-atrium-ticker", Static)
        start = self.query_one("#atrium-start-link", _AtriumStartLink)
        stop = self.query_one("#atrium-stop-link", _AtriumStopLink)

        rain_view.display = running
        ticker_view.display = running
        start.display = not running
        stop.display = running
        self.query_one("#sys-atrium-box", Vertical).set_class(not running, "sys-atrium-idle")

    def _set_idle_view(self) -> None:
        self._running = False
        if self._timer is not None:
            self._timer.stop()
            self._timer = None
        self._drops = []
        self._tick_count = 0
        self._ticker_offset = 0
        self._ticker_scroll_accum = 0.0
        self.query_one("#sys-atrium-rain", Static).update("")
        self.query_one("#sys-atrium-ticker", Static).update("")
        self._set_running_widgets(False)

    def _start_rain(self) -> None:
        self._running = True
        self._set_running_widgets(True)

        rain_view = self.query_one("#sys-atrium-rain", Static)
        width = max(24, (rain_view.size.width or self.size.width or 48) - 2)
        self._init_rain(width, _RAIN_ROWS)
        self._refresh_frame()

        if self._timer is None:
            self._timer = self.set_interval(_TICK_SECONDS, self._refresh_frame)

    def _stop_rain(self) -> None:
        self._set_idle_view()


AtriumDisplay = AtriumPanel
