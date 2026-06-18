"""
Message reader — read-only access to messages.db.
Provides data for the Messages Feed panel and agictl.
"""

import sqlite3
from typing import Optional

class MessageReader:
    def __init__(self, db_path: str):
        self.db_path = db_path

    def _query(self, sql: str, params: tuple = ()) -> list[dict]:
        try:
            conn = sqlite3.connect(
                self.db_path,
                timeout=5,
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
        except Exception:
            return False

    def get_recent_messages(self, limit: int = 20, channel: str = None, offset: int = 0) -> list[dict]:
        """Get most recent messages. Optional channel filter: 'vv', 'internal', or None (all)."""
        base = ("SELECT id, message_id, direction, display_name, from_user_id, to_user_id, "
                "text, original_text, mode, created_at, status, "
                "has_attachments, attachment_path, raw_payload, cycle_id, channel_id, "
                "COALESCE(channel, 'vv') as channel "
                "FROM messages ")
        if channel:
            base += "WHERE channel = ? "
            base += "ORDER BY created_at DESC LIMIT ? OFFSET ?"
            return self._query(base, (channel, limit, offset))
        else:
            base += "ORDER BY created_at DESC LIMIT ? OFFSET ?"
            return self._query(base, (limit, offset))

    def count_messages(self, channel: str = None) -> int:
        """Get total message count for pagination."""
        if channel:
            rows = self._query("SELECT COUNT(*) as c FROM messages WHERE channel = ?", (channel,))
        else:
            rows = self._query("SELECT COUNT(*) as c FROM messages")
        return rows[0]["c"] if rows else 0

    def get_pending_messages(self) -> list[dict]:
        """Get messages awaiting processing or delivery."""
        return self._query(
            "SELECT id, direction, status, created_at "
            "FROM messages WHERE status IN ('unprocessed', 'pending', 'queued')"
        )

    def count_unprocessed(self, sub_account: str, within_seconds: int = 0, agent_name: str = "") -> int:
        """Count unprocessed received messages for a specific sub-account.
        Filters by to_user_id for multi-agent support (shared messages.db).
        If agent_name is provided, also matches internal messages addressed by name."""
        if agent_name and agent_name != sub_account:
            rows = self._query(
                "SELECT COUNT(*) as c FROM messages "
                "WHERE status = 'unprocessed' AND direction = 'received' AND (to_user_id = ? OR to_user_id = ?)",
                (sub_account, agent_name)
            )
        else:
            rows = self._query(
                "SELECT COUNT(*) as c FROM messages "
                "WHERE status = 'unprocessed' AND direction = 'received' AND to_user_id = ?",
                (sub_account,)
            )
        return rows[0]["c"] if rows else 0

    def count_stale(self, sub_account: str, older_than_seconds: int) -> int:
        rows = self._query(
            "SELECT COUNT(*) as c FROM messages WHERE status = 'unprocessed' AND to_user_id = ? "
            f"AND created_at < datetime('now', '-{older_than_seconds} seconds')",
            (sub_account,)
        )
        return rows[0]["c"] if rows else 0

    def fail_stale(self, sub_account: str, older_than_seconds: int) -> bool:
        return self._execute(
            "UPDATE messages SET status = 'failed', updated_at = datetime('now') "
            "WHERE status = 'unprocessed' AND to_user_id = ? "
            f"AND created_at < datetime('now', '-{older_than_seconds} seconds')",
            (sub_account,)
        )

    def get_top_contacts(self, sub_account: str, limit: int = 5) -> list[str]:
        """Find the most recently active contacts for context building."""
        rows = self._query(
            "SELECT CASE WHEN direction='sent' THEN to_user_id ELSE from_user_id END AS uid, "
            "MAX(created_at) AS latest "
            "FROM messages "
            "WHERE from_user_id = ? OR to_user_id = ? "
            "GROUP BY uid ORDER BY latest DESC LIMIT ?",
            (sub_account, sub_account, limit)
        )
        return [r["uid"] for r in rows if r["uid"]]

    def get_contact_history(self, sub_account: str, contact_uid: str, limit: int = 60, exclude_unprocessed: bool = False, agent_name: str = "") -> list[dict]:
        """Get the most recent conversation history in chronological order for prompt injection.
        When exclude_unprocessed=True, omits unprocessed inbound messages (already injected separately).
        When agent_name is provided, also matches internal messages where to_user_id=agent_name."""
        unprocessed_filter = "AND NOT (direction = 'received' AND processed_cycle_id IS NULL) " if exclude_unprocessed else ""
        # Build ID list for matching both VV UID and agent name
        my_ids = [sub_account]
        if agent_name and agent_name != sub_account:
            my_ids.append(agent_name)
        # Build WHERE clause for multi-ID matching
        id_placeholders = ",".join("?" * len(my_ids))
        # For each my_id, we need: (from=contact AND to=my_id) OR (to=contact AND from=my_id)
        # Simplified: messages involving both this agent and the contact
        to_conditions = " OR ".join(f"to_user_id = ?" for _ in my_ids)
        from_conditions = " OR ".join(f"from_user_id = ?" for _ in my_ids)
        params = (
            [contact_uid] + my_ids +  # from=contact AND to IN my_ids
            [contact_uid] + my_ids +  # to=contact AND from IN my_ids
            [limit]
        )
        return self._query(
            "SELECT direction, cleaned_text, created_at FROM ("
            "  SELECT direction, REPLACE(REPLACE(COALESCE(NULLIF(original_text,''), text), CHAR(10), ' '), CHAR(13), '') as cleaned_text, created_at "
            "  FROM messages "
            f"  WHERE ((from_user_id = ? AND ({to_conditions})) "
            f"     OR (to_user_id = ? AND ({from_conditions}))) "
            f"  {unprocessed_filter}"
            "  ORDER BY created_at DESC "
            "  LIMIT ?"
            ") ORDER BY created_at ASC",
            tuple(params)
        )

    def store_inbox_messages(self, messages: list[dict], sub_account: str) -> tuple[int, list[str]]:
        """Stores a list of incoming messages, returning the number inserted and distinct message IDs."""
        inserted = 0
        persisted_ids = []
        try:
            conn = sqlite3.connect(self.db_path, timeout=5)
            for msg in messages:
                # Resolve IDs varying between REST and MCP payloads
                msg_id = msg.get("messageId")
                if not msg_id:
                    continue

                # Dedup
                c = conn.execute("SELECT COUNT(*) FROM messages WHERE message_id = ?", (msg_id,))
                if c.fetchone()[0] > 0:
                    continue

                # Sender parsing
                sender_id = msg.get("from", {}).get("uid") or msg.get("from", {}).get("senderId") or msg.get("senderId")
                sender_name = msg.get("from", {}).get("displayName") or msg.get("senderName") or "Unknown"

                text = msg.get("text") or ""
                original = msg.get("originalText")
                channel_id = msg.get("channelId") or ""
                created_at = msg.get("timestamp") or "now"
                # Use sqlite datetime('now') if empty
                time_val = msg.get("timestamp")
                
                query = """
                    INSERT INTO messages 
                    (direction, from_user_id, to_user_id, display_name, message_id, channel_id, text, original_text, mode, status, created_at)
                    VALUES ('received', ?, ?, ?, ?, ?, ?, ?, 'typed', 'unprocessed', COALESCE(?, datetime('now')))
                """
                conn.execute(query, (sender_id, sub_account, sender_name, msg_id, channel_id, text, original, time_val))
                inserted += 1
                persisted_ids.append(msg_id)
            conn.commit()
            conn.close()
        except Exception:
            pass
        return inserted, persisted_ids

    def store_outbox_messages(self, messages: list[dict], agent_uid: str) -> int:
        """Stores outgoing MCP messages."""
        import os
        inserted = 0
        # Cycle ID from environment (set by lifeline env script)
        env_cycle_id = os.environ.get("VERSA_CYCLE_ID") or None
        try:
            conn = sqlite3.connect(self.db_path, timeout=5)
            for msg in messages:
                msg_id = msg.get("messageId")
                
                if msg_id and msg_id != "N/A":
                    c = conn.execute("SELECT COUNT(*) FROM messages WHERE message_id = ?", (msg_id,))
                    if c.fetchone()[0] > 0:
                        continue

                text = msg.get("text") or ""
                mode = msg.get("mode", "typed")
                status = msg.get("status", "sent")
                recipient_id = msg.get("recipientId") or ""
                error_msg = msg.get("errorMessage")
                time_val = msg.get("timestamp")
                has_attach = 1 if msg.get("hasAttachments") or msg.get("attachments") else 0
                cycle_id = msg.get("cycleId") or env_cycle_id

                query = """
                    INSERT INTO messages 
                    (direction, from_user_id, to_user_id, text, mode, status, message_id, error_message, has_attachments, cycle_id, created_at)
                    VALUES ('sent', ?, ?, ?, ?, ?, ?, ?, ?, ?, COALESCE(?, datetime('now')))
                """
                conn.execute(query, (agent_uid, recipient_id, text, mode, status, msg_id, error_msg, has_attach, cycle_id, time_val))
                inserted += 1
            conn.commit()
            conn.close()
        except Exception as e:
            pass
        return inserted

    def stamp_cycle_id(self, sub_account: str, cycle_id: str, agent_name: str = "") -> int:
        """Stamp all unprocessed received messages with the given cycle_id."""
        try:
            conn = sqlite3.connect(self.db_path, timeout=5)
            if agent_name and agent_name != sub_account:
                cursor = conn.execute(
                    "UPDATE messages SET cycle_id = ? WHERE status = 'unprocessed' AND direction = 'received' AND (to_user_id = ? OR to_user_id = ?) AND cycle_id IS NULL",
                    (cycle_id, sub_account, agent_name)
                )
            else:
                cursor = conn.execute(
                    "UPDATE messages SET cycle_id = ? WHERE status = 'unprocessed' AND direction = 'received' AND to_user_id = ? AND cycle_id IS NULL",
                    (cycle_id, sub_account)
                )
            count = cursor.rowcount
            conn.commit()
            conn.close()
            return count
        except Exception:
            return 0

    def update_attachment_status(self, msg_id: str, path: str) -> bool:
        return self._execute(
            "UPDATE messages SET has_attachments=1, attachment_path=? WHERE message_id=?",
            (path, msg_id)
        )

    def get_unprocessed_from(self, sub_account: str, senders: list[str]) -> list[str]:
        if not senders:
            return []
        placeholders = ",".join("?" * len(senders))
        rows = self._query(
            f"SELECT DISTINCT from_user_id FROM messages WHERE status='unprocessed' AND to_user_id=? AND from_user_id IN ({placeholders})",
            (sub_account, *senders)
        )
        return [r["from_user_id"] for r in rows]

    def delete_message(self, message_id: str) -> bool:
        """Delete a message by its message_id and tombstone it against inbox re-sync."""
        try:
            conn = sqlite3.connect(self.db_path, timeout=5)
            conn.execute(
                "CREATE TABLE IF NOT EXISTS deleted_message_ids ("
                "message_id TEXT PRIMARY KEY, "
                "deleted_at DATETIME NOT NULL DEFAULT (datetime('now'))"
                ")"
            )
            conn.execute(
                "INSERT OR IGNORE INTO deleted_message_ids (message_id) VALUES (?)",
                (message_id,),
            )
            conn.execute("DELETE FROM messages WHERE message_id = ?", (message_id,))
            conn.commit()
            conn.close()
            return True
        except Exception:
            return False

    def update_message_status(self, message_id: str, new_status: str) -> bool:
        """Update the status of a message."""
        allowed = {"pending", "queued", "sent", "delivered", "error", "unprocessed", "processed"}
        if new_status not in allowed:
            return False
        return self._execute(
            "UPDATE messages SET status = ? WHERE message_id = ?",
            (new_status, message_id)
        )
