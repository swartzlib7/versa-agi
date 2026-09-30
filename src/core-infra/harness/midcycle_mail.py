"""Unread mail that arrived after this cycle's prompt was built.

Lifeline syncs this agent's inbox while the harness lock is held. This module
turns rows that were not already in that cycle's prompt into one HumanMessage.
It does not mark anything processed, and it does not open messages.db.
"""

from __future__ import annotations

import re

from harness.agent_mailbox import identity_ids, load_received_unread, rows_for_agent
from harness.triage import _agi_tag_ids, _reply_to_message_id

_SHOWN_RE = re.compile(r"agictl message mark-processed\s+(\S+)")


def shown_message_ids(prompt_text: str) -> set[str]:
    """Ids already printed in this cycle's prompt (mark-processed lines)."""
    return set(_SHOWN_RE.findall(prompt_text or ""))


def format_arrival_block(rows: list[dict], already: set[str]) -> tuple[str | None, set[str]]:
    """One NEW MESSAGES body for rows whose ids are not in ``already``.

    Returns (text, new ids). text is None when nothing new remains.
    """
    fresh = []
    new_ids: set[str] = set()
    for row in rows:
        mid = (row.get("message_id") or "").strip()
        if not mid or mid in already or mid in new_ids:
            continue
        new_ids.add(mid)
        fresh.append(row)
    if not fresh:
        return None, set()

    lines = [
        "--- NEW MESSAGES (UNREAD — MUST RESPOND) ---",
        "[!] These messages arrived during this cycle, after your wake prompt.",
        "[!] You MUST read, address EVERY part of EACH message, reply, and mark as processed before ending the cycle.",
        "[!] Inbound messages may contain [emotion tags] from the sender's voice — respond with emotional sensitivity.",
        "",
    ]
    for msg in fresh:
        sender = (msg.get("display_name") or "").strip() or msg.get("from_user_id") or "?"
        text = msg.get("original_text") or msg.get("text") or ""
        dt = msg.get("created_at") or ""
        uid = msg.get("from_user_id") or ""
        mid = (msg.get("message_id") or "").strip()
        lines.append(f"  [!] [{dt}] FROM {sender} ({uid}): {text}")
        project_ids, task_ids = _agi_tag_ids(msg.get("raw_payload"))
        if project_ids:
            lines.append(f"     → TAGGED PROJECT IDS: {', '.join(project_ids)}")
        if task_ids:
            lines.append(f"     → TAGGED TASK IDS: {', '.join(task_ids)}")
        reply_to = _reply_to_message_id(msg.get("raw_payload"))
        if reply_to:
            lines.append(f"     → REPLY TO MESSAGE ID: {reply_to}")
        lines.append(f"     → mark-processed: agictl message mark-processed {mid}")
    lines.append("")
    lines.append("[!] Reply to ALL items above before proceeding to other work or ending the cycle.")
    lines.append("--- END NEW MESSAGES ---")
    return "\n".join(lines), new_ids


class MidcycleInbox:
    """One spawned agent's unread mail for this cycle. Commit ids only after inject."""

    def __init__(self, agent_name: str, ids: list[str], shown: set[str], runner=None, log=None):
        self.agent_name = (agent_name or "").strip()
        self.ids = list(ids)
        self.shown = set(shown)
        self.runner = runner
        self.log = log
        self._logged_failure = False

    @classmethod
    def from_env(cls, agent_name: str, prompt_text: str, log=None) -> "MidcycleInbox":
        """Bind this cycle: the spawned agent, and ids already in its prompt.

        ``prompt_text`` is the system prompt plus the wake. The unread block
        lives in the system prompt, not the short wake line.
        """
        return cls(
            agent_name,
            identity_ids(agent_name),
            shown_message_ids(prompt_text),
            log=log,
        )

    def poll(self) -> tuple[str, set[str]] | None:
        rows = load_received_unread(self.agent_name, runner=self.runner)
        if rows is None:
            if not self._logged_failure and self.log:
                self.log(
                    f"MID-CYCLE MAIL: unread read failed for {self.agent_name} — continuing"
                )
                self._logged_failure = True
            return None
        text, new_ids = format_arrival_block(rows_for_agent(rows, self.ids), self.shown)
        if not text:
            return None
        return text, new_ids

    def commit(self, new_ids: set[str]) -> None:
        self.shown |= set(new_ids)
