"""
Agent reader — read-only composite access to agents.db and cycles.db.
Provides data for the Agents Panel and Footer Stats.
"""

import os
import sys
_CORE_INFRA = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _CORE_INFRA not in sys.path:
    sys.path.insert(0, _CORE_INFRA)
import db_connect  # noqa: E402

import sqlite3
from typing import Optional

class AgentReader:
    def __init__(self, agents_db_path: str, cycles_db_path: str, messages_db_path: str = "", tasks_db_path: str = ""):
        self.agents_db_path = agents_db_path
        self.cycles_db_path = cycles_db_path
        self.messages_db_path = messages_db_path
        self.tasks_db_path = tasks_db_path

    def _query_agents(self, sql: str, params: tuple = ()) -> list[dict]:
        try:
            conn = db_connect.connect_compat(
                f"file:{self.agents_db_path}?mode=ro",
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

    def _query_cycles(self, sql: str, params: tuple = ()) -> list[dict]:
        try:
            conn = db_connect.connect_compat(
                f"file:{self.cycles_db_path}?mode=ro",
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

    def _query_messages(self, sql: str, params: tuple = ()) -> list[dict]:
        if not self.messages_db_path: return []
        try:
            conn = db_connect.connect_compat(
                f"file:{self.messages_db_path}?mode=ro",
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

    def _query_tasks(self, sql: str, params: tuple = ()) -> list[dict]:
        if not self.tasks_db_path: return []
        try:
            conn = db_connect.connect_compat(
                f"file:{self.tasks_db_path}?mode=ro",
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

    def _get_sub_account_id(self, agent_name: str) -> str:
        import os, json
        path = f"/etc/versa-agi/{agent_name}_config.json"
        if os.path.exists(path):
            try:
                with open(path, 'r') as f:
                    data = json.load(f)
                    return data.get("versavoice", {}).get("sub_account_id", "")
            except Exception:
                pass
        return ""

    def build_uid_to_agent_map(self) -> dict[str, str]:
        """Build sub_account_id → agent_name lookup from all registered agents.
        Also maps agent_name → agent_name for internal message resolution."""
        agents = self.get_all_agents()
        uid_map = {}
        for agent in agents:
            name = agent.get("name", "")
            if name:
                # Map agent name to itself (for internal messages)
                uid_map[name] = name
                # Map VV sub_account_id to agent name (for VV messages)
                sub_id = self._get_sub_account_id(name)
                if sub_id:
                    uid_map[sub_id] = name
        return uid_map

    def get_agent_sub_account_uids(self) -> set[str]:
        """Return all VersaVoice sub_account_id values for registered agents."""
        uids: set[str] = set()
        for agent in self.get_all_agents():
            name = agent.get("name", "")
            if not name:
                continue
            sub_id = self._get_sub_account_id(name)
            if sub_id:
                uids.add(sub_id)
        return uids

    def get_active_agents(self) -> list[dict]:
        """Get active agents from agents.db."""
        return self._enrich_agent_table_fields(self._query_agents("SELECT * FROM v_active_agents"))

    def get_all_agents(self) -> list[dict]:
        """Get full registry from agents.db."""
        return self._enrich_agent_table_fields(self._query_agents("SELECT * FROM v_agent_registry"))

    def _enrich_agent_table_fields(self, agents: list[dict]) -> list[dict]:
        """Merge authoritative agents-table flags when views are stale."""
        if not agents or not self.agents_db_path:
            return agents
        try:
            conn = db_connect.connect_compat(self.agents_db_path, timeout=5)
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """SELECT name, tool_output_token_budget, model_routing_enabled FROM agents"""
            ).fetchall()
            conn.close()
            by_name = {r["name"]: dict(r) for r in rows}
            for agent in agents:
                src = by_name.get(agent.get("name"))
                if not src:
                    continue
                for key in ("tool_output_token_budget", "model_routing_enabled"):
                    if src.get(key) is not None:
                        agent[key] = src[key]
        except Exception:
            pass
        return agents

    def update_agent_field(self, agent_name: str, field: str, value) -> bool:
        """Update a mutable field on an agent record."""
        allowed = {"timeout_minutes", "runaway_threshold", "runaway_size_threshold",
                   "inactive", "can_message_connections", "model", "context_injection_mode",
                   "status", "status_message", "token_budget",
                   "max_session_turns", "tool_output_token_budget",
                   "session_retention_enabled", "session_retention_max_age",
                   "session_retention_max_count", "anchor_style", "triage_model",
                   "num_ctx", "temperature", "reasoning_effort", "reasoning_max_tokens",
                   "model_params_extra", "conversation_depth", "resume_enabled",
                   "resume_max_messages", "skill_injection_mode", "browser_enabled",
                   "model_routing_enabled"}
        if field not in allowed:
            return False
        try:
            conn = db_connect.connect_compat(self.agents_db_path, timeout=5)
            conn.execute(
                f"UPDATE agents SET {field} = ?, updated_at = datetime('now') WHERE name = ?",
                (value, agent_name)
            )
            conn.commit()
            conn.close()
            return True
        except Exception:
            return False

    def get_last_cycle(self, agent_name: str = "") -> Optional[dict]:
        """Get the most recent cycle from cycles.db, optionally filtered by agent."""
        if agent_name:
            rows = self._query_cycles(
                "SELECT id, started_at, ended_at, exit_code, summary, "
                "messages_sent, messages_recv, tasks_done, "
                "COALESCE(tokens_input, 0) as tokens_input, "
                "COALESCE(tokens_output, 0) as tokens_output, "
                "COALESCE(tokens_thinking, 0) as tokens_thinking, "
                "COALESCE(tokens_cached, 0) as tokens_cached, "
                "COALESCE(tokens_total, 0) as tokens_total, "
                "json_output_path "
                "FROM cycles WHERE id LIKE ? ORDER BY started_at DESC LIMIT 1",
                (f"{agent_name}-%",)
            )
        else:
            rows = self._query_cycles(
                "SELECT id, started_at, ended_at, exit_code, summary, "
                "messages_sent, messages_recv, tasks_done, "
                "COALESCE(tokens_input, 0) as tokens_input, "
                "COALESCE(tokens_output, 0) as tokens_output, "
                "COALESCE(tokens_thinking, 0) as tokens_thinking, "
                "COALESCE(tokens_cached, 0) as tokens_cached, "
                "COALESCE(tokens_total, 0) as tokens_total, "
                "json_output_path "
                "FROM cycles ORDER BY started_at DESC LIMIT 1"
            )
        return rows[0] if rows else None

    def get_agent_lifetime_stats(self, agent_name: str) -> dict:
        """Compose genuine lifetime metrics from the isolated data lakes."""
        stats = {"sent": 0, "received": 0, "tasks_done": 0}
        
        # Resolve identity mapping automatically
        sub_account_id = self._get_sub_account_id(agent_name)
        
        if self.messages_db_path and sub_account_id:
            s_rows = self._query_messages("SELECT COUNT(id) as c FROM messages WHERE direction='sent' AND from_user_id=?", (sub_account_id,))
            if s_rows: stats["sent"] = s_rows[0]["c"]
            
            r_rows = self._query_messages("SELECT COUNT(id) as c FROM messages WHERE direction='received' AND to_user_id=?", (sub_account_id,))
            if r_rows: stats["received"] = r_rows[0]["c"]

        if self.tasks_db_path:
            t_rows = self._query_tasks(
                "SELECT COUNT(id) as c FROM tasks WHERE status='done' AND assigned_to=?",
                (agent_name,)
            )
            if t_rows: stats["tasks_done"] = t_rows[0]["c"]
            
        return stats

    def get_monthly_token_totals(self) -> dict:
        """Get token totals for the current calendar month from cycles.db."""
        rows = self._query_cycles(
            "SELECT "
            "COALESCE(SUM(tokens_input), 0) as input, "
            "COALESCE(SUM(tokens_output), 0) as output, "
            "COALESCE(SUM(tokens_thinking), 0) as thinking, "
            "COALESCE(SUM(tokens_cached), 0) as cached, "
            "COALESCE(SUM(tokens_total), 0) as total "
            "FROM cycles "
            "WHERE started_at >= strftime('%Y-%m-01', 'now')"
        )
        return rows[0] if rows else {"input": 0, "output": 0, "thinking": 0, "cached": 0, "total": 0}

    def get_agent_monthly_tokens(self, agent_name: str) -> int:
        """Get total tokens for a specific agent in the current month."""
        rows = self._query_cycles(
            "SELECT COALESCE(SUM(tokens_total), 0) as total "
            "FROM cycles "
            "WHERE id LIKE ? AND started_at >= strftime('%Y-%m-01', 'now')",
            (f"{agent_name}-%",)
        )
        return rows[0]["total"] if rows else 0

    def reset_monthly_cycles(self) -> bool:
        """Delete all cycle records in the current month. Returns True on success."""
        try:
            conn = db_connect.connect_compat(self.cycles_db_path, timeout=5)
            conn.execute(
                "DELETE FROM cycles WHERE started_at >= strftime('%Y-%m-01', 'now')"
            )
            conn.commit()
            conn.execute("VACUUM")
            conn.close()
            return True
        except Exception:
            return False

    def drain_all_cycles(self) -> bool:
        """Delete ALL cycle records (full history drain). Returns True on success."""
        try:
            conn = db_connect.connect_compat(self.cycles_db_path, timeout=5)
            conn.execute("DELETE FROM cycles")
            conn.commit()
            conn.execute("VACUUM")
            conn.close()
            return True
        except Exception:
            return False

    def get_total_cycles_count(self) -> int:
        """Count total executions historically."""
        rows = self._query_cycles("SELECT COUNT(id) as count FROM cycles")
        return rows[0]["count"] if rows else 0

    def get_agent_cycles_count(self, agent_name: str) -> int:
        """Count total executions historically for a specific agent."""
        rows = self._query_cycles("SELECT COUNT(id) as count FROM cycles WHERE id LIKE ?", (f"{agent_name}-%",))
        return rows[0]["count"] if rows else 0

    def get_recent_cycle_summaries(self, agent_name: str, limit: int = 8) -> list[str]:
        """Get chronological summaries of recent cycles for context injection.

        Timestamps are rendered in the system's local timezone so they agree
        with the "System time" stated in CYCLE PARAMETERS (stored values are
        UTC). Consecutive identical summaries — the signature of a respawn
        loop — are collapsed into a single entry annotated with a repeat
        count, keeping the loop signal without wasting prompt tokens.
        """
        # Over-fetch so duplicate collapsing can still yield `limit` entries
        rows = self._query_cycles(
            "SELECT summary, ts FROM ("
            "  SELECT COALESCE(summary, '(no summary)') AS summary, "
            "         datetime(started_at, 'localtime') AS ts, started_at "
            "  FROM cycles WHERE id LIKE ? ORDER BY started_at DESC LIMIT ?"
            ") ORDER BY started_at ASC",
            (f"{agent_name}-%", limit * 3)
        )
        collapsed: list[list] = []  # [ts, summary, count]
        for r in rows:
            if collapsed and collapsed[-1][1] == r["summary"]:
                collapsed[-1][0] = r["ts"]  # keep the most recent timestamp
                collapsed[-1][2] += 1
            else:
                collapsed.append([r["ts"], r["summary"], 1])
        collapsed = collapsed[-limit:]
        return [
            f"{ts}: {summary}" + (f" (repeated ×{count})" if count > 1 else "")
            for ts, summary, count in collapsed
        ]

    def update_last_cycle_tokens(self, agent_name: str, t_in: int, t_out: int, t_think: int, t_total: int, exit_code: int = None, t_cached: int = 0, session_path: str = None) -> bool:
        try:
            conn = db_connect.connect_compat(self.cycles_db_path, timeout=5)
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                "SELECT id, execution_model FROM cycles WHERE id LIKE ? ORDER BY started_at DESC LIMIT 1",
                (f"{agent_name}-%",)
            )
            row = cursor.fetchone()
            if row:
                cycle_id = row["id"]
                execution_model = row["execution_model"] or ""
                cost_usd = None
                pricing_source = None
                if execution_model:
                    try:
                        import sys as _sys
                        _lib = "/usr/local/lib/versa-agi"
                        if _lib not in _sys.path:
                            _sys.path.insert(0, _lib)
                        from model_catalog import estimate_cycle_cost_usd
                        cost_usd, pricing_source = estimate_cycle_cost_usd(
                            execution_model, t_in, t_out, tokens_thinking=t_think, tokens_cached=t_cached,
                        )
                    except Exception:
                        pass
                if exit_code is not None:
                    conn.execute(
                        """UPDATE cycles SET tokens_input=?, tokens_output=?, tokens_thinking=?,
                           tokens_cached=?, tokens_total=?, exit_code=?, json_output_path=?,
                           cost_usd_estimated=?, pricing_source=? WHERE id=?""",
                        (t_in, t_out, t_think, t_cached, t_total, exit_code, session_path,
                         cost_usd, pricing_source, cycle_id)
                    )
                else:
                    conn.execute(
                        """UPDATE cycles SET tokens_input=?, tokens_output=?, tokens_thinking=?,
                           tokens_cached=?, tokens_total=?, json_output_path=?,
                           cost_usd_estimated=?, pricing_source=? WHERE id=?""",
                        (t_in, t_out, t_think, t_cached, t_total, session_path,
                         cost_usd, pricing_source, cycle_id)
                    )
                conn.commit()
            conn.close()
            return True
        except Exception:
            return False
