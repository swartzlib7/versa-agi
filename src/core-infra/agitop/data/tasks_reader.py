"""
Tasks reader — read/write access to tasks.db.
Provides data for the Tasks Panel and agictl.
"""

import sqlite3
from typing import Optional

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
        try:
            conn = sqlite3.connect(self.db_path, timeout=5)
            conn.execute(sql, params)
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            return False

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

    def add_task(self, title: str, assigned_to: str = 'coa', project_id: Optional[int] = None,
                 description: Optional[str] = None, priority: str = 'normal') -> bool:
        if project_id is not None:
            return self._execute(
                "INSERT INTO tasks (title, status, assigned_to, project_id, description, priority) VALUES (?, 'planned', ?, ?, ?, ?)",
                (title, assigned_to, project_id, description, priority)
            )
        return self._execute(
            "INSERT INTO tasks (title, status, assigned_to, description, priority) VALUES (?, 'planned', ?, ?, ?)",
            (title, assigned_to, description, priority)
        )

    def update_task(self, task_id: int, updates: dict) -> bool:
        if not updates:
            return False
        
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
        return self._execute(
            "UPDATE tasks SET status = 'blocked', due_date = datetime('now', '+' || ? || ' minutes'), updated_at = datetime('now') WHERE id = ?",
            (str(minutes), task_id)
        )

    def get_connections(self) -> list[dict]:
        """Get known connections."""
        return self._query(
            "SELECT uid, display_name, spoken_lang, relationship "
            "FROM connections ORDER BY display_name"
        )
        
    def get_connection_name(self, uid: str) -> str:
        rows = self._query(
            "SELECT display_name FROM connections WHERE uid = ? LIMIT 1",
            (uid,)
        )
        return rows[0]["display_name"] if rows else uid
        
    def count_pending(self, agent: str) -> int:
        """Count actionable tasks: in_progress always counts, planned/waiting/blocked
        count if due_date has arrived. Safe from infinite waking because the Lifeline
        auto-freezes overdue tasks after MAX_SPAWN_ATTEMPTS (3) failed cycles."""
        rows = self._query(
            "SELECT COUNT(*) as c FROM tasks "
            "WHERE ("
            "  status = 'in_progress' "
            "  OR (status IN ('planned', 'waiting', 'blocked') "
            "      AND due_date IS NOT NULL "
            "      AND due_date <= datetime('now'))"
            ") "
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
            conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
            conn.commit()
            conn.close()
            return True, f"Deleted task #{task_id}: '{title}'"
        except Exception as e:
            return False, str(e)
