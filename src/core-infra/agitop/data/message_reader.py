"""
Message reader — read-only access to messages.db.
Provides data for the Messages Feed panel and agictl.
"""

import os
import sys
_CORE_INFRA = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _CORE_INFRA not in sys.path:
    sys.path.insert(0, _CORE_INFRA)
import db_connect  # noqa: E402

import sqlite3
from typing import Optional

class MessageReader:
    def __init__(self, db_path: str):
        self.db_path = db_path

    def _query(self, sql: str, params: tuple = ()) -> list[dict]:
        try:
            conn = db_connect.connect_compat(
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

    def _query_strict(self, sql: str, params: tuple = ()) -> list[dict]:
        """Run a read query whose failure must not masquerade as empty state."""
        conn = db_connect.connect_compat(self.db_path, timeout=5)
        conn.row_factory = sqlite3.Row
        try:
            return [dict(row) for row in conn.execute(sql, params).fetchall()]
        finally:
            conn.close()

    def _execute(self, sql: str, params: tuple = ()) -> bool:
        try:
            conn = db_connect.connect_compat(self.db_path, timeout=5)
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
        If agent_name is provided, also matches internal messages addressed by name.

        ``within_seconds`` above 0 counts only messages received in the last
        that many seconds; 0 means no window (every unprocessed message).
        ``datetime(created_at)`` reads both stored forms (plain and ISO `…Z`)."""
        ids = [sub_account]
        if agent_name and agent_name != sub_account:
            ids.append(agent_name)
        sql = ("SELECT COUNT(*) as c FROM messages "
               "WHERE status = 'unprocessed' AND direction = 'received' "
               f"AND to_user_id IN ({','.join('?' * len(ids))})")
        params = list(ids)
        window = int(within_seconds or 0)
        if window > 0:
            sql += " AND datetime(created_at) >= datetime('now', ?)"
            params.append(f"-{window} seconds")
        rows = self._query(sql, tuple(params))
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
        unprocessed_filter = "AND NOT (direction = 'received' AND status = 'unprocessed') " if exclude_unprocessed else ""
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
        return self._query_strict(
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

    def get_outbound_streak(
        self,
        sub_account: str,
        contact_uid: str,
        *,
        agent_name: str = "",
        limit: int = 10,
    ) -> dict:
        """Return an identity-aware consecutive outbound streak for one contact.

        Both the VersaVoice sub-account UID and internal agent-name alias identify
        the same agent. Any inbound on either identity resets the streak.
        """
        my_ids = [sub_account]
        if agent_name and agent_name != sub_account:
            my_ids.append(agent_name)
        id_placeholders = ",".join("?" * len(my_ids))
        params = (
            my_ids
            + [contact_uid]
            + [contact_uid]
            + my_ids
            + [max(1, int(limit))]
        )
        rows = self._query_strict(
            "SELECT direction, created_at, "
            "CAST((julianday('now') - julianday(created_at)) * 24 AS INTEGER) "
            "AS age_hours "
            "FROM messages "
            f"WHERE ((from_user_id IN ({id_placeholders}) AND to_user_id = ?) "
            f"   OR (from_user_id = ? AND to_user_id IN ({id_placeholders}))) "
            "ORDER BY created_at DESC, id DESC "
            "LIMIT ?",
            tuple(params),
        )

        count = 0
        latest_outbound_age_hours = None
        for row in rows:
            if row["direction"] == "received":
                break
            if row["direction"] != "sent":
                break
            if latest_outbound_age_hours is None:
                latest_outbound_age_hours = row["age_hours"]
            count += 1

        return {
            "count": count,
            "latest_outbound_age_hours": latest_outbound_age_hours,
        }

    def store_inbox_messages(self, messages: list[dict], sub_account: str) -> tuple[int, list[str]]:
        """Stores a list of incoming messages, returning the number inserted and distinct message IDs."""
        inserted = 0
        persisted_ids = []
        try:
            conn = db_connect.connect_compat(self.db_path, timeout=5)
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
            conn = db_connect.connect_compat(self.db_path, timeout=5)
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
            conn = db_connect.connect_compat(self.db_path, timeout=5)
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
            conn = db_connect.connect_compat(self.db_path, timeout=5)
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

    def delete_local_messages(self, message_ids=(), agent_ids=(), other: str = "", before_utc: str = "",
                              channel: str = "all", include_unread: bool = False, dry_run: bool = False) -> dict:
        """Delete the local copies of messages. Nothing is sent to VersaVoice. SPWN-55.

        Either ``message_ids``, or ``agent_ids`` (an agent's name and its
        VersaVoice account id): the rows that agent sent and the rows it
        received, optionally only those exchanged with ``other`` and only
        those created before ``before_utc`` (stored UTC text).

        ``channel``: ``internal`` (agent-to-agent), ``vv`` (local copies of
        VersaVoice messages) or ``all``. A deleted VersaVoice row is
        tombstoned so inbox sync never brings it back.

        Kept: a received message still unprocessed, unless ``include_unread``;
        always, a message waiting to be sent (``pending`` / ``queued``).
        Returns ``{deleted, by_channel, kept_unread, kept_unsent,
        skipped_channel, not_found}``; with ``dry_run`` nothing is deleted.
        """
        ids = [str(i) for i in (message_ids or ()) if str(i).strip()]
        mine = [str(i) for i in (agent_ids or ()) if str(i).strip()]
        result = {"deleted": 0, "by_channel": {}, "kept_unread": 0, "kept_unsent": 0,
                  "skipped_channel": [], "not_found": []}
        where: list[str] = []
        params: list = []
        if channel in ("internal", "vv"):
            where.append("channel = ?")
            params.append(channel)
        if ids:
            where.append(f"message_id IN ({','.join('?' * len(ids))})")
            params += ids
        elif mine:
            marks = ",".join("?" * len(mine))
            where.append(f"((direction = 'sent' AND from_user_id IN ({marks})) "
                         f"OR (direction = 'received' AND to_user_id IN ({marks})))")
            params += mine + mine
            if other:
                where.append("((direction = 'sent' AND to_user_id = ?) OR (direction = 'received' AND from_user_id = ?))")
                params += [other, other]
            if before_utc:
                where.append("datetime(created_at) < datetime(?)")
                params.append(before_utc)
        else:
            return result
        scope = " AND ".join(where)
        unread = "(direction = 'received' AND status = 'unprocessed')"
        unsent = "(status IN ('pending', 'queued'))"
        conn = db_connect.connect_compat(self.db_path, timeout=5)
        try:
            if ids:
                known = {row[0]: row[1] for row in conn.execute(
                    f"SELECT message_id, channel FROM messages WHERE message_id IN ({','.join('?' * len(ids))})", ids)}
                result["not_found"] = [i for i in ids if i not in known]
                if channel in ("internal", "vv"):
                    result["skipped_channel"] = [i for i in ids if known.get(i) not in (None, channel)]
            result["kept_unsent"] = conn.execute(
                f"SELECT COUNT(*) FROM messages WHERE {scope} AND {unsent}", params).fetchone()[0]
            scope += f" AND NOT {unsent}"
            if not include_unread:
                result["kept_unread"] = conn.execute(
                    f"SELECT COUNT(*) FROM messages WHERE {scope} AND {unread}", params).fetchone()[0]
                scope += f" AND NOT {unread}"
            rows = conn.execute(f"SELECT message_id, channel FROM messages WHERE {scope}", params).fetchall()
            for _, row_channel in rows:
                result["by_channel"][row_channel] = result["by_channel"].get(row_channel, 0) + 1
            result["deleted"] = len(rows)
            if not dry_run and rows:
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS deleted_message_ids ("
                    "message_id TEXT PRIMARY KEY, deleted_at DATETIME NOT NULL DEFAULT (datetime('now')))")
                conn.executemany("INSERT OR IGNORE INTO deleted_message_ids (message_id) VALUES (?)",
                                 [(mid,) for mid, row_channel in rows if row_channel == "vv" and mid])
                conn.execute(f"DELETE FROM messages WHERE {scope}", params)
                conn.commit()
        finally:
            conn.close()
        return result

    def update_message_status(self, message_id: str, new_status: str) -> bool:
        """Update the status of a message."""
        allowed = {"pending", "queued", "sent", "delivered", "error", "unprocessed", "processed"}
        if new_status not in allowed:
            return False
        return self._execute(
            "UPDATE messages SET status = ? WHERE message_id = ?",
            (new_status, message_id)
        )
