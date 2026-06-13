"""Strategy Modal — Game of Life strategic overview.

Unified 'agent brain viewer' with three sections:
  1. Games — Strategic pursuits
  2. Awareness — Conclusions + Actions (paginated)
  3. System Memory — Operational knowledge (paginated, relocated from SystemMemoryEditorModal)

Sub-modals:
  - GameDetailModal — Full game details with projects + opponents
  - AwarenessDetailModal — Full awareness entry details
"""

import os
import sqlite3
import time
from typing import Optional

from textual import on
from textual.app import ComposeResult
from textual.screen import ModalScreen
from textual.containers import Vertical, VerticalScroll, Horizontal
from textual.widgets import DataTable, Button, Static, Input, Select

from agitop.data import TasksReader


_TZ = time.strftime("%Z")

# ── Pagination ──
_PAGE_SIZE = 20

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


# ═══════════════════════════════════════════════════════
# Main Strategy Modal
# ═══════════════════════════════════════════════════════

class StrategyModal(ModalScreen):
    """Game of Life — Strategic Overview modal with three sections."""

    def __init__(self, tasks_reader: Optional[TasksReader], **kwargs):
        super().__init__(**kwargs)
        self._tasks_reader = tasks_reader
        self.selected_memory_key = None
        # Pagination state
        self._awareness_page = 0
        self._memory_page = 0
        # Awareness filters
        self._awareness_status_filter = None   # None = All
        self._awareness_type_filter = None     # None = All

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="strategy-dialog"):
            yield Static(
                "[bold]🎯  Game of Life — Strategic Overview[/]",
                id="strategy-dialog-title"
            )

            # ── Section 1: Games ──
            yield Static("[bold cyan]Games[/]")
            yield DataTable(id="games-table", cursor_type="row")

            yield Static("[bold cyan]Awareness[/]",
                          id="awareness-section-header",
                          classes="strat-section-header")
            with Horizontal(classes="awareness-filter-row"):
                yield Select(
                    [("All", "all"), ("💡 Conclusion", "conclusion"),
                     ("⚡ Action", "action")],
                    value="all", id="awareness-type-filter", allow_blank=False,
                )
                yield Select(
                    [("All", "all"), ("Active", "active"), ("Revised", "revised"),
                     ("Superseded", "superseded"), ("Completed", "completed")],
                    value="all", id="awareness-status-filter", allow_blank=False,
                )
            yield DataTable(id="awareness-table", cursor_type="row")

            # ── Section 3: System Memory ──
            yield Static("[bold cyan]System Memory[/]",
                          id="sys-memory-section-header",
                          classes="strat-section-header")
            yield DataTable(id="sys-memory-strat-table", cursor_type="row")
            yield Static("", id="sys-memory-strat-hint")
            with Horizontal(classes="btn-grid-row"):
                yield Button("Edit Selected", variant="primary", id="btn-strat-edit-mem",
                             disabled=True, classes="panel-btn")
                yield Button("Delete Selected", variant="error", id="btn-strat-delete-mem",
                             disabled=True, classes="panel-btn")

            yield Static("")
            yield Button("Close", variant="default", id="btn-strategy-close",
                         classes="modal-close-btn dismiss-btn")

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

        # Setup System Memory table
        mem_table = self.query_one("#sys-memory-strat-table", DataTable)
        mem_table.add_column("Updated", width=20)
        mem_table.add_column("Stored By", width=12)
        mem_table.add_column("Key", width=30)
        mem_table.add_column("Value")
        self._refresh_system_memory()

    # ── Data refresh ──

    def _refresh_games(self) -> None:
        table = self.query_one("#games-table", DataTable)
        table.clear()
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

    def _refresh_awareness(self) -> None:
        table = self.query_one("#awareness-table", DataTable)
        table.clear()
        if not self._tasks_reader:
            return

        # Resolve filters
        status_f = self._awareness_status_filter
        type_f = self._awareness_type_filter

        # Paginated query with filters
        offset = self._awareness_page * _PAGE_SIZE
        entries = self._tasks_reader.get_awareness_entries(
            status=status_f, entry_type=type_f, limit=_PAGE_SIZE, offset=offset
        )
        total = self._tasks_reader.count_all_awareness(
            status=status_f, entry_type=type_f
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
        active_count = self._tasks_reader.count_active_awareness()
        try:
            page_hint = (
                f"  │  Page {current_page}/{total_pages}  │  PgUp/PgDn"
                if total_pages > 1 else ""
            )
            filter_hint = ""
            if status_f or type_f:
                parts = []
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

    def _refresh_system_memory(self) -> None:
        table = self.query_one("#sys-memory-strat-table", DataTable)
        table.clear()
        self.selected_memory_key = None
        self.query_one("#btn-strat-edit-mem", Button).disabled = True
        self.query_one("#btn-strat-delete-mem", Button).disabled = True
        self.query_one("#sys-memory-strat-hint", Static).update(
            "[dim]Select a row to edit or delete.[/]"
        )

        tasks_db = os.getenv("AGICTL_TASKS_DB", "/var/lib/versa-agi/coa/tasks.db")
        offset = self._memory_page * _PAGE_SIZE
        try:
            conn = sqlite3.connect(tasks_db, timeout=5)
            conn.row_factory = sqlite3.Row

            # Get total count for pagination
            total = conn.execute("SELECT COUNT(*) FROM agent_memory_system").fetchone()[0]
            total_pages = max(1, (total + _PAGE_SIZE - 1) // _PAGE_SIZE)
            current_page = self._memory_page + 1

            rows = conn.execute(
                "SELECT * FROM agent_memory_system ORDER BY updated_at ASC LIMIT ? OFFSET ?",
                (_PAGE_SIZE, offset)
            ).fetchall()
            for r in rows:
                val = r["value"]
                if val and len(val) > 80:
                    val = val[:77] + "..."
                table.add_row(
                    str(r["updated_at"] or "--"),
                    str(r["agent_name"] or "?"),
                    r["key"],
                    val,
                    key=r["key"]
                )
            conn.close()

            # Update section header with count + page info (matches messages/tasks pattern)
            try:
                page_hint = (
                    f"  │  Page {current_page}/{total_pages}  │  PgUp/PgDn"
                    if total_pages > 1 else ""
                )
                self.query_one("#sys-memory-section-header", Static).update(
                    f"[bold cyan]System Memory ({total} entries){page_hint}[/]"
                )
            except Exception:
                pass
        except Exception as e:
            pass

    # ── Event handlers ──

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

    @on(DataTable.RowSelected, "#games-table")
    def on_game_selected(self, event: DataTable.RowSelected) -> None:
        game_id = event.row_key.value
        if game_id and self._tasks_reader:
            try:
                gid = int(game_id)
                game = self._tasks_reader.get_game(gid)
                if game:
                    self.app.push_screen(GameDetailModal(game, self._tasks_reader))
            except (ValueError, TypeError):
                pass

    @on(DataTable.RowSelected, "#awareness-table")
    def on_awareness_selected(self, event: DataTable.RowSelected) -> None:
        entry_id = event.row_key.value
        if entry_id and self._tasks_reader:
            try:
                eid = int(entry_id)
                entry = self._tasks_reader.get_awareness_entry(eid)
                if entry:
                    self.app.push_screen(AwarenessDetailModal(entry, self._tasks_reader))
            except (ValueError, TypeError):
                pass

    @on(DataTable.RowHighlighted, "#sys-memory-strat-table")
    def on_memory_highlighted(self, event: DataTable.RowHighlighted) -> None:
        """Single-click / cursor-move enables edit/delete buttons."""
        if event.row_key is None:
            return
        self.selected_memory_key = event.row_key.value
        self.query_one("#btn-strat-edit-mem", Button).disabled = False
        self.query_one("#btn-strat-delete-mem", Button).disabled = False
        self.query_one("#sys-memory-strat-hint", Static).update(
            f"[bold cyan]Selected:[/] {self.selected_memory_key}"
        )

    @on(DataTable.RowSelected, "#sys-memory-strat-table")
    def on_memory_selected(self, event: DataTable.RowSelected) -> None:
        """Double-click / Enter opens edit directly."""
        self.selected_memory_key = event.row_key.value
        if self.selected_memory_key:
            from agitop.panels.system_memory_editor import EditMemoryRowModal
            self.app.push_screen(EditMemoryRowModal(self.selected_memory_key, self))

    def on_key(self, event) -> None:
        """PgUp/PgDn pagination — targets whichever table is focused."""
        focused = self.app.focused
        if event.key == "pagedown":
            if isinstance(focused, DataTable) and focused.id == "awareness-table":
                status_f = self._awareness_status_filter
                type_f = self._awareness_type_filter
                total = self._tasks_reader.count_all_awareness(
                    status=status_f, entry_type=type_f
                ) if self._tasks_reader else 0
                max_page = max(0, (total - 1) // _PAGE_SIZE)
                if self._awareness_page < max_page:
                    self._awareness_page += 1
                    self._refresh_awareness()
            elif isinstance(focused, DataTable) and focused.id == "sys-memory-strat-table":
                import sqlite3 as _sq
                try:
                    db = os.getenv("AGICTL_TASKS_DB", "/var/lib/versa-agi/coa/tasks.db")
                    conn = _sq.connect(db, timeout=5)
                    total = conn.execute("SELECT COUNT(*) FROM agent_memory_system").fetchone()[0]
                    conn.close()
                    max_page = max(0, (total - 1) // _PAGE_SIZE)
                    if self._memory_page < max_page:
                        self._memory_page += 1
                        self._refresh_system_memory()
                except Exception:
                    pass
        elif event.key == "pageup":
            if isinstance(focused, DataTable) and focused.id == "awareness-table":
                if self._awareness_page > 0:
                    self._awareness_page -= 1
                    self._refresh_awareness()
            elif isinstance(focused, DataTable) and focused.id == "sys-memory-strat-table":
                if self._memory_page > 0:
                    self._memory_page -= 1
                    self._refresh_system_memory()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid == "btn-strategy-close":
            self.app.pop_screen()
        elif bid == "btn-strat-edit-mem":
            if self.selected_memory_key:
                from agitop.panels.system_memory_editor import EditMemoryRowModal
                # Pass self as parent_modal so refresh works
                self.app.push_screen(EditMemoryRowModal(self.selected_memory_key, self))
        elif bid == "btn-strat-delete-mem":
            if self.selected_memory_key:
                from agitop.panels.system_memory_editor import DeleteMemoryConfirmModal
                self.app.push_screen(DeleteMemoryConfirmModal(self.selected_memory_key, self))

    def refresh_table(self) -> None:
        """Called by EditMemoryRowModal / DeleteMemoryConfirmModal after changes."""
        self._refresh_system_memory()


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
