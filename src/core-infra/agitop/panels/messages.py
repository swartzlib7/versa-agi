"""Messages panel — recent messages with live data and message viewer modal."""
from agitop.data import AgentReader

import json
import re
import time
from typing import Optional
from textual import on
from textual.app import ComposeResult
from textual.screen import ModalScreen
from textual.containers import Vertical, Horizontal, VerticalScroll
from textual.widgets import DataTable, Static, Button, Select, TextArea, Markdown
from textual.widget import Widget
import subprocess

from agitop.data import MessageReader
from agitop.data.config_reader import ConfigReader

_TZ = time.strftime("%Z")


class PasteSafeTextArea(TextArea):
    """TextArea that tolerates Textual's WrappedDocument undo bug after paste.

    Pasting into an empty document then undoing (Ctrl+Z) can raise
    ``ValueError: The document line index N is out of bounds`` in
    ``WrappedDocument.get_offsets``. Swallow that so agitop does not exit.
    """

    def action_undo(self) -> None:
        try:
            super().action_undo()
        except ValueError:
            pass


def _utc_to_local(utc_str: str) -> str:
    """Convert 'YYYY-MM-DD HH:MM:SS' UTC string to local timezone."""
    if not utc_str or len(utc_str) < 16:
        return utc_str
    from datetime import datetime, timezone
    try:
        dt = datetime.strptime(utc_str[:19], "%Y-%m-%d %H:%M:%S")
        dt = dt.replace(tzinfo=timezone.utc).astimezone()
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, OSError):
        return utc_str

# Regex to strip emotion/stage-direction tags like [cheerful], [short pause], etc.
_EMOTION_TAG_RE = re.compile(r'\[(?:cheerful|calm|sad|angry|excited|thoughtful|surprised|'
                              r'serious|playful|warm|gentle|laughing|laughs|sighs|chuckles|'
                              r'breathes deeply|pauses|short pause|long pause|whispers|'
                              r'softly|firmly|hesitant|confident|nervous|relieved|'
                              r'[a-z ]{2,25})?\]\s*', re.IGNORECASE)


def strip_emotion_tags(text: str) -> str:
    """Remove [emotion] and [stage direction] tags from message text."""
    if not text:
        return ""
    result = _EMOTION_TAG_RE.sub('', text)
    # Cleanup any remaining empty brackets or whitespace-only brackets
    result = re.sub(r'\[\s*\]\s*', '', result)
    return result.strip()


def _message_has_real_attachments(msg: dict) -> bool:
    """True when the message has user attachments (media/markdown/url), not voice audio URLs."""
    if msg.get("has_attachments"):
        return True
    attach_path = (msg.get("attachment_path") or "").strip()
    if attach_path and not attach_path.startswith("http"):
        return True
    raw_payload = msg.get("raw_payload") or ""
    if not raw_payload:
        return False
    try:
        payload = json.loads(raw_payload) if isinstance(raw_payload, str) else raw_payload
    except (json.JSONDecodeError, TypeError):
        return False
    if isinstance(payload, list):
        return bool(payload)
    attachments = payload.get("attachments") if isinstance(payload, dict) else None
    return isinstance(attachments, list) and bool(attachments)


def _parse_message_attachments(msg: dict) -> tuple[list[str], list[dict]]:
    """Return (label lines, markdown attachment dicts) from raw_payload."""
    labels: list[str] = []
    md_attachments: list[dict] = []
    raw_payload = msg.get("raw_payload") or ""
    if not raw_payload:
        return labels, md_attachments
    try:
        payload = json.loads(raw_payload) if isinstance(raw_payload, str) else raw_payload
    except (json.JSONDecodeError, TypeError):
        return labels, md_attachments

    attachments = payload if isinstance(payload, list) else payload.get("attachments")
    if not isinstance(attachments, list):
        return labels, md_attachments

    for att in attachments:
        if not isinstance(att, dict):
            continue
        att_type = att.get("type", "unknown")
        meta = att.get("meta") or {}
        if att_type == "markdown" and att.get("value"):
            md_attachments.append({
                "title": meta.get("title", "Untitled"),
                "content": att["value"],
            })
        elif att_type == "media":
            labels.append(f"📷 {meta.get('fileName', 'media')}")
        elif att_type == "url":
            labels.append(f"🔗 {att.get('value', 'url')}")
        else:
            labels.append(f"📎 {att_type}")
    return labels, md_attachments


def _is_pu_recipient(msg: dict, primary_uid: Optional[str]) -> bool:
    return (
        bool(primary_uid)
        and msg.get("direction") == "received"
        and msg.get("to_user_id") == primary_uid
    )


def _needs_attention(msg: dict, vv_enabled: bool, primary_uid: Optional[str]) -> bool:
    """Highlight unprocessed inbox rows that still need agent pickup (not PU/VV read queue)."""
    if msg.get("status") != "unprocessed" or msg.get("direction") != "received":
        return False
    if _is_pu_recipient(msg, primary_uid) and vv_enabled:
        return False
    return True


