"""Checkbox with distinct on/off glyphs (✓ vs □).

Textual's stock Checkbox uses BUTTON_INNER="X" for both states and only
changes color (green vs dim), which reads as "always checked."
"""

from __future__ import annotations

from textual.content import Content
from textual.style import Style
from textual.widgets import Checkbox


class ClearCheckbox(Checkbox):
    """Checkbox that shows a checkmark when on and an empty box when off."""

    BUTTON_INNER_ON = "✓"
    BUTTON_INNER_OFF = "□"

    @property
    def _button(self) -> Content:
        button_style = self.get_visual_style("toggle--button")
        side_style = Style(
            foreground=button_style.background,
            background=self.background_colors[1],
        )
        inner = self.BUTTON_INNER_ON if self.value else self.BUTTON_INNER_OFF
        return Content.assemble(
            (self.BUTTON_LEFT, side_style),
            (inner, button_style),
            (self.BUTTON_RIGHT, side_style),
        )
