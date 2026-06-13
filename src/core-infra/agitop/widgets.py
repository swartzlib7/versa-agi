"""Shared agitop UI widgets."""

from textual.widgets import DataTable


class PaginatedDataTable(DataTable):
    """DataTable that routes PageUp/PageDown to a custom pagination callback."""

    def __init__(self, key_callback, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.key_callback = key_callback

    def on_key(self, event) -> None:
        if event.key in ("pageup", "pagedown"):
            event.prevent_default()
            event.stop()
            self.key_callback(event.key)
