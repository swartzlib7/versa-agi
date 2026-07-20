"""DataTable with PageUp/PageDown pagination callback."""

from textual.widgets import DataTable

# MacBooks have no dedicated PgUp/PgDn — Fn+↑/↓ usually sends pageup/pagedown,
# but some OrbStack/SSH TTYs don't. Ctrl+B / Ctrl+F are reliable fallbacks.
_PAGE_KEY_ALIASES = {
    "pageup": "pageup",
    "pagedown": "pagedown",
    "ctrl+b": "pageup",
    "ctrl+f": "pagedown",
}


class PaginatedDataTable(DataTable):
    """DataTable that routes PageUp/PageDown (and Mac-friendly aliases) to a callback."""

    def __init__(self, key_callback, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.key_callback = key_callback

    def on_key(self, event) -> None:
        mapped = _PAGE_KEY_ALIASES.get(event.key)
        if mapped:
            event.prevent_default()
            event.stop()
            self.key_callback(mapped)