def _display_status(msg: dict, vv_enabled: bool, primary_uid: Optional[str]) -> str:
    status = msg.get("status") or "pending"
    if _is_pu_recipient(msg, primary_uid) and vv_enabled and status == "unprocessed":
        status = "processed"
    if _needs_attention(msg, vv_enabled, primary_uid):
        return "[yellow]⏳ unprocessed[/]"
    if status == "processed":
        return "[green]✓ processed[/]"
    if status == "unprocessed":
        return "[yellow]⏳ unprocessed[/]"
    return status


def _display_attach(has_attach: bool) -> str:
    return "[cyan]📎 Yes[/]" if has_attach else "No"


def _fetch_cycle_routing_summary(cycle_id: str) -> str:
    """Load execution_model / routing_mode from cycles.db via agictl cycle get."""
    cycle_id = (cycle_id or "").strip()
    if not cycle_id or cycle_id == "--":
        return ""
    try:
        proc = subprocess.run(
            ["sudo", "-u", "watchdog", "/usr/local/lib/versa-agi/agictl", "cycle", "get", cycle_id],
            capture_output=True, text=True, timeout=10,
        )
        if not proc.stdout:
            return ""
        data = {}
        for line in reversed(proc.stdout.strip().splitlines()):
            if line.strip().startswith("{"):
                data = json.loads(line)
                break
        if not data.get("success"):
            return ""
        cycle = data.get("cycle") or {}
        exec_m = cycle.get("execution_model") or ""
        assigned = cycle.get("assigned_model") or ""
        mode = cycle.get("routing_mode") or ""
        wm = cycle.get("routing_work_modality") or ""
        if not exec_m and (not mode or mode == "none"):
            return ""
        parts = []
        if exec_m:
            parts.append(f"exec: {exec_m}")
        if assigned and assigned != exec_m:
            parts.append(f"assigned: {assigned}")
        if mode and mode != "none":
            parts.append(f"route: {mode}")
        if wm:
            parts.append(f"modality: {wm}")
        return " · ".join(parts)
    except Exception:
        return ""


