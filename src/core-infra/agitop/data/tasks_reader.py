"""
Tasks reader — read/write access to tasks.db.
Provides data for the Tasks Panel and agictl.
"""

import sqlite3
from typing import Optional

# TD-SCRIPT-001: Reserved-name protection for shared system projects (mirrors
# RESERVED_SYSTEM_PROJECTS in agictl/cli.py). AGi-Tools (the Script Task source)
# and AGi-Knowledgebase must never be hard-deleted — a reserved-name set is the
# simplest durable guard (no `protected` column / migration required).
RESERVED_SYSTEM_PROJECTS = {"AGi-Tools", "AGi-Knowledgebase"}


class TasksReader:
    def __init__(self, db_path: str):
        self.db_path = db_path

    def _query(self, sql: str, params: tuple = ()) -> list[dict]:
        try:
            conn = sqlite3.connect(
                f"file:{self.db_path}?mode=ro",
                uri=True,
                timeout=2,
            )
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(sql, params)
            rows = [dict(row) for row in cursor.fetchall()]
            conn.close()
            return rows
        except Exception:
            return []

    def _execute(self, sql: str, params: tuple = ()) -> bool:
        return self._insert(sql, params) is not None

    def _insert(self, sql: str, params: tuple = ()) -> Optional[int]:
        try:
            conn = sqlite3.connect(self.db_path, timeout=5)
            cur = conn.execute(sql, params)
            rowid = cur.lastrowid
            conn.commit()
            conn.close()
            return int(rowid) if rowid is not None else None
        except Exception:
            return None

    def get_active_tasks(self, limit: int = 15) -> list[dict]:
        """Get active tasks from v_active_tasks."""
        return self._query(
            "SELECT * FROM v_active_tasks LIMIT ?",
            (limit,),
        )

    def get_all_tasks(self, limit: int = 50, offset: int = 0) -> list[dict]:
        """Get all tasks, actionable statuses first, then by recency."""
        return self._query(
            "SELECT * FROM tasks ORDER BY "
            "CASE status "
            "  WHEN 'in_progress' THEN 0 "
            "  WHEN 'frozen' THEN 1 "
            "  WHEN 'planned' THEN 2 "
            "  WHEN 'waiting' THEN 3 "
            "  WHEN 'blocked' THEN 4 "
            "  WHEN 'done' THEN 5 "
            "  WHEN 'cancelled' THEN 6 "
            "  ELSE 7 END, "
            "created_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        )

    def count_all_tasks(self) -> int:
        """Get total task count for pagination."""
        rows = self._query("SELECT COUNT(*) as c FROM tasks")
        return rows[0]["c"] if rows else 0

    def get_due_blocked_tasks(self, limit: int = 15) -> list[dict]:
        """Get blocked tasks that are due to wake up."""
        return self._query(
            "SELECT * FROM v_due_blocked_tasks LIMIT ?",
            (limit,),
        )

    def get_active_projects(self) -> list[dict]:
        """Get active projects from projects table."""
        return self._query("SELECT * FROM v_active_projects")

    def get_all_projects(self) -> list[dict]:
        """Get all projects regardless of status."""
        return self._query("SELECT * FROM projects ORDER BY status ASC, name ASC")

    def get_project_members(self, project_id: int) -> list[dict]:
        """Get all members for a project."""
        return self._query(
            "SELECT member_type, member_id, display_name, workspace_path, branch, roles, assigned_at "
            "FROM project_members WHERE project_id=? ORDER BY roles DESC, assigned_at ASC",
            (project_id,)
        )

    def get_project_member_summary(self, project_id: int) -> str:
        """Get a condensed member summary string for table display."""
        members = self._query(
            "SELECT display_name, member_type FROM project_members WHERE project_id=? ORDER BY roles DESC",
            (project_id,)
        )
        if not members:
            return "--"
        names = [m["display_name"] or "?" for m in members]
        return f"{len(names)}: {', '.join(names[:3])}{'…' if len(names) > 3 else ''}"

    def update_project(self, project_id: int, updates: dict) -> bool:
        """Update mutable fields on a project."""
        if not updates:
            return False
        set_clauses = []
        params = []
        for k, v in updates.items():
            set_clauses.append(f"{k} = ?")
            params.append(v)
        set_clauses.append("updated_at = datetime('now')")
        params.append(project_id)
        return self._execute(
            f"UPDATE projects SET {', '.join(set_clauses)} WHERE id = ?",
            tuple(params)
        )

    def get_project_options(self) -> list[tuple]:
        """Get (name, id) tuples for Select widget population."""
        rows = self._query("SELECT id, name FROM projects WHERE status='active' ORDER BY name")
        return [(r["name"], r["id"]) for r in rows]

    def get_agent_names(self) -> list[str]:
        """Get active agent names for Select widget population."""
        try:
            import sqlite3
            agents_path = self.db_path.replace("coa/tasks.db", "agents.db")
            conn = sqlite3.connect(agents_path, timeout=5)
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT name FROM agents WHERE inactive=0 ORDER BY name").fetchall()
            conn.close()
            return [r["name"] for r in rows]
        except Exception:
            return []

    def get_project_name(self, project_id: int) -> str:
        """Resolve project_id to project name."""
        if not project_id:
            return "--"
        rows = self._query("SELECT name FROM projects WHERE id = ?", (project_id,))
        return rows[0]["name"] if rows else f"#{project_id}"

    def get_task(self, task_id: int) -> Optional[dict]:
        """Get full task details by ID."""
        rows = self._query("SELECT * FROM tasks WHERE id = ?", (task_id,))
        return rows[0] if rows else None

    def count_task_progress(self, task_id: int) -> int:
        """Return total progress journal entries for a task."""
        rows = self._query(
            "SELECT COUNT(*) as c FROM task_progress WHERE task_id = ?",
            (task_id,),
        )
        return rows[0]["c"] if rows else 0

    def get_task_progress(self, task_id: int, limit: int = 20) -> list[dict]:
        """Get a task's progress journal entries, oldest first."""
        rows = self._query(
            "SELECT id, created_at, agent_name, note FROM task_progress "
            "WHERE task_id = ? ORDER BY id DESC LIMIT ?",
            (task_id, limit),
        )
        return list(reversed(rows))

    def get_task_progress_page(self, task_id: int, offset: int = 0, limit: int = 10) -> list[dict]:
        """Get a page of progress entries, newest first."""
        rows = self._query(
            "SELECT id, created_at, agent_name, note FROM task_progress "
            "WHERE task_id = ? ORDER BY id DESC LIMIT ? OFFSET ?",
            (task_id, limit, offset),
        )
        return rows

    def count_task_progress_matching(self, task_id: int, pattern: str) -> int:
        """Count progress entries matching a SQL LIKE pattern."""
        rows = self._query(
            "SELECT COUNT(*) as c FROM task_progress WHERE task_id = ? AND note LIKE ?",
            (task_id, pattern),
        )
        return rows[0]["c"] if rows else 0

    def delete_task_progress_entry(self, entry_id: int, task_id: int) -> bool:
        """Delete a single progress entry (PU dashboard only)."""
        try:
            conn = sqlite3.connect(self.db_path, timeout=5)
            cur = conn.cursor()
            cur.execute(
                "DELETE FROM task_progress WHERE id = ? AND task_id = ?",
                (entry_id, task_id),
            )
            deleted = cur.rowcount > 0
            conn.commit()
            conn.close()
            return deleted
        except Exception:
            return False

    def get_task_progress_entry(self, entry_id: int, task_id: int) -> Optional[dict]:
        """Fetch a single progress entry (PU dashboard only)."""
        rows = self._query(
            "SELECT id, created_at, agent_name, note FROM task_progress "
            "WHERE id = ? AND task_id = ?",
            (entry_id, task_id),
        )
        return rows[0] if rows else None

    def update_task_progress_entry(self, entry_id: int, task_id: int, note: str) -> bool:
        """Update a progress entry's note text (PU dashboard only)."""
        note = (note or "").strip()
        if not note:
            return False
        try:
            conn = sqlite3.connect(self.db_path, timeout=5)
            cur = conn.cursor()
            cur.execute(
                "UPDATE task_progress SET note = ? WHERE id = ? AND task_id = ?",
                (note, entry_id, task_id),
            )
            updated = cur.rowcount > 0
            if updated:
                conn.execute(
                    "UPDATE tasks SET updated_at = datetime('now') WHERE id = ?",
                    (task_id,),
                )
            conn.commit()
            conn.close()
            return updated
        except Exception:
            return False

    def prune_task_progress(self, task_id: int, pattern: str) -> int:
        """Delete matching progress entries except the most recent (PU dashboard only)."""
        try:
            conn = sqlite3.connect(self.db_path, timeout=5)
            cur = conn.cursor()
            keep = cur.execute(
                "SELECT id FROM task_progress WHERE task_id = ? AND note LIKE ? "
                "ORDER BY id DESC LIMIT 1",
                (task_id, pattern),
            ).fetchone()
            if not keep:
                conn.close()
                return 0
            cur.execute(
                "DELETE FROM task_progress WHERE task_id = ? AND note LIKE ? AND id != ?",
                (task_id, pattern, keep[0]),
            )
            deleted = cur.rowcount
            conn.commit()
            conn.close()
            return deleted
        except Exception:
            return 0

    def add_task(self, title: str, assigned_to: str = 'coa', project_id: Optional[int] = None,
                 description: Optional[str] = None, priority: str = 'normal') -> Optional[int]:
        if project_id is not None:
            return self._insert(
                "INSERT INTO tasks (title, status, assigned_to, project_id, description, priority, created_at, updated_at) "
                "VALUES (?, 'planned', ?, ?, ?, ?, datetime('now'), datetime('now'))",
                (title, assigned_to, project_id, description, priority),
            )
        return self._insert(
            "INSERT INTO tasks (title, status, assigned_to, description, priority, created_at, updated_at) "
            "VALUES (?, 'planned', ?, ?, ?, datetime('now'), datetime('now'))",
            (title, assigned_to, description, priority),
        )

    def update_task(self, task_id: int, updates: dict) -> bool:
        if not updates:
            return False

        existing = self.get_task(task_id)
        if existing and existing.get("status") == "frozen":
            new_status = updates.get("status")
            if new_status and new_status != "frozen":
                updates["spawn_attempts"] = 0
                updates["pre_freeze_status"] = None
        
        # Manual due_date reschedule clears snooze gate so lifeline can wake immediately.
        if "due_date" in updates and "wake_after" not in updates:
            updates["wake_after"] = None

        set_clauses = []
        params = []
        for k, v in updates.items():
            set_clauses.append(f"{k} = ?")
            params.append(v)

        set_clauses.append("updated_at = datetime('now')")
        
        query = f"UPDATE tasks SET {', '.join(set_clauses)} WHERE id = ?"
        params.append(task_id)
        
        return self._execute(query, tuple(params))

    def update_task_status(self, task_id: int, status: str) -> bool:
        # Reset spawn_attempts on terminal status transitions so the task
        # gets a fresh retry budget if it is ever reactivated.
        if status in ('done', 'cancelled'):
            return self._execute(
                "UPDATE tasks SET status = ?, spawn_attempts = 0, updated_at = datetime('now') WHERE id = ?",
                (status, task_id)
            )
        return self._execute(
            "UPDATE tasks SET status = ?, updated_at = datetime('now') WHERE id = ?",
            (status, task_id)
        )

    def unfreeze_agent_tasks(self, agent_name: str) -> int:
        """Unfreeze all frozen tasks for an agent. Restores pre_freeze_status, resets spawn_attempts.
        Returns count of unfrozen tasks."""
        try:
            conn = sqlite3.connect(self.db_path, timeout=5)
            # Restore to pre_freeze_status if available, otherwise 'planned'
            conn.execute(
                "UPDATE tasks SET status = COALESCE(pre_freeze_status, 'planned'), "
                "spawn_attempts = 0, pre_freeze_status = NULL, updated_at = datetime('now') "
                "WHERE status = 'frozen' AND assigned_to = ?",
                (agent_name,)
            )
            count = conn.execute(
                "SELECT changes()"
            ).fetchone()[0]
            conn.commit()
            conn.close()
            return count
        except Exception:
            return 0

    def count_frozen(self, agent_name: str) -> int:
        """Count frozen tasks for an agent."""
        rows = self._query(
            "SELECT COUNT(*) as c FROM tasks WHERE status = 'frozen' AND assigned_to = ?",
            (agent_name,)
        )
        return rows[0]["c"] if rows else 0

    def snooze_task(self, task_id: int, minutes: int) -> bool:
        """Defer a task by setting wake_after and due_date forward (status unchanged).

        Resets spawn_attempts so a proper snooze clears the overdue retry budget.
        """
        return self._execute(
            "UPDATE tasks SET "
            "wake_after = datetime('now', '+' || ? || ' minutes'), "
            "due_date = datetime('now', '+' || ? || ' minutes'), "
            "wake_cycle_count = COALESCE(wake_cycle_count, 0) + 1, "
            "spawn_attempts = 0, "
            "updated_at = datetime('now') "
            "WHERE id = ?",
            (str(minutes), str(minutes), task_id)
        )

    def get_connections(self) -> list[dict]:
        """Get known connections (including date_of_birth for org-staff display)."""
        return self._query(
            "SELECT uid, display_name, spoken_lang, relationship, date_of_birth "
            "FROM connections ORDER BY display_name"
        )
        
    def get_connection_name(self, uid: str) -> str:
        rows = self._query(
            "SELECT display_name FROM connections WHERE uid = ? LIMIT 1",
            (uid,)
        )
        return rows[0]["display_name"] if rows else uid
        
    def count_pending(self, agent: str) -> int:
        """Count actionable tasks: in_progress always counts; planned/waiting/blocked
        count only when due_date has arrived AND wake_after is unset or elapsed.

        Note: Lifeline auto-freezes overdue *planned* and repeatedly-waking *waiting*
        tasks after MAX_SPAWN_ATTEMPTS (3). Blocked tasks rely on snooze (wake_after).

        Deterministic task kinds (utility — TD-UTIL-001, script — TD-SCRIPT-001) are
        excluded: they run via their own lifeline runners and must never spawn the
        LLM agent."""
        rows = self._query(
            "SELECT COUNT(*) as c FROM tasks "
            "WHERE ("
            "  status = 'in_progress' "
            "  OR (status IN ('planned', 'waiting', 'blocked') "
            "      AND due_date IS NOT NULL "
            "      AND due_date <= datetime('now') "
            "      AND (wake_after IS NULL OR wake_after <= datetime('now')))"
            ") "
            "AND (task_kind IS NULL OR task_kind NOT IN ('utility', 'script')) "
            "AND (assigned_to = ? OR assigned_to IS NULL)",
            (agent,)
        )
        return rows[0]["c"] if rows else 0

    def count_due_blocked(self, agent: str) -> int:
        rows = self._query(
            "SELECT COUNT(*) as c FROM tasks "
            "WHERE status='blocked' AND wake_after IS NOT NULL AND wake_after <= datetime('now') "
            "AND (assigned_to = ? OR assigned_to IS NULL)",
            (agent,)
        )
        return rows[0]["c"] if rows else 0

    def count_total_blocked(self, agent: str) -> int:
        rows = self._query(
            "SELECT COUNT(*) as c FROM tasks "
            "WHERE status='blocked' AND wake_after IS NOT NULL "
            "AND (assigned_to = ? OR assigned_to IS NULL)",
            (agent,)
        )
        return rows[0]["c"] if rows else 0

    def get_blocked_detail(self, agent: str) -> str:
        rows = self._query(
            "SELECT id || ': ' || title || ' (wake=' || COALESCE(wake_after,'NULL') || "
            "', now=' || datetime('now') || ', due=' || "
            "CASE WHEN wake_after IS NOT NULL AND wake_after <= datetime('now') THEN 'YES' ELSE 'NO' END || ')' as detail "
            "FROM tasks WHERE status='blocked' AND wake_after IS NOT NULL "
            "AND (assigned_to = ? OR assigned_to IS NULL)",
            (agent,)
        )
        return "\n".join([r["detail"] for r in rows if r["detail"]])

    def get_blocked_uids(self) -> list[str]:
        rows = self._query("SELECT uid FROM connections WHERE relationship='blocked'")
        return [r["uid"] for r in rows if r["uid"]]

    def check_connection_followup(self, agent_name: str) -> int:
        rows = self._query(
            "SELECT COUNT(*) as c FROM tasks WHERE callback_action = 'check_connection' "
            "AND status IN ('blocked','pending','in_progress') AND (assigned_to=? OR assigned_to IS NULL)",
            (agent_name,)
        )
        return rows[0]["c"] if rows else 0

    def inject_connection_followup(self, agent_name: str) -> bool:
        return self._execute(
            "INSERT INTO tasks (title, description, status, priority, assigned_to, callback_action, wake_after, wake_cycle_count, created_at, updated_at) VALUES (?, ?, 'blocked', 'normal', ?, 'check_connection', datetime('now', '+2 minutes'), 0, datetime('now'), datetime('now'))",
            (
                'Follow up on connection request',
                'SYSTEM-INJECTED: IMPORTANT — Re-read connection_lifecycle.md before processing this task. A connect_sub_account call was detected. Check if the connection was accepted using list_connections. If accepted: introduce yourself using the self_introduction skill, then COMPLETE this task with: agictl task done <this_task_id> "Connected and introduced". If still pending: inform Primary User the connection is pending, then COMPLETE this task. YOU MUST complete this task or you will be re-spawned repeatedly.',
                agent_name
            )
        )

    def delete_project(self, project_id: int) -> tuple[bool, str]:
        """Hard-delete a project. Only allowed if status is 'archived'.
        
        Unlinks any tasks referencing this project (sets project_id=NULL).
        Cleans up associated project memberships and agent memories.
        Returns (success, message).
        """
        try:
            conn = sqlite3.connect(self.db_path, timeout=5)
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT id, name, status FROM projects WHERE id = ?", (project_id,)).fetchone()
            if not row:
                conn.close()
                return False, f"Project #{project_id} not found"
            if row["status"] != "archived":
                conn.close()
                return False, f"Project '{row['name']}' must be archived before deletion (current: {row['status']})"
            name = row["name"]
            # Reserved-name guard (TD-SCRIPT-001) — protected system projects cannot be deleted.
            if name in RESERVED_SYSTEM_PROJECTS:
                conn.close()
                return False, f"'{name}' is a protected system project and cannot be deleted"
            
            # 1. Unlink tasks referencing this project
            conn.execute("UPDATE tasks SET project_id = NULL WHERE project_id = ?", (project_id,))
            
            # 2. Clean up project memberships
            conn.execute("DELETE FROM project_members WHERE project_id = ?", (project_id,))
            
            # 3. Clean up associated project memories
            conn.execute("DELETE FROM agent_memory_project WHERE project_id = ?", (project_id,))
            
            # 4. Delete the project record itself
            conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
            
            conn.commit()
            conn.close()
            return True, f"Deleted archived project '{name}' and associated memories/members"
        except Exception as e:
            return False, str(e)

    def delete_task(self, task_id: int) -> tuple[bool, str]:
        """Hard-delete a task. Only allowed if status is 'done' or 'cancelled'.
        
        Returns (success, message).
        """
        try:
            conn = sqlite3.connect(self.db_path, timeout=5)
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT id, title, status FROM tasks WHERE id = ?", (task_id,)).fetchone()
            if not row:
                conn.close()
                return False, f"Task #{task_id} not found"
            if row["status"] not in ("done", "cancelled"):
                conn.close()
                return False, f"Task '{row['title']}' must be done or cancelled before deletion (current: {row['status']})"
            title = row["title"]
            conn.execute("DELETE FROM task_progress WHERE task_id = ?", (task_id,))
            conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
            conn.commit()
            conn.close()
            return True, f"Deleted task #{task_id}: '{title}' (progress journal removed)"
        except Exception as e:
            return False, str(e)

    # ═══════════════════════════════════════════════════════
    # Games — Strategic Pursuit Management
    # ═══════════════════════════════════════════════════════

    def get_all_games(self) -> list[dict]:
        """All games ordered by status then name."""
        return self._query("SELECT * FROM games ORDER BY status ASC, name ASC")

    def get_game(self, game_id: int) -> Optional[dict]:
        """Full game details by ID."""
        rows = self._query("SELECT * FROM games WHERE id = ?", (game_id,))
        return rows[0] if rows else None

    def get_game_project_count(self, game_id: int) -> int:
        """Count projects linked to a game."""
        rows = self._query("SELECT COUNT(*) as c FROM projects WHERE game_id = ?", (game_id,))
        return rows[0]["c"] if rows else 0

    def get_game_name(self, game_id: int) -> str:
        """Resolve game_id to game name."""
        if not game_id:
            return "--"
        rows = self._query("SELECT name FROM games WHERE id = ?", (game_id,))
        return rows[0]["name"] if rows else f"#{game_id}"

    def get_game_projects(self, game_id: int) -> list[dict]:
        """Projects linked to a game."""
        return self._query(
            "SELECT id, name, status FROM projects WHERE game_id = ? ORDER BY name",
            (game_id,)
        )

    def get_game_opponents(self, game_id: int) -> list[dict]:
        """Opponents across all projects linked to a game."""
        return self._query(
            "SELECT po.*, p.name as project_name FROM project_opponents po "
            "JOIN projects p ON po.project_id = p.id "
            "WHERE p.game_id = ? ORDER BY po.last_assessed_at DESC",
            (game_id,)
        )

    # ═══════════════════════════════════════════════════════
    # Awareness — Agent Cognitive State
    # ═══════════════════════════════════════════════════════

    def get_awareness_entries(self, agent_name: str = None, status: str = None,
                              entry_type: str = None,
                              limit: int = 50, offset: int = 0) -> list[dict]:
        """Awareness entries with optional filters and pagination."""
        entries, _total = self.get_awareness_page(
            agent_name=agent_name,
            status=status,
            entry_type=entry_type,
            limit=limit,
            offset=offset,
        )
        return entries

    def get_awareness_page(
        self,
        agent_name: str = None,
        status: str = None,
        entry_type: str = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[dict], int]:
        """Paginated awareness rows + total count in one DB round trip."""
        clauses, params = [], []
        if agent_name:
            clauses.append("agent_name = ?")
            params.append(agent_name)
        if status:
            clauses.append("status = ?")
            params.append(status)
        if entry_type:
            clauses.append("type = ?")
            params.append(entry_type)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        list_sql = (
            "SELECT id, agent_name, type, status, subject_type, subject_id, "
            "content, created_at FROM agent_awareness "
            f"{where} ORDER BY id ASC LIMIT ? OFFSET ?"
        )
        count_sql = f"SELECT COUNT(*) as c FROM agent_awareness {where}"
        try:
            conn = sqlite3.connect(
                f"file:{self.db_path}?mode=ro",
                uri=True,
                timeout=2,
            )
            conn.row_factory = sqlite3.Row
            total_row = conn.execute(count_sql, tuple(params)).fetchone()
            total = int(total_row["c"]) if total_row else 0
            rows = conn.execute(list_sql, tuple(params) + (limit, offset)).fetchall()
            conn.close()
            return [dict(row) for row in rows], total
        except Exception:
            return [], 0

    def get_awareness_entry(self, entry_id: int) -> Optional[dict]:
        """Full awareness entry by ID."""
        rows = self._query("SELECT * FROM agent_awareness WHERE id = ?", (entry_id,))
        return rows[0] if rows else None

    def count_active_awareness(self) -> int:
        """Count active awareness entries."""
        rows = self._query("SELECT COUNT(*) as c FROM agent_awareness WHERE status = 'active'")
        return rows[0]["c"] if rows else 0

    def count_all_awareness(
        self,
        status: str = None,
        entry_type: str = None,
        agent_name: str = None,
    ) -> int:
        """Count awareness entries with optional filters for pagination."""
        clauses, params = [], []
        if agent_name:
            clauses.append("agent_name = ?")
            params.append(agent_name)
        if status:
            clauses.append("status = ?")
            params.append(status)
        if entry_type:
            clauses.append("type = ?")
            params.append(entry_type)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._query(f"SELECT COUNT(*) as c FROM agent_awareness {where}", tuple(params))
        return rows[0]["c"] if rows else 0

    def get_awareness_agent_names(self) -> list[str]:
        """Distinct agent names with awareness entries, for filter picklists."""
        rows = self._query(
            "SELECT DISTINCT agent_name FROM agent_awareness "
            "WHERE agent_name IS NOT NULL AND TRIM(agent_name) != '' "
            "ORDER BY agent_name COLLATE NOCASE"
        )
        return [r["agent_name"] for r in rows]

    def count_active_games(self) -> int:
        """Count active games."""
        rows = self._query("SELECT COUNT(*) as c FROM games WHERE status = 'active'")
        return rows[0]["c"] if rows else 0
