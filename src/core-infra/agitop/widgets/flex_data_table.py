"""DataTable with columns that stretch to fill available terminal width."""

from __future__ import annotations

from typing import Sequence

from textual import events
from textual.widgets import DataTable


class FlexDataTable(DataTable):
    """DataTable that redistributes leftover width across named flex columns.

    Fixed-width columns keep their ``add_column(..., width=N)`` sizes. Named
    flex columns share remaining space evenly on resize (Textual has no native
    flex column support).
    """

    def __init__(
        self,
        *args,
        flex_keys: Sequence[str] | None = None,
        min_flex_width: int = 12,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self._flex_keys = list(flex_keys or [])
        self._min_flex_width = max(4, int(min_flex_width))
        self._last_flex_widths: tuple[int, ...] | None = None

    def on_resize(self, event: events.Resize) -> None:
        self.apply_flex_widths(event.size.width)

    def on_show(self) -> None:
        self.call_after_refresh(self.apply_flex_widths)

    def apply_flex_widths(self, available: int | None = None) -> None:
        """Set flex column widths from leftover space after fixed columns."""
        if not self._flex_keys or not self.columns:
            return

        if available is None:
            try:
                available = self.scrollable_content_region.width
            except Exception:
                available = self.size.width
        if available is None or available <= 0:
            return

        pad = 2 * self.cell_padding
        flex_set = set(self._flex_keys)
        flex_cols = []
        fixed_total = 0

        for column in self.ordered_columns:
            key = column.key.value if column.key.value is not None else str(column.key)
            if key in flex_set:
                flex_cols.append(column)
            else:
                fixed_total += column.width + pad

        if self.show_row_labels and getattr(self, "_labelled_row_exists", False):
            try:
                fixed_total += self._label_column.get_render_width(self)
            except Exception:
                pass

        if not flex_cols:
            return

        # Leave a small gutter so a vertical scrollbar does not force wrap-clip.
        gutter = 1
        remaining = available - fixed_total - gutter - (len(flex_cols) * pad)
        n = len(flex_cols)
        min_total = self._min_flex_width * n
        if remaining < min_total:
            each, extra = self._min_flex_width, 0
        else:
            each = remaining // n
            extra = remaining - (each * n)

        widths: list[int] = []
        changed = False
        for i, column in enumerate(flex_cols):
            width = each + (1 if i < extra else 0)
            widths.append(width)
            if column.auto_width:
                column.auto_width = False
                changed = True
            if column.width != width:
                column.width = width
                changed = True

        width_tuple = tuple(widths)
        if changed or width_tuple != self._last_flex_widths:
            self._last_flex_widths = width_tuple
            self._require_update_dimensions = True
            self.refresh()
