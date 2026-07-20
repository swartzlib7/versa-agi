"""Strategy Modal — Game of Life strategic overview.

Unified strategic viewer with two sections:
  1. Games — Strategic pursuits
  2. Awareness — Conclusions + Actions (paginated)

Sub-modals:
  - GameDetailModal — Full game details with projects + opponents
  - AwarenessDetailModal — Full awareness entry details
"""

import os
import sys
_CORE_INFRA = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _CORE_INFRA not in sys.path:
    sys.path.insert(0, _CORE_INFRA)
import db_connect  # noqa: E402

import os
import sqlite3
import time
import json
from typing import Optional

from textual import on
from textual.app import ComposeResult
from textual.screen import ModalScreen
from textual.containers import Vertical, VerticalScroll, Horizontal
from textual.widgets import DataTable, Button, Static, Input, Select, TabbedContent, TabPane, TextArea

from agitop.data import TasksReader


_TZ = time.strftime("%Z")

# ── Pagination ──
# Page size scales with taller tab tables (2× prior max-heights).
_PAGE_SIZE = 40

# ── Color helpers ──

POSTURE_COLORS = {
    "exploratory": ("cyan", "🔵"),
    "steady": ("green", "🟢"),
    "aggressive": ("yellow", "🟡"),
    "defensive": ("red", "🔴"),
}

STATUS_COLORS = {
    "active": "green",
    "paused": "yellow",
    "archived": "dim",
}

AWARENESS_STATUS_COLORS = {
    "active": "green",
    "revised": "yellow",
    "superseded": "dim",
    "completed": "cyan",
}

AWARENESS_TYPE_ICONS = {
    "conclusion": "💡",
    "action": "⚡",
}


def _utc_to_local(utc_str: str) -> str:
    """Convert 'YYYY-MM-DD HH:MM:SS' UTC string to local timezone."""
    if not utc_str or utc_str == "--" or len(utc_str) < 16:
        return utc_str or "Never"
    from datetime import datetime, timezone
    try:
        dt = datetime.strptime(utc_str[:19], "%Y-%m-%d %H:%M:%S")
        dt = dt.replace(tzinfo=timezone.utc).astimezone()
        return dt.strftime("%Y-%m-%d %H:%M")
    except (ValueError, OSError):
        return utc_str


def _milestones_to_text(raw: str) -> str:
    if not raw:
        return ""
    try:
        ms = json.loads(raw)
        if isinstance(ms, list):
            return "\n".join(str(m) for m in ms)
    except (json.JSONDecodeError, TypeError):
        pass
    return raw


def _text_to_milestones(text: str) -> Optional[str]:
    text = (text or "").strip()
    if not text:
        return None
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return json.dumps(lines)


# ═══════════════════════════════════════════════════════
# Main Strategy Modal
# ═══════════════════════════════════════════════════════