class ComposeModal(ModalScreen):
    """Compose a local message to an agent."""

    def __init__(self, agents: list, preselect_agent: str = None,
                 lock_to_agent: str = None, **kwargs):
        super().__init__(**kwargs)
        self.agents = agents
        self.preselect_agent = preselect_agent
        self.lock_to_agent = lock_to_agent

    def compose(self) -> ComposeResult:
        title = "[bold]↩ Reply[/]" if self.lock_to_agent else "[bold]✉ New Message[/]"

        with Vertical(id="compose-dialog"):
            yield Static(title, id="compose-title")
            yield Static("[cyan]To Agent:[/]")
            if self.lock_to_agent:
                yield Static(self.lock_to_agent, id="compose-agent-locked")
            else:
                options = [(a, a) for a in self.agents]
                default = self.preselect_agent or (self.agents[0] if self.agents else None)
                yield Select(options, value=default, id="compose-agent", allow_blank=False)
            yield Static("[cyan]Message:[/]")
            yield PasteSafeTextArea(id="compose-body", classes="msg-input-paper")
            with Horizontal(id="compose-actions"):
                yield Button("Send", variant="success", id="compose-send")
                yield Button("Cancel", classes="dismiss-btn", variant="default", id="compose-cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "compose-send":
            if self.lock_to_agent:
                agent = self.lock_to_agent
            else:
                select = self.query_one("#compose-agent", Select)
                agent = select.value
            body = self.query_one("#compose-body", TextArea).text.strip()
            if not body:
                self.app.notify("Message body is empty", severity="warning")
                return
            # PU identity auto-detected by CLI (caller is not a registered agent)
            try:
                result = subprocess.run(
                    ["sudo", "agictl", "message", "internal", "--from-pu", str(agent), body],
                    capture_output=True, timeout=15, text=True
                )
                if result.returncode == 0:
                    self.app.notify(f"Message sent to {agent}", title="Sent")
                else:
                    err = result.stderr.strip() or result.stdout.strip() or "Unknown error"
                    self.app.notify(f"Failed: {err[:100]}", severity="error")
            except Exception as e:
                self.app.notify(f"Failed: {e}", severity="error")
            self.app.pop_screen()
            # Schedule refresh after modal is fully dismissed
            self.app.call_later(lambda: self.app.query_one(MessagesPanel).refresh_data())
        elif event.button.id == "compose-cancel":
            self.app.pop_screen()


class MarkdownViewModal(ModalScreen):
    """Modal dialog to render markdown content with syntax highlighting."""

    def __init__(self, title: str, content: str, **kwargs):
        super().__init__(**kwargs)
        self.md_title = title
        self.md_content = content

    def compose(self) -> ComposeResult:
        with Vertical(id="md-viewer-dialog"):
            yield Static(f"[bold cyan]📄 {self.md_title}[/]", id="md-viewer-title")
            with VerticalScroll(id="md-viewer-scroll"):
                yield Markdown(self.md_content, id="md-viewer-content")
            with Horizontal(id="md-viewer-actions"):
                yield Button("📋 Copy", variant="default", id="md-copy")
                yield Button("Close", classes="dismiss-btn", variant="default", id="md-close")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "md-close":
            self.app.pop_screen()
        elif event.button.id == "md-copy":
            try:
                subprocess.run(["xclip", "-selection", "clipboard"], input=self.md_content.encode(), check=True)
                self.app.notify("Markdown copied", title="Clipboard")
            except FileNotFoundError:
                try:
                    subprocess.run(["xsel", "--clipboard", "--input"], input=self.md_content.encode(), check=True)
                    self.app.notify("Markdown copied", title="Clipboard")
                except Exception:
                    self.app.notify("Install xclip or xsel for clipboard support", severity="warning")
            except Exception:
                self.app.notify("Clipboard copy failed", severity="warning")


class MessageViewModal(ModalScreen):
    """Modal dialog to show full message content with edit/delete actions."""

    def __init__(self, msg: dict, agent_name: str = "Agent",
                 primary_name: str = None,
                 primary_uid: str = None,
                 reply_agent: str = None,
                 from_display: str = None,
                 to_display: str = None,
                 vv_enabled: bool = True,
                 message_reader: Optional[MessageReader] = None,
                 **kwargs):
        super().__init__(**kwargs)
        self.msg = msg
        self.agent_name = agent_name
        self.primary_name = primary_name
        self.primary_uid = primary_uid
        self.reply_agent = reply_agent
        self.from_display = from_display or "Unknown"
        self.to_display = to_display or "Unknown"
        self.vv_enabled = vv_enabled
        self.message_reader = message_reader
        self._confirm_delete = False
        status = msg.get("status", "")
        self._original_status = status if status else "processed"

    def compose(self) -> ComposeResult:
        msg = self.msg
        direction = msg.get("direction", "sent")
        arrow = "→ SENT" if direction == "sent" else "← RECEIVED"

        # Resolve from/to names from actual message data (not direction)
        from_name = self.from_display
        to_name = self.to_display

        mode = msg.get("mode", "typed")
        created = _utc_to_local(msg.get("created_at", ""))
        msg_id = msg.get("message_id", "")
        cycle = msg.get("cycle_id") or "--"
        routing_summary = _fetch_cycle_routing_summary(cycle) if cycle != "--" else ""
        cycle_display = f"{cycle}\n[dim]{routing_summary}[/]" if routing_summary else cycle
        has_attach = _message_has_real_attachments(msg)
        attach_display = _display_attach(has_attach)

        # Prefer original_text (with emotion tags) for dashboard display
        raw_text = msg.get("original_text") or msg.get("text") or ""
        if not raw_text:
            raw_text = "(empty message)"
        # Store raw text for copy button
        self._raw_text = raw_text

        attach_labels, self._md_attachments = _parse_message_attachments(msg)
        if has_attach and not attach_labels and not self._md_attachments:
            attach_path = (msg.get("attachment_path") or "").strip()
            if attach_path and not attach_path.startswith("http"):
                attach_labels.append(f"📁 {attach_path}")

        # Status selector options (must match messages.db CHECK constraint)
        status_options = [
            ("unprocessed", "unprocessed"),
            ("processed", "processed"),
            ("sent", "sent"),
            ("delivered", "delivered"),
            ("pending", "pending"),
            ("error", "error"),
        ]
        current_status = (
            self._original_status
            if self._original_status in dict(status_options)
            else "processed"
        )
        if _is_pu_recipient(msg, self.primary_uid) and self.vv_enabled and current_status == "unprocessed":
            current_status = "processed"

        with Vertical(id="msg-dialog"):
            with Vertical(id="msg-dialog-top"):
                yield Static(f"[bold]{arrow}[/]  [dim]{created}[/]", id="msg-dialog-direction")
                with Horizontal(classes="msg-dialog-meta"):
                    yield Static(f"[cyan]From:[/]\n{from_name}", classes="msg-meta-col")
                    yield Static(f"[cyan]To:[/]\n{to_name}", classes="msg-meta-col")
                    yield Static(f"[cyan]Mode:[/]\n{mode}", classes="msg-meta-col")
                with Horizontal(classes="msg-dialog-meta"):
                    yield Static(f"[cyan]Message ID:[/]\n{msg_id}", classes="msg-meta-col")
                    yield Static(f"[cyan]Cycle:[/]\n{cycle_display}", classes="msg-meta-col")
                    yield Static(f"[cyan]Attachments:[/]\n{attach_display}", classes="msg-meta-col")
                with Horizontal(id="msg-status-row"):
                    yield Static("[cyan]Status:[/]")
                    yield Select(
                        status_options,
                        value=current_status,
                        id="select-msg-status",
                        allow_blank=False,
                    )
            with VerticalScroll(id="msg-dialog-scroll"):
                with Vertical(id="msg-body-pane"):
                    yield TextArea(raw_text, id="msg-dialog-body", read_only=True)
                    if attach_labels or self._md_attachments:
                        with Vertical(id="msg-attachments"):
                            if attach_labels:
                                yield Static("[cyan]Attachments:[/]\n" + "\n".join(attach_labels))
                            for idx, md_att in enumerate(self._md_attachments):
                                yield Button(
                                    f"📄 View: {md_att['title']}",
                                    variant="warning",
                                    id=f"md-view-{idx}",
                                )
                if self.reply_agent:
                    with Vertical(id="msg-reply-section"):
                        yield Static(f"[cyan]Reply To:[/] {self.reply_agent}")
                        yield Static("[cyan]Reply:[/]")
                        yield PasteSafeTextArea("", id="msg-reply-body", classes="msg-input-paper")
            with Horizontal(id="msg-dialog-footer"):
                if self.reply_agent:
                    yield Button("💬 Reply", variant="warning", id="msg-reply", disabled=True)
                yield Button("Save Status", variant="success", id="msg-save-status", disabled=True)
                yield Button("📋 Copy Body", variant="primary", id="msg-copy-body")
                yield Button("Copy ID", variant="primary", id="msg-dialog-copy-id")
                yield Button("Delete", variant="error", id="msg-delete")
                yield Button("Close", classes="dismiss-btn", variant="default", id="msg-dialog-close")

    def on_mount(self) -> None:
        """Auto-mark Primary User (PU) inbox messages as processed when the dashboard viewer opens them."""
        if not self.message_reader or not _is_pu_recipient(self.msg, self.primary_uid):
            return
        msg_id = self.msg.get("message_id") or ""
        if not msg_id or self.msg.get("status") != "unprocessed":
            return
        if self.message_reader.update_message_status(msg_id, "processed"):
            self._original_status = "processed"
            self.msg["status"] = "processed"
            try:
                status_select = self.query_one("#select-msg-status", Select)
                status_select.value = "processed"
            except Exception:
                pass
            try:
                self.app.query_one(MessagesPanel).refresh_data()
            except Exception:
                pass

    def _update_action_buttons(self) -> None:
        status_select = self.query_one("#select-msg-status", Select)
        save_btn = self.query_one("#msg-save-status", Button)
        current_status = status_select.value
        status_dirty = isinstance(current_status, str) and current_status != self._original_status
        save_btn.disabled = not status_dirty

        if self.reply_agent:
            reply_text = self.query_one("#msg-reply-body", TextArea).text.strip()
            self.query_one("#msg-reply", Button).disabled = not reply_text

    @on(Select.Changed, "#select-msg-status")
    def on_status_changed(self, event: Select.Changed) -> None:
        self._update_action_buttons()

    @on(TextArea.Changed, "#msg-reply-body")
    def on_reply_changed(self, event: TextArea.Changed) -> None:
        reply_text = self.query_one("#msg-reply-body", TextArea).text.strip()
        self.query_one("#msg-reply", Button).disabled = not reply_text

    def on_button_pressed(self, event: Button.Pressed) -> None:
        msg_id = self.msg.get("message_id") or ""

        # Handle markdown viewer buttons (md-view-0, md-view-1, etc.)
        if event.button.id and event.button.id.startswith("md-view-"):
            try:
                idx = int(event.button.id.split("-")[-1])
                md_att = self._md_attachments[idx]
                self.app.push_screen(MarkdownViewModal(
                    title=md_att["title"],
                    content=md_att["content"]
                ))
            except (IndexError, ValueError):
                self.app.notify("Attachment not found", severity="warning")
            return

        if event.button.id == "msg-dialog-close":
            self.app.pop_screen()
        elif event.button.id == "msg-reply":
            body = self.query_one("#msg-reply-body", TextArea).text.strip()
            if not body:
                self.app.notify("Reply is empty", severity="warning")
                return
            try:
                result = subprocess.run(
                    ["sudo", "agictl", "message", "internal", "--from-pu",
                     str(self.reply_agent), body],
                    capture_output=True, timeout=15, text=True
                )
                if result.returncode == 0:
                    self.app.notify(f"Reply sent to {self.reply_agent}", title="Sent")
                    self.query_one("#msg-reply-body", TextArea).text = ""
                    self._update_action_buttons()
                    self.app.query_one(MessagesPanel).refresh_data()
                else:
                    err = result.stderr.strip() or result.stdout.strip() or "Unknown error"
                    self.app.notify(f"Failed: {err[:100]}", severity="error")
            except Exception as e:
                self.app.notify(f"Reply failed: {e}", severity="error")
        elif event.button.id == "msg-copy-body":
            try:
                subprocess.run(["xclip", "-selection", "clipboard"], input=self._raw_text.encode(), check=True)
                self.app.notify("Message body copied", title="Clipboard")
            except FileNotFoundError:
                try:
                    subprocess.run(["xsel", "--clipboard", "--input"], input=self._raw_text.encode(), check=True)
                    self.app.notify("Message body copied", title="Clipboard")
                except Exception:
                    self.app.notify("Install xclip or xsel for clipboard support", severity="warning")
            except Exception:
                self.app.notify("Clipboard copy failed", severity="warning")
        elif event.button.id == "msg-dialog-copy-id":
            if msg_id:
                try:
                    subprocess.run(["xclip", "-selection", "clipboard"], input=str(msg_id).encode(), check=True)
                    self.app.notify(f"Copied: {msg_id}", title="Message ID")
                except Exception:
                    self.app.notify(f"ID: {msg_id} (xclip not available)", title="Message ID")
        elif event.button.id == "msg-save-status":
            try:
                status_select = self.query_one("#select-msg-status", Select)
                new_status = status_select.value
                if isinstance(new_status, str) and new_status:
                    messages_panel = self.app.query_one(MessagesPanel)
                    ok = messages_panel.message_reader.update_message_status(msg_id, new_status)
                    if ok:
                        self._original_status = new_status
                        self._update_action_buttons()
                        self.app.notify(f"Status → {new_status}", title="Message Updated")
                        messages_panel.refresh_data()
                    else:
                        self.app.notify("Failed to update status", title="Error", severity="error")
            except Exception as e:
                self.app.notify(f"Error: {e}", title="Error", severity="error")
        elif event.button.id == "msg-delete":
            if not self._confirm_delete:
                self._confirm_delete = True
                event.button.label = "Confirm Delete?"
            else:
                messages_panel = self.app.query_one(MessagesPanel)
                ok = messages_panel.message_reader.delete_message(msg_id)
                if ok:
                    # Also delete from VV cloud if it's a VV-channel message
                    channel = self.msg.get("channel", "vv")
                    channel_id = self.msg.get("channel_id") or ""
                    if channel == "vv" and channel_id and msg_id:
                        try:
                            cloud = subprocess.run(
                                ["sudo", "agictl", "message", "delete", msg_id, "--channel", channel_id],
                                capture_output=True, timeout=15, text=True,
                            )
                            if cloud.returncode != 0:
                                err = (cloud.stderr or cloud.stdout or "cloud delete failed").strip()
                                self.app.notify(
                                    f"Local delete OK; cloud delete failed: {err[:80]}",
                                    title="Partial Delete",
                                    severity="warning",
                                )
                        except Exception as e:
                            self.app.notify(
                                f"Local delete OK; cloud delete failed: {e}",
                                title="Partial Delete",
                                severity="warning",
                            )
                    self.app.notify(f"Deleted message {msg_id[:12]}...", title="Message Deleted")
                    messages_panel.refresh_data()
                else:
                    self.app.notify("Failed to delete message", title="Error", severity="error")
                self.app.pop_screen()

class DeleteMessageConfirmModal(ModalScreen):
    """Confirmation modal for deleting a message (local tombstone + optional VV cloud)."""

    CSS = """
    DeleteMessageConfirmModal {
        align: center middle;
        background: $surface 80%;
    }
    #message-delete-dialog {
        width: 64;
        height: auto;
        padding: 1 2;
        border: heavy $error;
        background: $surface;
    }
    #message-delete-actions {
        margin-top: 1;
        height: auto;
        align: center middle;
    }
    #message-delete-actions Button {
        width: 1fr;
        margin: 0 1;
        min-width: 16;
        height: 3;
    }
    """

    def __init__(self, msg: dict, message_reader: MessageReader, **kwargs):
        super().__init__(**kwargs)
        self.msg = msg
        self.message_reader = message_reader

    def compose(self) -> ComposeResult:
        msg_id = self.msg.get("message_id") or "(unknown)"
        preview = (self.msg.get("original_text") or self.msg.get("text") or "").strip()
        if len(preview) > 120:
            preview = preview[:117] + "..."
        channel = self.msg.get("channel", "vv")
        cloud_note = ""
        if channel == "vv" and (self.msg.get("channel_id") or ""):
            cloud_note = "\n\nAlso removes the message from VersaVoice cloud when applicable."
        with Vertical(id="message-delete-dialog"):
            yield Static(f"[bold red]⚠ Delete Message[/]\n")
            yield Static(f"[dim]ID: {msg_id}[/]")
            if preview:
                yield Static(f"[dim]{preview}[/]\n")
            yield Static(
                "Marks the message deleted locally (tombstone)."
                f"{cloud_note}\n\n[bold]This cannot be undone.[/]"
            )
            with Horizontal(id="message-delete-actions"):
                yield Button("Delete", variant="error", id="btn-message-delete-confirm")
                yield Button("Close", classes="dismiss-btn", variant="default", id="btn-message-delete-cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id != "btn-message-delete-confirm":
            self.dismiss(None)
            return

        msg_id = self.msg.get("message_id") or ""
        ok = self.message_reader.delete_message(msg_id) if msg_id else False
        self.dismiss(None)
        if not ok:
            self.app.notify("Failed to delete message", title="Error", severity="error")
            return

        channel = self.msg.get("channel", "vv")
        channel_id = self.msg.get("channel_id") or ""
        if channel == "vv" and channel_id and msg_id:
            try:
                cloud = subprocess.run(
                    ["sudo", "agictl", "message", "delete", msg_id, "--channel", channel_id],
                    capture_output=True, timeout=15, text=True,
                )
                if cloud.returncode != 0:
                    err = (cloud.stderr or cloud.stdout or "cloud delete failed").strip()
                    self.app.notify(
                        f"Local delete OK; cloud delete failed: {err[:80]}",
                        title="Partial Delete",
                        severity="warning",
                    )
            except Exception as e:
                self.app.notify(
                    f"Local delete OK; cloud delete failed: {e}",
                    title="Partial Delete",
                    severity="warning",
                )

        self.app.notify(f"Deleted message {msg_id[:12]}...", title="Message Deleted")
        try:
            self.app.query_one(MessagesPanel).refresh_data()
        except Exception:
            pass

    def on_key(self, event) -> None:
        if event.key == "escape":
            event.stop()
            self.dismiss(None)


class MessagesPanel(Widget):
    """Displays recent messages from SQLite with interactive triggers."""

    PAGE_SIZE = 50

    def __init__(self, message_reader: Optional[MessageReader],
                 config: Optional[ConfigReader] = None,
                 agent_reader: Optional[AgentReader] = None, **kwargs):
        super().__init__(**kwargs)
        self.message_reader = message_reader
        self.config = config
        self.agent_reader = agent_reader
        self._agent_name: Optional[str] = None
        self._primary_name: Optional[str] = None
        self._primary_uid: Optional[str] = None
        self._uid_to_agent: dict[str, str] = {}
        self.table = DataTable(id="messages-table")
        self._msg_data: dict[str, dict] = {}
        self._channel_filter: str = None  # None = all, 'vv', 'internal'
        self._page = 0
        self._total = 0

    def _get_agent_name(self) -> str:
        """Resolve agent display name from config (cached)."""
        if self._agent_name is None and self.config:
            identity = self.config.get_identity()
            first = identity.get("first_name", "")
            last = identity.get("last_name", "")
            if first:
                self._agent_name = f"{first} {last}".strip() if last else first
        return self._agent_name or "Agent"

    def _get_primary_name(self) -> str:
        """Resolve Primary User display name from config (cached)."""
        if self._primary_name is None and self.config:
            pu = self.config.get_config().get("primary_user", {})
            self._primary_name = pu.get("display_name")
        return self._primary_name

    def _get_primary_uid(self) -> Optional[str]:
        """Resolve Primary User UID from config (cached)."""
        if self._primary_uid is None and self.config:
            self._primary_uid = self.config.get_config().get("primary_user", {}).get("uid")
        return self._primary_uid

    def compose(self) -> ComposeResult:
        with Vertical(id="messages-panel-body", classes="work-tab-body"):
            with Horizontal(classes="panel-header"):
                with Horizontal(id="channel-filters"):
                    yield Static("All", id="btn-ch-all", classes="action-link-filter")
                    yield Static(" | ", classes="header-sep")
                    yield Static("VV", id="btn-ch-vv", classes="action-link-filter")
                    yield Static(" | ", classes="header-sep")
                    yield Static("Internal", id="btn-ch-internal", classes="action-link-filter")

            yield self.table
            with Horizontal(classes="work-tab-actions"):
                yield Button("Retrieve Messages Now", variant="warning", id="btn-messages-fetch")
                yield Button("New", variant="success", id="btn-messages-new")
                yield Button("Edit", variant="primary", id="btn-messages-edit", disabled=True)
                yield Button("Delete", variant="error", id="btn-messages-delete", disabled=True)
                yield Button("Quit", variant="default", id="btn-app-quit", classes="btn-quit")

    def on_mount(self) -> None:
        self._update_title()
        self.table.cursor_type = "row"
        # Compact metadata cols; Text is the primary readable field.
        self.table.add_column("ID", width=6)
        self.table.add_column("Channel", width=10)
        self.table.add_column("Msg Src", width=8)
        self.table.add_column("Dir", width=4)
        self.table.add_column("From", width=12)
        self.table.add_column("To", width=12)
        self.table.add_column("Text", width=90)
        self.table.add_column("Mode", width=8)
        self.table.add_column("Status", width=10)
        self.table.add_column("Attach", width=6)
        self.table.add_column(f"Date/Time ({_TZ})", width=14)
        self.table.add_column("Cycle", width=8)
        self._update_filter_links()
        self.refresh_data()

    def _update_title(self) -> None:
        total_pages = max(1, (self._total + self.PAGE_SIZE - 1) // self.PAGE_SIZE)
        current_page = self._page + 1
        self.table.border_title = (
            f"Messages ({self._total})  │  Page {current_page}/{total_pages}  │  "
            f"PgUp/PgDn to navigate"
        )

    def _selected_row_key(self) -> str | None:
        try:
            row_key, _ = self.table.coordinate_to_cell_key(self.table.cursor_coordinate)
            if row_key and row_key.value:
                return str(row_key.value)
        except Exception:
            pass
        return None

    def _update_action_buttons(self) -> None:
        has_row = bool(self._selected_row_key())
        try:
            self.query_one("#btn-messages-edit", Button).disabled = not has_row
            self.query_one("#btn-messages-delete", Button).disabled = not has_row
        except Exception:
            pass

    @on(DataTable.RowHighlighted, "#messages-table")
    def _on_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        self._update_action_buttons()

    def _update_filter_links(self) -> None:
        """Visually highlight active channel filter link."""
        link_map = {"btn-ch-all": None, "btn-ch-vv": "vv", "btn-ch-internal": "internal"}
        for link_id, channel in link_map.items():
            try:
                link = self.query_one(f"#{link_id}", Static)
                if self._channel_filter == channel:
                    link.add_class("action-link-filter-active")
                else:
                    link.remove_class("action-link-filter-active")
            except Exception:
                pass

    def refresh_data(self) -> None:
        """Refresh message data from SQLite with pagination."""
        self._total = self.message_reader.count_messages(
            channel=self._channel_filter
        ) if self.message_reader else 0
        offset = self._page * self.PAGE_SIZE
        messages = self.message_reader.get_recent_messages(
            limit=self.PAGE_SIZE, channel=self._channel_filter, offset=offset
        ) if self.message_reader else []

        self.table.clear()
        self._msg_data = {}
        self._update_title()

        if not messages:
            return

        agent_name = self._get_agent_name()
        primary_name = self._get_primary_name()
        vv_enabled = self._is_vv_enabled()
        pu_uid = self._get_primary_uid()

        # Build UID→agent name lookup (cached after first call)
        if not self._uid_to_agent and self.agent_reader:
            self._uid_to_agent = self.agent_reader.build_uid_to_agent_map()

        # Build a UID→name lookup for sent message recipients
        recipient_cache: dict[str, str] = {}

        for idx, msg in enumerate(messages):
            direction = msg.get("direction", "sent")
            from_uid = msg.get("from_user_id") or ""
            to_uid = msg.get("to_user_id") or ""
            channel = msg.get("channel", "vv")
            
            # Resolve agent name from sub_account_id
            if direction == "sent":
                agent_label = self._uid_to_agent.get(from_uid, "--")
            else:
                agent_label = self._uid_to_agent.get(to_uid, "--")
            
            # Resolve From and To names
            from_name, to_name = self._resolve_from_to_names(msg, recipient_cache)

            # Prefer original_text (with emotion tags) for dashboard display
            clean_text = msg.get("original_text") or msg.get("text") or ""
            mode = msg.get("mode", "typed")
            status = _display_status(msg, vv_enabled, pu_uid)
            has_attach = _display_attach(_message_has_real_attachments(msg))
            # Format: "YYYY-MM-DD HH:MM:SS" → "MM-DD HH:MM"
            raw_ts = _utc_to_local(msg.get("created_at", ""))
            if len(raw_ts) >= 16:
                time_str = raw_ts[5:16]  # MM-DD HH:MM
            elif len(raw_ts) >= 8:
                time_str = raw_ts[-8:]   # HH:MM:SS fallback
            else:
                time_str = raw_ts
            cycle = str(msg.get("cycle_id") or "--")

            # Truncate message text (preview — full text in row-select modal)
            desc_truncated = clean_text[:360] + "..." if len(clean_text) > 363 else clean_text
            desc_truncated = desc_truncated.replace("[", "\\[")

            arrow = "[green]→[/]" if direction == "sent" else "[cyan]←[/]"
            ch_tag = "[cyan]INT[/]" if channel == "internal" else "VV"
            msg_id = msg.get("message_id") or ""
            db_id = str(msg.get("id") or idx)
            row_key = db_id
            self._msg_data[row_key] = msg
            
            self.table.add_row(
                str(db_id),
                f"[bold]{agent_label}[/]" if agent_label != "--" else "[dim]--[/]",
                ch_tag,
                arrow,
                from_name,
                to_name,
                desc_truncated,
                mode,
                status,
                has_attach,
                time_str,
                cycle,
                key=row_key
            )
        self._update_action_buttons()

    def _open_compose_modal(self) -> None:
        agents = []
        if self.agent_reader:
            try:
                all_agents = self.agent_reader.get_all_agents()
                agents = [a["name"] for a in all_agents
                          if a.get("name") not in ("watchdog",) and not a.get("inactive")]
            except Exception:
                pass
        if not agents:
            self.app.notify("No agents found", severity="warning")
            return
        self.app.push_screen(ComposeModal(agents=sorted(agents)))

    def _open_message_modal(self, msg: dict) -> None:
        if not msg:
            return
        if not self._uid_to_agent and self.agent_reader:
            self._uid_to_agent = self.agent_reader.build_uid_to_agent_map()
        from_name, to_name = self._resolve_from_to_names(msg)
        self.app.push_screen(MessageViewModal(
            msg,
            agent_name=self._get_agent_name(),
            primary_name=self._get_primary_name(),
            primary_uid=self._get_primary_uid(),
            reply_agent=self._resolve_reply_agent(msg),
            from_display=from_name,
            to_display=to_name,
            vv_enabled=self._is_vv_enabled(),
            message_reader=self.message_reader,
        ))

    def _try_delete_selected(self) -> None:
        row_key = self._selected_row_key()
        if not row_key or not self.message_reader:
            return
        msg = self._msg_data.get(row_key, {})
        if msg:
            self.app.push_screen(DeleteMessageConfirmModal(msg, self.message_reader))

    def _lookup_uid_display_name(self, uid: str, cache: dict[str, str]) -> str:
        """Resolve a UID to a short display name via agent map or message history."""
        if uid in self._uid_to_agent:
            return self._uid_to_agent[uid]
        if uid in cache:
            return cache[uid]
        try:
            rows = self.message_reader._query(
                "SELECT display_name FROM messages WHERE (from_user_id=? OR to_user_id=?) "
                "AND display_name IS NOT NULL AND display_name != '' "
                "ORDER BY created_at DESC LIMIT 1",
                (uid, uid)
            )
            found_name = rows[0]["display_name"] if rows else None
            if found_name and len(found_name) < 50:
                cache[uid] = found_name
                return found_name
        except Exception:
            pass
        short = uid[:8] if uid else "(unknown)"
        cache[uid] = short
        return short

    def _resolve_from_to_names(self, msg: dict, recipient_cache: Optional[dict] = None) -> tuple[str, str]:
        """Resolve sender/recipient display names for table and modal."""
        recipient_cache = recipient_cache if recipient_cache is not None else {}
        if not self._uid_to_agent and self.agent_reader:
            self._uid_to_agent = self.agent_reader.build_uid_to_agent_map()

        direction = msg.get("direction", "sent")
        db_name = (msg.get("display_name") or "").strip()
        from_uid = msg.get("from_user_id") or ""
        to_uid = msg.get("to_user_id") or ""
        primary_name = self._get_primary_name()
        agent_name = self._get_agent_name()
        pu_uid = self._get_primary_uid()

        if direction == "sent":
            from_name = (
                self._uid_to_agent.get(from_uid)
                or db_name
                or (from_uid[:8] if from_uid else "(unknown)")
            )
            if to_uid:
                if to_uid in self._uid_to_agent:
                    to_name = self._uid_to_agent[to_uid]
                elif pu_uid and to_uid == pu_uid:
                    to_name = primary_name or "Primary User"
                else:
                    to_name = self._lookup_uid_display_name(to_uid, recipient_cache)
            else:
                to_name = "(unknown)"
        else:
            if pu_uid and to_uid == pu_uid:
                to_name = primary_name or "Primary User"
            else:
                to_name = self._uid_to_agent.get(to_uid) or agent_name

            if from_uid and from_uid in self._uid_to_agent:
                from_name = self._uid_to_agent[from_uid]
            elif pu_uid and from_uid == pu_uid:
                from_name = primary_name or "Primary User"
            elif db_name and len(db_name) < 50 and "!" not in db_name:
                from_name = db_name
            elif from_uid:
                from_name = from_uid[:8]
            else:
                from_name = "(unknown)"

        return from_name, to_name

    def _resolve_reply_agent(self, msg: dict) -> Optional[str]:
        """Return agent name to reply to for PU-addressed messages."""
        primary_uid = self._get_primary_uid()
        to_uid = msg.get("to_user_id", "")
        if not primary_uid or to_uid != primary_uid:
            return None
        from_uid = msg.get("from_user_id", "")
        agent_name = self._uid_to_agent.get(from_uid)
        if not agent_name:
            display = (msg.get("display_name") or "").strip()
            if display and len(display) < 50:
                agent_name = display
            else:
                agent_name = from_uid or None
        if not agent_name or agent_name in ("watchdog",):
            return None
        return agent_name

    @on(DataTable.RowSelected, "#messages-table")
    def on_row_selected(self, event: DataTable.RowSelected) -> None:
        """Launch message viewer modal on row selection."""
        msg = self._msg_data.get(event.row_key.value, {})
        self._open_message_modal(msg)

    def _is_vv_enabled(self) -> bool:
        """Read VV enabled state from setup.ini."""
        try:
            import configparser
            config = configparser.ConfigParser()
            config.read("/etc/versa-agi/setup.ini")
            return config.get("versavoice", "enabled", fallback="true").lower() == "true"
        except Exception:
            return True

    def _trigger_fetch_inbox(self) -> None:
        command = ["sudo", "-u", "watchdog", "/home/watchdog/core-infra/lifeline.sh", "--force"]
        try:
            subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.app.notify("Cloud Firehose Activated! Polling Data Gateway...", title="Versa AGi")
        except Exception as e:
            self.app.notify(f"Failed to trigger Lifeline: {e}", title="Error", severity="error")

    def on_click(self, event) -> None:
        widget_id = getattr(event.widget, "id", None)
        if widget_id == "btn-ch-all":
            self._channel_filter = None
            self._page = 0
            self._update_filter_links()
            self.refresh_data()
        elif widget_id == "btn-ch-vv":
            self._channel_filter = "vv"
            self._page = 0
            self._update_filter_links()
            self.refresh_data()
        elif widget_id == "btn-ch-internal":
            self._channel_filter = "internal"
            self._page = 0
            self._update_filter_links()
            self.refresh_data()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-messages-fetch":
            self._trigger_fetch_inbox()
        elif event.button.id == "btn-messages-new":
            self._open_compose_modal()
        elif event.button.id == "btn-messages-edit":
            msg = self._msg_data.get(self._selected_row_key() or "", {})
            self._open_message_modal(msg)
        elif event.button.id == "btn-messages-delete":
            self._try_delete_selected()
        elif event.button.id == "btn-app-quit":
            self.app.exit()

    def on_key(self, event) -> None:
        if event.key in ("delete", "backspace"):
            self._try_delete_selected()
        elif event.key == "pagedown":
            max_page = max(0, (self._total - 1) // self.PAGE_SIZE)
            if self._page < max_page:
                self._page += 1
                self.refresh_data()
        elif event.key == "pageup":
            if self._page > 0:
                self._page -= 1
                self.refresh_data()