class StrategyModal(ModalScreen):
    """Game of Life — Strategic Overview modal with Games and Awareness."""

    def __init__(self, tasks_reader: Optional[TasksReader], **kwargs):
        super().__init__(**kwargs)
        self._tasks_reader = tasks_reader
        self.selected_game_id: Optional[int] = None
        self.selected_awareness_id: Optional[int] = None
        # Pagination state
        self._awareness_page = 0
        # Awareness filters
        self._awareness_agent_filter = None   # None = All
        self._awareness_status_filter = None   # None = All
        self._awareness_type_filter = None     # None = All
        self._awareness_active_count: Optional[int] = None
        self._awareness_agent_names_cache: Optional[list[str]] = None
        self._cached_agent_option_values: Optional[tuple[str, ...]] = None
        self._suppress_agent_filter_event = False

    def _awareness_agent_options(self) -> list[tuple[str, str]]:
        options: list[tuple[str, str]] = [("All (Agent)", "all")]
        if self._tasks_reader:
            if self._awareness_agent_names_cache is None:
                self._awareness_agent_names_cache = (
                    self._tasks_reader.get_awareness_agent_names()
                )
            for name in self._awareness_agent_names_cache:
                options.append((name, name))
        return options

    def _sync_awareness_agent_filter(self, *, force: bool = False) -> None:
        """Refresh agent picklist only when the distinct agent set changes."""
        try:
            select = self.query_one("#awareness-agent-filter", Select)
        except Exception:
            return
        options = self._awareness_agent_options()
        option_values = tuple(value for _label, value in options)
        if not force and option_values == self._cached_agent_option_values:
            return
        self._cached_agent_option_values = option_values
        current = select.value or "all"
        if current not in option_values:
            current = "all"
            self._awareness_agent_filter = None
        self._suppress_agent_filter_event = True
        try:
            select.set_options(options)
            if select.value != current:
                select.value = current
        finally:
            self._suppress_agent_filter_event = False

    def _awareness_active_total(self) -> int:
        if self._awareness_active_count is None and self._tasks_reader:
            self._awareness_active_count = self._tasks_reader.count_active_awareness()
        return self._awareness_active_count or 0

    def compose(self) -> ComposeResult:
        with Vertical(id="strategy-dialog"):
            yield Static(
                "[bold]🎯  Game of Life — Strategic Overview[/]",
                id="strategy-dialog-title",
            )

            with TabbedContent(initial="strategy-games-tab", id="strategy-tabs"):
                with TabPane("Games", id="strategy-games-tab"):
                    with Vertical(id="strategy-games-pane"):
                        yield Static(
                            "[bold cyan]Games[/]",
                            id="games-section-header",
                        )
                        with VerticalScroll(id="strategy-games-scroll"):
                            yield DataTable(id="games-table", cursor_type="row")
                        yield Static("", id="games-strat-hint")
                        with Horizontal(classes="strategy-tab-actions"):
                            yield Button("Edit Selected", variant="primary", id="btn-strat-edit-game",
                                         disabled=True, classes="panel-btn")
                            yield Button("Delete Selected", variant="error", id="btn-strat-delete-game",
                                         disabled=True, classes="panel-btn")

                with TabPane("Awareness", id="strategy-awareness-tab"):
                    with Vertical(id="strategy-awareness-pane"):
                        yield Static(
                            "[bold cyan]Awareness[/]",
                            id="awareness-section-header",
                        )
                        with Horizontal(classes="awareness-filter-row"):
                            yield Select(
                                self._awareness_agent_options(),
                                value="all", id="awareness-agent-filter", allow_blank=False,
                            )
                            yield Select(
                                [("All (Type)", "all"), ("💡 Conclusion", "conclusion"),
                                 ("⚡ Action", "action")],
                                value="all", id="awareness-type-filter", allow_blank=False,
                            )
                            yield Select(
                                [("All (Status)", "all"), ("Active", "active"), ("Revised", "revised"),
                                 ("Superseded", "superseded"), ("Completed", "completed")],
                                value="all", id="awareness-status-filter", allow_blank=False,
                            )
                        with VerticalScroll(id="strategy-awareness-scroll"):
                            yield DataTable(id="awareness-table", cursor_type="row")
                        yield Static("", id="awareness-strat-hint")
                        with Horizontal(classes="strategy-tab-actions"):
                            yield Button("Edit Selected", variant="primary", id="btn-strat-edit-awareness",
                                         disabled=True, classes="panel-btn")
                            yield Button("Delete Selected", variant="error", id="btn-strat-delete-awareness",
                                         disabled=True, classes="panel-btn")

            with Horizontal(id="strategy-dialog-footer"):
                yield Button("Close", variant="default", id="btn-strategy-close",
                             classes="panel-btn dismiss-btn")

    def on_mount(self) -> None:
        # Setup Games table
        games_table = self.query_one("#games-table", DataTable)
        games_table.add_columns("ID", "Name", "Posture", "Autonomy", "Projects",
                                "Status", "Last Assessed")
        self._refresh_games()

        # Setup Awareness table
        awareness_table = self.query_one("#awareness-table", DataTable)
        awareness_table.add_columns("ID", "Agent", "Type", "Status",
                                    "Subject", "Content", "Created")
        self._refresh_awareness()
        self._cached_agent_option_values = tuple(
            value for _label, value in self._awareness_agent_options()
        )

    def _refresh_games(self) -> None:
        table = self.query_one("#games-table", DataTable)
        table.clear()
        self.selected_game_id = None
        self.query_one("#btn-strat-edit-game", Button).disabled = True
        self.query_one("#btn-strat-delete-game", Button).disabled = True
        self.query_one("#games-strat-hint", Static).update(
            "[dim]Select a row to edit or delete.[/]"
        )
        if not self._tasks_reader:
            return
        games = self._tasks_reader.get_all_games()
        for g in games:
            posture = g.get("posture", "exploratory")
            p_color, p_icon = POSTURE_COLORS.get(posture, ("white", "⚪"))
            autonomy = g.get("autonomy", "advisory")
            status = g.get("status", "active")
            s_color = STATUS_COLORS.get(status, "white")
            proj_count = self._tasks_reader.get_game_project_count(g["id"])
            proj_str = f"{proj_count} linked" if proj_count > 0 else "0"
            assessed = _utc_to_local(g.get("environment_assessed_at") or "")

            table.add_row(
                str(g["id"]),
                str(g.get("name", "Unnamed")),
                f"[{p_color}]{p_icon} {posture}[/]",
                autonomy,
                proj_str,
                f"[{s_color}]{status}[/]",
                assessed,
                key=str(g["id"])
            )
        if not games:
            table.add_row("", "[dim]No games defined[/]", "", "", "", "", "")

        active_count = self._tasks_reader.count_active_games() if self._tasks_reader else 0
        try:
            self.query_one("#games-section-header", Static).update(
                f"[bold cyan]Games ({active_count} active, {len(games)} total)[/]"
            )
        except Exception:
            pass

    def _refresh_awareness(self) -> None:
        table = self.query_one("#awareness-table", DataTable)
        table.clear()
        self.selected_awareness_id = None
        self.query_one("#btn-strat-edit-awareness", Button).disabled = True
        self.query_one("#btn-strat-delete-awareness", Button).disabled = True
        self.query_one("#awareness-strat-hint", Static).update(
            "[dim]Select a row to edit or delete.[/]"
        )
        if not self._tasks_reader:
            return

        # Resolve filters
        agent_f = self._awareness_agent_filter
        status_f = self._awareness_status_filter
        type_f = self._awareness_type_filter

        # Paginated query with filters (single DB round trip)
        offset = self._awareness_page * _PAGE_SIZE
        entries, total = self._tasks_reader.get_awareness_page(
            agent_name=agent_f, status=status_f, entry_type=type_f,
            limit=_PAGE_SIZE, offset=offset,
        )
        total_pages = max(1, (total + _PAGE_SIZE - 1) // _PAGE_SIZE)
        current_page = self._awareness_page + 1

        for e in entries:
            entry_type = e.get("type", "conclusion")
            icon = AWARENESS_TYPE_ICONS.get(entry_type, "?")
            subject = e.get("subject_type", "")
            subject_id = e.get("subject_id", "")
            subject_str = f"{subject}: {subject_id}" if subject_id else subject
            content = e.get("content", "")
            if len(content) > 80:
                content = content[:77] + "..."
            status = e.get("status", "active")
            a_color = AWARENESS_STATUS_COLORS.get(status, "white")
            created = _utc_to_local(e.get("created_at") or "")

            table.add_row(
                str(e["id"]),
                str(e.get("agent_name", "?")),
                f"{icon} {entry_type}",
                f"[{a_color}]{status}[/]",
                subject_str,
                content,
                created,
                key=str(e["id"])
            )
        if not entries:
            table.add_row("", "", "[dim]No awareness entries[/]", "", "", "", "")

        # Update section header with count + page info (matches messages/tasks pattern)
        active_count = self._awareness_active_total()
        try:
            page_hint = (
                f"  │  Page {current_page}/{total_pages}  │  PgUp/PgDn"
                if total_pages > 1 else ""
            )
            filter_hint = ""
            if agent_f or status_f or type_f:
                parts = []
                if agent_f:
                    parts.append(agent_f)
                if status_f:
                    parts.append(status_f)
                if type_f:
                    parts.append(type_f)
                filter_hint = f"  │  Filter: {', '.join(parts)}"
            self.query_one("#awareness-section-header", Static).update(
                f"[bold cyan]Awareness ({active_count} active, {total} shown){filter_hint}{page_hint}[/]"
            )
        except Exception:
            pass

    # ── Event handlers ──

    @on(Select.Changed, "#awareness-agent-filter")
    def on_awareness_agent_changed(self, event: Select.Changed) -> None:
        """Agent filter changed — reset page and refresh."""
        if self._suppress_agent_filter_event:
            return
        val = event.value
        self._awareness_agent_filter = None if val == "all" else val
        self._awareness_page = 0
        self._refresh_awareness()

    @on(Select.Changed, "#awareness-status-filter")
    def on_awareness_status_changed(self, event: Select.Changed) -> None:
        """Status filter changed — reset page and refresh."""
        val = event.value
        self._awareness_status_filter = None if val == "all" else val
        self._awareness_page = 0
        self._refresh_awareness()

    @on(Select.Changed, "#awareness-type-filter")
    def on_awareness_type_changed(self, event: Select.Changed) -> None:
        """Type filter changed — reset page and refresh."""
        val = event.value
        self._awareness_type_filter = None if val == "all" else val
        self._awareness_page = 0
        self._refresh_awareness()

    @on(DataTable.RowHighlighted, "#games-table")
    def on_game_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.row_key is None:
            return
        try:
            self.selected_game_id = int(event.row_key.value)
        except (TypeError, ValueError):
            return
        self.query_one("#btn-strat-edit-game", Button).disabled = False
        self.query_one("#btn-strat-delete-game", Button).disabled = False
        self.query_one("#games-strat-hint", Static).update(
            f"[bold cyan]Selected:[/] game #{self.selected_game_id}"
        )

    @on(DataTable.RowSelected, "#games-table")
    def on_game_selected(self, event: DataTable.RowSelected) -> None:
        game_id = event.row_key.value
        if game_id and self._tasks_reader:
            try:
                gid = int(game_id)
                self.selected_game_id = gid
                game = self._tasks_reader.get_game(gid)
                if game:
                    self.app.push_screen(EditGameModal(game, self))
            except (ValueError, TypeError):
                pass

    @on(DataTable.RowHighlighted, "#awareness-table")
    def on_awareness_highlighted(self, event: DataTable.RowHighlighted) -> None:
        """Single-click / cursor-move enables edit/delete buttons."""
        if event.row_key is None:
            return
        try:
            self.selected_awareness_id = int(event.row_key.value)
        except (TypeError, ValueError):
            return
        self.query_one("#btn-strat-edit-awareness", Button).disabled = False
        self.query_one("#btn-strat-delete-awareness", Button).disabled = False
        self.query_one("#awareness-strat-hint", Static).update(
            f"[bold cyan]Selected:[/] awareness #{self.selected_awareness_id}"
        )

    @on(DataTable.RowSelected, "#awareness-table")
    def on_awareness_selected(self, event: DataTable.RowSelected) -> None:
        entry_id = event.row_key.value
        if entry_id and self._tasks_reader:
            try:
                eid = int(entry_id)
                self.selected_awareness_id = eid
                entry = self._tasks_reader.get_awareness_entry(eid)
                if entry:
                    self.app.push_screen(EditAwarenessModal(entry, self))
            except (ValueError, TypeError):
                pass

    def on_key(self, event) -> None:
        """PgUp/PgDn pagination — targets whichever table is focused."""
        focused = self.app.focused
        if event.key == "pagedown":
            if isinstance(focused, DataTable) and focused.id == "awareness-table":
                agent_f = self._awareness_agent_filter
                status_f = self._awareness_status_filter
                type_f = self._awareness_type_filter
                total = self._tasks_reader.count_all_awareness(
                    agent_name=agent_f, status=status_f, entry_type=type_f,
                ) if self._tasks_reader else 0
                max_page = max(0, (total - 1) // _PAGE_SIZE)
                if self._awareness_page < max_page:
                    self._awareness_page += 1
                    self._refresh_awareness()
        elif event.key == "pageup":
            if isinstance(focused, DataTable) and focused.id == "awareness-table":
                if self._awareness_page > 0:
                    self._awareness_page -= 1
                    self._refresh_awareness()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid == "btn-strategy-close":
            self.app.pop_screen()
        elif bid == "btn-strat-edit-game":
            if self.selected_game_id and self._tasks_reader:
                game = self._tasks_reader.get_game(self.selected_game_id)
                if game:
                    self.app.push_screen(EditGameModal(game, self))
        elif bid == "btn-strat-delete-game":
            if self.selected_game_id and self._tasks_reader:
                self.app.push_screen(DeleteGameConfirmModal(self.selected_game_id, self._tasks_reader, self))
        elif bid == "btn-strat-edit-awareness":
            if self.selected_awareness_id and self._tasks_reader:
                entry = self._tasks_reader.get_awareness_entry(self.selected_awareness_id)
                if entry:
                    self.app.push_screen(EditAwarenessModal(entry, self))
        elif bid == "btn-strat-delete-awareness":
            if self.selected_awareness_id:
                self.app.push_screen(DeleteAwarenessConfirmModal(self.selected_awareness_id, self))

    def refresh_games_table(self) -> None:
        """Called by game edit/delete modals after changes."""
        self._refresh_games()

    def refresh_awareness_table(self) -> None:
        """Called by awareness edit/delete modals after changes."""
        self._awareness_active_count = None
        self._awareness_agent_names_cache = None
        self._cached_agent_option_values = None
        self._sync_awareness_agent_filter(force=True)
        self._refresh_awareness()


# ═══════════════════════════════════════════════════════
# Game Detail Sub-Modal
# ═══════════════════════════════════════════════════════

class GameDetailModal(ModalScreen):
    """Full detail view for a single game."""

    def __init__(self, game: dict, tasks_reader: TasksReader, **kwargs):
        super().__init__(**kwargs)
        self._game = game
        self._tasks_reader = tasks_reader

    def compose(self) -> ComposeResult:
        g = self._game
        gid = g.get("id", "?")
        name = g.get("name", "Unnamed")
        posture = g.get("posture", "exploratory")
        p_color, p_icon = POSTURE_COLORS.get(posture, ("white", "⚪"))
        autonomy = g.get("autonomy", "advisory")
        status = g.get("status", "active")
        s_color = STATUS_COLORS.get(status, "white")
        postulate = g.get("postulate") or "(no postulate defined)"
        freedoms = g.get("freedoms_summary") or "(none recorded)"
        barriers = g.get("barriers_summary") or "(none recorded)"
        milestones_raw = g.get("milestones") or ""
        created = _utc_to_local(g.get("created_at") or "")
        assessed = _utc_to_local(g.get("environment_assessed_at") or "")

        # Parse milestones
        milestones_str = "(none set)"
        if milestones_raw:
            import json
            try:
                ms = json.loads(milestones_raw)
                if isinstance(ms, list) and ms:
                    milestones_str = "\n".join(f"  • {m}" for m in ms)
            except (json.JSONDecodeError, TypeError):
                milestones_str = milestones_raw

        with VerticalScroll(id="game-detail-dialog"):
            yield Static(
                f"[bold]Game #{gid} — {name}[/]",
                id="game-detail-title"
            )
            yield Static(f"\n[bold]Postulate:[/]\n  {postulate}\n")
            yield Static(
                f"  [dim]Posture:[/]  [{p_color}]{p_icon} {posture}[/]   "
                f"  [dim]Autonomy:[/]  {autonomy}   "
                f"  [dim]Status:[/]  [{s_color}]{status}[/]"
            )
            yield Static(
                f"  [dim]Created:[/]  {created}   "
                f"  [dim]Last Assessed:[/]  {assessed}"
            )
            yield Static(f"\n[bold]Freedoms:[/]\n  {freedoms}")
            yield Static(f"\n[bold]Barriers:[/]\n  {barriers}")
            yield Static(f"\n[bold]Milestones:[/]\n{milestones_str}")

            # Linked Projects
            yield Static("\n[bold cyan]─── Linked Projects ───[/]")
            projects = self._tasks_reader.get_game_projects(gid) if self._tasks_reader else []
            if projects:
                for p in projects:
                    ps_color = STATUS_COLORS.get(p.get("status", ""), "white")
                    yield Static(
                        f"  #{p['id']}  {p['name']}  [{ps_color}]{p.get('status', '')}[/]"
                    )
            else:
                yield Static("  [dim](none — 0 projects linked to this game)[/]")

            # Opponents
            yield Static("\n[bold cyan]─── Opponents ───[/]")
            opponents = self._tasks_reader.get_game_opponents(gid) if self._tasks_reader else []
            if opponents:
                for opp in opponents:
                    opp_type = opp.get("type") or "?"
                    opp_name = opp.get("name", "Unknown")
                    opp_desc = opp.get("description") or ""
                    opp_proj = opp.get("project_name") or "?"
                    assessed_at = _utc_to_local(opp.get("last_assessed_at") or "")
                    assessment = opp.get("last_assessment") or "(none)"
                    yield Static(
                        f"  [bold]{opp_name}[/] ({opp_type}) — project: {opp_proj}\n"
                        f"    {opp_desc}\n"
                        f"    [dim]Last assessed:[/] {assessed_at} — {assessment}"
                    )
            else:
                yield Static("  [dim](none recorded)[/]")

            yield Static("")
            yield Button("Close", variant="default", id="btn-game-detail-close",
                         classes="modal-close-btn dismiss-btn")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-game-detail-close":
            self.app.pop_screen()


# ═══════════════════════════════════════════════════════
# Awareness Detail Sub-Modal
# ═══════════════════════════════════════════════════════

class AwarenessDetailModal(ModalScreen):
    """Full detail view for a single awareness entry."""

    def __init__(self, entry: dict, tasks_reader: TasksReader, **kwargs):
        super().__init__(**kwargs)
        self._entry = entry
        self._tasks_reader = tasks_reader

    def compose(self) -> ComposeResult:
        e = self._entry
        eid = e.get("id", "?")
        entry_type = e.get("type", "conclusion")
        icon = AWARENESS_TYPE_ICONS.get(entry_type, "?")
        agent = e.get("agent_name", "?")
        subject_type = e.get("subject_type", "")
        subject_id = e.get("subject_id", "")
        subject_str = f"{subject_type}: {subject_id}" if subject_id else subject_type
        content = e.get("content", "")
        context = e.get("context") or "(none)"
        status = e.get("status", "active")
        a_color = AWARENESS_STATUS_COLORS.get(status, "white")
        created = _utc_to_local(e.get("created_at") or "")
        updated = _utc_to_local(e.get("updated_at") or "")
        conclusion_id = e.get("action_conclusion_id")

        with VerticalScroll(id="awareness-detail-dialog"):
            yield Static(
                f"[bold]{icon} {entry_type.title()} #{eid} — {agent}[/]",
                id="awareness-detail-title"
            )
            yield Static(
                f"\n  [dim]Subject:[/]  {subject_str}\n"
                f"  [dim]Status:[/]   [{a_color}]● {status}[/]\n"
                f"  [dim]Created:[/]  {created}\n"
                f"  [dim]Updated:[/]  {updated}"
            )
            yield Static(f"\n[bold]Content:[/]\n  {content}")
            yield Static(f"\n[bold]Context:[/]\n  {context}")

            # Linked conclusion (for actions only)
            if entry_type == "action" and conclusion_id:
                linked = self._tasks_reader.get_awareness_entry(conclusion_id)
                if linked:
                    linked_content = linked.get("content", "?")
                    if len(linked_content) > 100:
                        linked_content = linked_content[:97] + "..."
                    yield Static(
                        f"\n[bold]Linked Conclusion:[/]\n"
                        f"  └─ #{conclusion_id}: {linked_content}"
                    )

            yield Static("")
            yield Button("Close", variant="default", id="btn-awareness-detail-close",
                         classes="modal-close-btn dismiss-btn")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-awareness-detail-close":
            self.app.pop_screen()


# ═══════════════════════════════════════════════════════
# Game Edit / Delete (PU dashboard)
# ═══════════════════════════════════════════════════════

class EditGameModal(ModalScreen):
    """Edit game strategic fields (PU override — in-place update)."""

    CSS = """
    EditGameModal {
        align: center middle;
        background: $surface 80%;
    }
    #game-edit-dialog {
        width: 86;
        height: auto;
        max-height: 85%;
        padding: 1 2;
        border: heavy $warning;
        background: $surface;
    }
    #edit-game-name {
        margin-bottom: 1;
    }
    #edit-game-postulate,
    #edit-game-freedoms,
    #edit-game-barriers {
        height: 4;
        margin: 1 0;
    }
    #edit-game-milestones {
        height: 5;
        margin-bottom: 1;
    }
    .game-edit-field-row {
        height: auto;
        width: 100%;
        margin-bottom: 1;
    }
    .game-edit-field-col {
        width: 1fr;
        height: auto;
        margin-right: 1;
    }
    #game-edit-actions {
        margin-top: 1;
        height: auto;
        align: center middle;
    }
    #game-edit-actions Button {
        width: 1fr;
        margin: 0 1;
        min-width: 16;
        height: 3;
    }
    """

    def __init__(self, game: dict, parent_modal: StrategyModal, **kwargs):
        super().__init__(**kwargs)
        self._game = game
        self._parent_modal = parent_modal

    def compose(self) -> ComposeResult:
        g = self._game
        gid = g.get("id", "?")
        posture = g.get("posture") or "exploratory"
        autonomy = g.get("autonomy") or "collaborative"
        status = g.get("status") or "active"

        with Vertical(id="game-edit-dialog"):
            yield Static(
                f"[bold]🎯 Edit Game #{gid}[/]\n"
                f"[dim]{g.get('name', 'Unnamed')} · last assessed "
                f"{_utc_to_local(g.get('environment_assessed_at') or '')}[/]"
            )
            yield Static("[cyan]Name[/]")
            yield Input(g.get("name") or "", id="edit-game-name")
            yield Static("[cyan]Postulate[/]")
            yield TextArea(g.get("postulate") or "", id="edit-game-postulate")
            with Horizontal(classes="game-edit-field-row"):
                with Vertical(classes="game-edit-field-col"):
                    yield Static("[cyan]Posture[/]")
                    yield Select(
                        [
                            ("exploratory", "exploratory"),
                            ("steady", "steady"),
                            ("aggressive", "aggressive"),
                            ("defensive", "defensive"),
                        ],
                        value=posture,
                        id="edit-game-posture",
                        allow_blank=False,
                    )
                with Vertical(classes="game-edit-field-col"):
                    yield Static("[cyan]Autonomy[/]")
                    yield Select(
                        [
                            ("advisory", "advisory"),
                            ("collaborative", "collaborative"),
                            ("autonomous", "autonomous"),
                        ],
                        value=autonomy,
                        id="edit-game-autonomy",
                        allow_blank=False,
                    )
                with Vertical(classes="game-edit-field-col"):
                    yield Static("[cyan]Status[/]")
                    yield Select(
                        [
                            ("active", "active"),
                            ("paused", "paused"),
                            ("archived", "archived"),
                        ],
                        value=status,
                        id="edit-game-status",
                        allow_blank=False,
                    )
            yield Static("[cyan]Freedoms summary[/]")
            yield TextArea(g.get("freedoms_summary") or "", id="edit-game-freedoms")
            yield Static("[cyan]Barriers summary[/]")
            yield TextArea(g.get("barriers_summary") or "", id="edit-game-barriers")
            yield Static("[cyan]Milestones[/] [dim](one per line)[/]")
            yield TextArea(_milestones_to_text(g.get("milestones") or ""), id="edit-game-milestones")
            with Horizontal(id="game-edit-actions"):
                yield Button("Save", variant="success", id="btn-save-game")
                yield Button("Cancel", classes="dismiss-btn", variant="default", id="btn-cancel-game")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "btn-cancel-game":
            self.app.pop_screen()
            return

        new_name = self.query_one("#edit-game-name", Input).value.strip()
        if not new_name:
            self.app.notify("Game name cannot be empty", severity="warning")
            return

        game_id = self._game.get("id")
        postulate = self.query_one("#edit-game-postulate", TextArea).text.strip() or None
        posture = self.query_one("#edit-game-posture", Select).value
        autonomy = self.query_one("#edit-game-autonomy", Select).value
        status = self.query_one("#edit-game-status", Select).value
        freedoms = self.query_one("#edit-game-freedoms", TextArea).text.strip() or None
        barriers = self.query_one("#edit-game-barriers", TextArea).text.strip() or None
        milestones = _text_to_milestones(self.query_one("#edit-game-milestones", TextArea).text)

        tasks_db = os.getenv("AGICTL_TASKS_DB", "/var/lib/versa-agi/coa/tasks.db")
        try:
            conn = db_connect.connect_compat(tasks_db, timeout=5)
            assess_env = (
                posture != (self._game.get("posture") or "exploratory")
                or freedoms != (self._game.get("freedoms_summary") or None)
                or barriers != (self._game.get("barriers_summary") or None)
            )
            if assess_env:
                conn.execute(
                    """UPDATE games SET name=?, postulate=?, posture=?, autonomy=?, status=?,
                       freedoms_summary=?, barriers_summary=?, milestones=?,
                       environment_assessed_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP
                       WHERE id=?""",
                    (new_name, postulate, posture, autonomy, status, freedoms, barriers, milestones, game_id),
                )
            else:
                conn.execute(
                    """UPDATE games SET name=?, postulate=?, posture=?, autonomy=?, status=?,
                       freedoms_summary=?, barriers_summary=?, milestones=?,
                       updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                    (new_name, postulate, posture, autonomy, status, freedoms, barriers, milestones, game_id),
                )
            conn.commit()
            conn.close()
            self.app.notify(f"Updated game #{game_id}", severity="information")
            self._parent_modal.refresh_games_table()
            self.app.pop_screen()
        except sqlite3.IntegrityError:
            self.app.notify(f"Game name '{new_name}' already exists", severity="error")
        except Exception as e:
            self.app.notify(f"Error saving game: {e}", severity="error")


class DeleteGameConfirmModal(ModalScreen):
    """Confirmation dialog before deleting a game."""

    CSS = """
    DeleteGameConfirmModal {
        align: center middle;
        background: $surface 80%;
    }
    #game-delete-dialog {
        width: 64;
        height: auto;
        padding: 1 2;
        border: heavy $error;
        background: $surface;
    }
    #game-delete-actions {
        margin-top: 1;
        height: auto;
        align: center middle;
    }
    #game-delete-actions Button {
        width: 1fr;
        margin: 0 1;
        min-width: 16;
        height: 3;
    }
    """

    def __init__(self, game_id: int, tasks_reader: TasksReader, parent_modal: StrategyModal, **kwargs):
        super().__init__(**kwargs)
        self.game_id = game_id
        self._tasks_reader = tasks_reader
        self._parent_modal = parent_modal
        self._project_count = tasks_reader.get_game_project_count(game_id) if tasks_reader else 0
        self._game_name = ""
        if tasks_reader:
            game = tasks_reader.get_game(game_id)
            if game:
                self._game_name = game.get("name") or f"#{game_id}"

    def compose(self) -> ComposeResult:
        unlink_note = ""
        if self._project_count:
            unlink_note = (
                f"\n[dim]Unlinks {self._project_count} project(s) from this game.[/]\n"
            )
        with Vertical(id="game-delete-dialog"):
            yield Static(f"[bold red]Delete game #{self.game_id}?[/]\n")
            yield Static(
                f"[dim]{self._game_name}[/]\n"
                f"{unlink_note}"
                "Removes game-scoped awareness entries.\n"
            )
            yield Static("[bold]This cannot be undone.[/]")
            with Horizontal(id="game-delete-actions"):
                yield Button("Delete", variant="error", id="btn-confirm-delete-game")
                yield Button("Cancel", classes="dismiss-btn", variant="default", id="btn-cancel-delete-game")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "btn-cancel-delete-game":
            self.app.pop_screen()
            return
        tasks_db = os.getenv("AGICTL_TASKS_DB", "/var/lib/versa-agi/coa/tasks.db")
        try:
            conn = db_connect.connect_compat(tasks_db, timeout=5)
            conn.execute(
                "UPDATE projects SET game_id=NULL, updated_at=CURRENT_TIMESTAMP WHERE game_id=?",
                (self.game_id,),
            )
            conn.execute(
                "DELETE FROM agent_awareness WHERE subject_type='game' AND subject_id=?",
                (str(self.game_id),),
            )
            conn.execute("DELETE FROM games WHERE id=?", (self.game_id,))
            conn.commit()
            conn.close()
            self.app.notify(f"Deleted game #{self.game_id}", severity="information")
            self._parent_modal.refresh_games_table()
        except Exception as e:
            self.app.notify(f"Error deleting game: {e}", severity="error")
        self.app.pop_screen()


# ═══════════════════════════════════════════════════════
# Awareness Edit / Delete (PU dashboard)
# ═══════════════════════════════════════════════════════

class EditAwarenessModal(ModalScreen):
    """Edit awareness entry content (PU override — in-place update)."""

    CSS = """
    EditAwarenessModal {
        align: center middle;
        background: $surface 80%;
    }
    #awareness-edit-dialog {
        width: 86;
        height: auto;
        max-height: 85%;
        padding: 1 2;
        border: heavy $primary;
        background: $surface;
    }
    #edit-awareness-content {
        height: 10;
        margin: 1 0;
    }
    #edit-awareness-context {
        height: 5;
        margin-bottom: 1;
    }
    #edit-awareness-actions {
        margin-top: 1;
        height: auto;
        align: center middle;
    }
    #edit-awareness-actions Button {
        width: 1fr;
        margin: 0 1;
        min-width: 16;
        height: 3;
    }
    """

    def __init__(self, entry: dict, parent_modal: StrategyModal, **kwargs):
        super().__init__(**kwargs)
        self._entry = entry
        self._parent_modal = parent_modal

    def compose(self) -> ComposeResult:
        e = self._entry
        eid = e.get("id", "?")
        entry_type = e.get("type", "conclusion")
        agent = e.get("agent_name", "?")
        icon = AWARENESS_TYPE_ICONS.get(entry_type, "?")

        with Vertical(id="awareness-edit-dialog"):
            yield Static(
                f"[bold]{icon} Edit Awareness #{eid}[/]\n"
                f"[dim]{agent} · {entry_type}[/]"
            )
            yield Static("[cyan]Status[/]")
            yield Select(
                [
                    ("active", "active"),
                    ("revised", "revised"),
                    ("superseded", "superseded"),
                    ("completed", "completed"),
                ],
                value=e.get("status") or "active",
                id="edit-awareness-status",
                allow_blank=False,
            )
            yield Static("[cyan]Content[/]")
            yield TextArea(e.get("content") or "", id="edit-awareness-content")
            yield Static("[cyan]Context (optional)[/]")
            yield TextArea(e.get("context") or "", id="edit-awareness-context")
            with Horizontal(id="edit-awareness-actions"):
                yield Button("Save", variant="success", id="btn-save-awareness")
                yield Button("Cancel", classes="dismiss-btn", variant="default", id="btn-cancel-awareness")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "btn-cancel-awareness":
            self.app.pop_screen()
        elif event.button.id == "btn-save-awareness":
            new_content = self.query_one("#edit-awareness-content", TextArea).text.strip()
            if not new_content:
                self.app.notify("Content cannot be empty", severity="warning")
                return
            new_context = self.query_one("#edit-awareness-context", TextArea).text.strip()
            new_status = self.query_one("#edit-awareness-status", Select).value
            entry_id = self._entry.get("id")
            tasks_db = os.getenv("AGICTL_TASKS_DB", "/var/lib/versa-agi/coa/tasks.db")
            try:
                conn = db_connect.connect_compat(tasks_db, timeout=5)
                conn.execute(
                    "UPDATE agent_awareness SET content=?, context=?, status=?, "
                    "updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (new_content, new_context or None, new_status, entry_id),
                )
                conn.commit()
                conn.close()
                self.app.notify(f"Updated awareness #{entry_id}", severity="information")
                self._parent_modal.refresh_awareness_table()
                self.app.pop_screen()
            except Exception as e:
                self.app.notify(f"Error saving awareness: {e}", severity="error")


class DeleteAwarenessConfirmModal(ModalScreen):
    """Confirmation dialog before deleting an awareness entry."""

    CSS = """
    DeleteAwarenessConfirmModal {
        align: center middle;
        background: $surface 80%;
    }
    #awareness-delete-dialog {
        width: 64;
        height: auto;
        padding: 1 2;
        border: heavy $error;
        background: $surface;
    }
    #awareness-delete-actions {
        margin-top: 1;
        height: auto;
        align: center middle;
    }
    #awareness-delete-actions Button {
        width: 1fr;
        margin: 0 1;
        min-width: 16;
        height: 3;
    }
    """

    def __init__(self, entry_id: int, parent_modal: StrategyModal, **kwargs):
        super().__init__(**kwargs)
        self.entry_id = entry_id
        self._parent_modal = parent_modal

    def compose(self) -> ComposeResult:
        with Vertical(id="awareness-delete-dialog"):
            yield Static(f"[bold red]Delete awareness #{self.entry_id}?[/]\n")
            yield Static(
                "[dim]Removes this entry from spawn context. "
                "Agents can recreate it via agictl if needed.[/]\n"
            )
            yield Static("[bold]This cannot be undone.[/]")
            with Horizontal(id="awareness-delete-actions"):
                yield Button("Delete", variant="error", id="btn-confirm-delete-awareness")
                yield Button("Cancel", classes="dismiss-btn", variant="default", id="btn-cancel-delete-awareness")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "btn-confirm-delete-awareness":
            tasks_db = os.getenv("AGICTL_TASKS_DB", "/var/lib/versa-agi/coa/tasks.db")
            try:
                conn = db_connect.connect_compat(tasks_db, timeout=5)
                conn.execute("DELETE FROM agent_awareness WHERE id=?", (self.entry_id,))
                conn.commit()
                conn.close()
                self.app.notify(f"Deleted awareness #{self.entry_id}", severity="information")
                self._parent_modal.refresh_awareness_table()
            except Exception as e:
                self.app.notify(f"Error deleting awareness: {e}", severity="error")
            self.app.pop_screen()
        elif event.button.id == "btn-cancel-delete-awareness":
            self.app.pop_screen()
