import os
import json
import base64
import mimetypes
import time
import urllib.request
import urllib.error
import urllib.parse
from collections import deque
from urllib.parse import urlparse
import sqlite3
from rich.console import Console

console = Console()

VV_API_BASE = "https://us-central1-versavoice-s777.cloudfunctions.net/api/v1"
MAX_FILE_SIZE = 52428800 # 50MB

# ── Rate Limiter (VV API: 60 req/min) ──
# Use 55 as effective limit with 5-request safety margin
_RATE_LIMIT = 55
_RATE_WINDOW = 60  # seconds
_request_timestamps: deque = deque()

def _rate_limit_wait():
    """Sliding-window rate limiter. Blocks if approaching 60 req/min VV API limit."""
    now = time.monotonic()
    # Purge timestamps older than the window
    while _request_timestamps and _request_timestamps[0] < now - _RATE_WINDOW:
        _request_timestamps.popleft()
    # If at limit, sleep until the oldest call expires
    if len(_request_timestamps) >= _RATE_LIMIT:
        sleep_for = _request_timestamps[0] - (now - _RATE_WINDOW) + 0.1
        if sleep_for > 0:
            console.print(f"[yellow]Rate limit: sleeping {sleep_for:.1f}s[/yellow]")
            time.sleep(sleep_for)
            # Notify PU via task (debounced — max once per 5 min)
            _rate_limit_notify(sleep_for)
    _request_timestamps.append(time.monotonic())

# Debounce: track last notification time
_last_rate_notify: float = 0.0

def _rate_limit_notify(sleep_duration: float):
    """Create a high-priority task to notify PU about rate limiting. Max once per 5 min."""
    import subprocess
    global _last_rate_notify
    now = time.monotonic()
    if now - _last_rate_notify < 300:  # 5 min debounce
        return
    _last_rate_notify = now
    try:
        subprocess.run(
            ["/usr/local/bin/agictl", "task", "add",
             f"RATE_LIMIT: VV API rate limit hit — throttled for {sleep_duration:.1f}s. High message volume detected.",
             "--priority", "high", "--callback", "notify_sponsor"],
            capture_output=True, timeout=5
        )
    except Exception:
        pass  # Best-effort — don't break the API call chain

def api_request(endpoint, token, method="GET", body=None):
    _rate_limit_wait()
    url = VV_API_BASE + endpoint
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    data = json.dumps(body).encode("utf-8") if body else None
    # TTS generation (speak mode) can take 15-30s for longer messages
    timeout = 60 if method == "POST" else 10
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            error_body = e.read().decode("utf-8")
            parsed_error = json.loads(error_body)
            # Log the parsed error message if available
            err_msg = parsed_error.get("message", "Unknown HTTP Error")
            console.print(f"[bold red]API Error ({endpoint}) [{e.code}]:[/bold red] {err_msg}")
            return parsed_error
        except Exception:
            console.print(f"[bold red]API Error ({endpoint}) [{e.code}]:[/bold red] {str(e)}")
            return None
    except Exception as e:
        console.print(f"[bold red]Network Error ({endpoint}):[/bold red] {str(e)}")
        return None

def fetch_inbox(agent_user, agent_path, sub_account_id, token, messages_db):
    """Fetches messages from VersaVoice Cloud and persists them cleanly to messages.db.
    
    Uses unreadOnly=false with a rolling 2-hour window to prevent the race condition
    where the user reads a message in the VV app before the CRON tick fires.
    The existing dedup check (message_id) prevents double-inserts.
    """
    from datetime import datetime, timedelta, timezone
    since = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
    
    endpoint = f"/messages/inbox?subAccountId={sub_account_id}&unreadOnly=false&since={since}"
    response = api_request(endpoint, token)
    
    if isinstance(response, list):
        messages = response
    elif isinstance(response, dict) and "messages" in response:
        messages = response.get("messages", [])
    else:
        return False
        
    if not messages:
        return True
        
    inserted = 0
    try:
        conn = sqlite3.connect(messages_db, timeout=5)
        c = conn.cursor()
        
        for msg in messages:
            msg_id = msg.get("messageId") or msg.get("id")
            if not msg_id:
                continue
                
            # Sender is in the "from" object: {uid, displayName, senderId}
            sender_obj = msg.get("from", {})
            sender_id = sender_obj.get("uid") or sender_obj.get("senderId") or msg.get("senderUid") or "unknown"
            sender_name = sender_obj.get("displayName") or msg.get("senderName") or "Unknown"
            # The inbox API's 'text' field is already Neural Text (no tags):
            # cascade: translatedTextNt → translatedText → enhancedSourceTextNt → originalText
            # Use it for display; 'originalText' retains emotion tags for the original_text column
            text = msg.get("text") or msg.get("originalTextNt") or msg.get("originalText", "")
            if not text:
               text = msg.get("translatedText") or ""
            
            # Store the raw VV mode as-is (typed, transcribe, translate, speak, etc.)
            raw_mode = msg.get("generationMode") or msg.get("mode", "typed")
            
            c.execute("SELECT id FROM messages WHERE message_id = ?", (msg_id,))
            if c.fetchone():
                continue
                
            c.execute('''INSERT INTO messages 
                (message_id, direction, status, from_user_id, to_user_id, original_text, text, attachment_path, created_at, mode, raw_payload, display_name, channel_id)
                VALUES (?, 'received', 'unprocessed', ?, ?, ?, ?, ?, datetime('now'), ?, ?, ?, ?)''',
                (msg_id, sender_id, sub_account_id, msg.get("originalText", ""), text, msg.get("audioUrl") or msg.get("translatedAudioUrl", ""), raw_mode, json.dumps(msg), sender_name, msg.get("channelId", ""))
            )
            inserted += 1
            
            # --- Attachment Pipeline: download all attachment types to workspace ---
            attachments = msg.get("attachments", [])
            audio_url = msg.get("audioUrl") or msg.get("translatedAudioUrl")
            
            # Determine workspace base for attachments
            attach_base = os.path.join(agent_path, ".agent", "attachments", msg_id)
            has_any_attachment = False
            
            # 1. Handle structured attachments array (media, markdown, url)
            if attachments and isinstance(attachments, list):
                for att_idx, att in enumerate(attachments):
                    att_type = att.get("type", "")
                    att_value = att.get("value", "")
                    att_meta = att.get("meta") or {}
                    
                    if not att_value:
                        continue
                    
                    try:
                        if att_type == "media":
                            media_dir = os.path.join(attach_base, "media")
                            os.makedirs(media_dir, exist_ok=True)
                            file_name = att_meta.get("fileName", f"media_{att_idx}")
                            local_path = os.path.join(media_dir, file_name)
                            req = urllib.request.Request(att_value)
                            with urllib.request.urlopen(req, timeout=30) as resp:
                                size = int(resp.headers.get('Content-Length', 0))
                                if size <= MAX_FILE_SIZE:
                                    with open(local_path, 'wb') as f:
                                        f.write(resp.read())
                                    try:
                                        os.chmod(local_path, 0o644)
                                    except: pass
                                    has_any_attachment = True
                        
                        elif att_type == "markdown":
                            md_dir = os.path.join(attach_base, "markdown")
                            os.makedirs(md_dir, exist_ok=True)
                            title = att_meta.get("title", f"markdown_{att_idx}")
                            # Sanitize filename
                            safe_title = "".join(c if c.isalnum() or c in " _-" else "_" for c in title).strip()
                            if not safe_title:
                                safe_title = f"markdown_{att_idx}"
                            local_path = os.path.join(md_dir, f"{safe_title}.md")
                            with open(local_path, 'w', encoding='utf-8') as f:
                                f.write(att_value)
                            try:
                                os.chmod(local_path, 0o644)
                            except: pass
                            has_any_attachment = True
                        
                        elif att_type == "url":
                            url_dir = os.path.join(attach_base, "urls")
                            os.makedirs(url_dir, exist_ok=True)
                            # Name from meta.description > meta.title > domain
                            url_name = att_meta.get("description") or att_meta.get("title")
                            if not url_name:
                                try:
                                    url_name = urlparse(att_value).netloc.replace("www.", "")
                                except:
                                    url_name = f"url_{att_idx}"
                            safe_name = "".join(c if c.isalnum() or c in " _-" else "_" for c in url_name).strip()
                            if not safe_name:
                                safe_name = f"url_{att_idx}"
                            local_path = os.path.join(url_dir, f"{safe_name}.url")
                            with open(local_path, 'w', encoding='utf-8') as f:
                                f.write(att_value + "\n")
                                if att_meta.get("description"):
                                    f.write(f"description: {att_meta['description']}\n")
                                if att_meta.get("title"):
                                    f.write(f"title: {att_meta['title']}\n")
                                if att_meta.get("image"):
                                    f.write(f"image: {att_meta['image']}\n")
                            try:
                                os.chmod(local_path, 0o644)
                            except: pass
                            has_any_attachment = True
                    
                    except Exception as e:
                        console.print(f"[red]Attachment download failed ({att_type}) for {msg_id}: {e}[/red]")
            
            # Note: audioUrl/translatedAudioUrl are voice-mode transcription audio, NOT user attachments.
            # The agent already has the text — no need to download audio files.
            
            # Update DB with attachment metadata
            if has_any_attachment:
                c.execute("UPDATE messages SET has_attachments = 1, attachment_path = ? WHERE message_id = ?",
                          (attach_base, msg_id))
            
        conn.commit()
        conn.close()
        
        if inserted > 0:
            console.print(f"Persisted {inserted} new message(s) to SQLite.")
            
        return True
    except Exception as e:
        console.print(f"[bold red]Database Error:[/bold red] {str(e)}")
        return False

def upload_media(token, sub_account_id, recipient_id, file_path):
    """Upload a media file to VersaVoice via /attachments/upload. Returns {downloadUrl, meta} or None."""
    if not os.path.isfile(file_path):
        console.print(f"[red]File not found: {file_path}[/red]")
        return None
    
    file_name = os.path.basename(file_path)
    mime_type = mimetypes.guess_type(file_path)[0] or "application/octet-stream"
    
    with open(file_path, 'rb') as f:
        data_b64 = base64.b64encode(f.read()).decode('ascii')
    
    payload = {
        "subAccountId": sub_account_id,
        "recipientId": recipient_id,
        "fileName": file_name,
        "mimeType": mime_type,
        "data": data_b64
    }
    
    response = api_request("/attachments/upload", token, method="POST", body=payload)
    if not response or not response.get("downloadUrl"):
        err = response.get("message", str(response)) if response else "Upload failed"
        console.print(f"[red]Media upload failed ({file_name}): {err}[/red]")
        return None
    
    return {
        "downloadUrl": response["downloadUrl"],
        "meta": response.get("meta", {"fileName": file_name, "mimeType": mime_type})
    }

def build_attachments(token, sub_account_id, recipient_id, media_paths=None, markdown_paths=None, urls=None):
    """Build a VV attachments array from local files and URLs.
    
    - media_paths: list of file paths → uploaded via /attachments/upload
    - markdown_paths: list of .md file paths → read content inline
    - urls: list of URL strings → passed directly
    
    Returns list of attachment dicts or empty list.
    """
    attachments = []
    
    # Media: upload each file, get download URL
    for path in (media_paths or []):
        result = upload_media(token, sub_account_id, recipient_id, path)
        if result:
            meta = result["meta"]
            attachments.append({
                "type": "media",
                "value": result["downloadUrl"],
                "meta": {
                    "fileName": meta.get("fileName", os.path.basename(path)),
                    "mimeType": meta.get("mimeType", "application/octet-stream"),
                    "sizeBytes": meta.get("sizeBytes", os.path.getsize(path))
                }
            })
    
    # Markdown: read file content inline
    for path in (markdown_paths or []):
        if not os.path.isfile(path):
            console.print(f"[red]Markdown file not found: {path}[/red]")
            continue
        with open(path, 'r', encoding='utf-8') as f:
            content = f.read()
        title = os.path.splitext(os.path.basename(path))[0]
        size_bytes = len(content.encode('utf-8'))
        attachments.append({
            "type": "markdown",
            "value": content,
            "meta": {"title": title, "sizeBytes": size_bytes}
        })
    
    # URLs: pass directly
    for url in (urls or []):
        attachments.append({
            "type": "url",
            "value": url,
            "meta": None
        })
    
    if len(attachments) > 10:
        console.print(f"[yellow]Warning: {len(attachments)} attachments exceeds max 10. Truncating.[/yellow]")
        attachments = attachments[:10]
    
    return attachments

def send_message(token, sub_account_id, recipient_id, text, mode, messages_db, attachments=None):
    """Sends a message via VersaVoice API to the designated recipient and persists to local outbox log."""
    endpoint = "/messages/send"
    payload = {
        "subAccountId": sub_account_id,
        "recipientId": recipient_id,
        "text": text,
        "mode": mode
    }
    if attachments:
        payload["attachments"] = attachments
    
    response = api_request(endpoint, token, method="POST", body=payload)
    if not response or not response.get("success"):
        err = response.get("message", str(response)) if response else "Network Error"
        console.print(f"[red]Failed to send message: {err}[/red]")
        return False
        
    data = response.get("data", {})
    # API may nest messageId at different levels — check all possibilities
    msg_id = (
        data.get("messageId")
        or response.get("messageId")
        or data.get("id")
        or response.get("id")
        or "local_out_" + os.urandom(4).hex()
    )
    
    # Save the outbound message text in SQLite so the Agent can see its own sent logs
    try:
        conn = sqlite3.connect(messages_db, timeout=5)
        c = conn.cursor()
        cycle_id = os.environ.get("VERSA_CYCLE_ID") or None
        has_attach = 1 if attachments else 0
        c.execute('''INSERT INTO messages 
            (message_id, direction, status, from_user_id, to_user_id, text, original_text, mode, has_attachments, cycle_id, created_at)
            VALUES (?, 'sent', 'processed', ?, ?, ?, ?, ?, ?, ?, datetime('now'))''',
            (msg_id, sub_account_id, recipient_id, text, text, mode, has_attach, cycle_id)
        )
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        console.print(f"[red]DB Error persisting sent message:[/red] {str(e)}")
        return False

def mark_message_processed(msg_id, messages_db):
    """Updates the local SQLite message instance to processed status so the Agent ignores it on subsequent wakes."""
    try:
        conn = sqlite3.connect(messages_db, timeout=5)
        c = conn.cursor()
        c.execute("UPDATE messages SET status='processed' WHERE message_id=?", (msg_id,))
        rows = c.rowcount
        conn.commit()
        conn.close()
        return rows > 0
    except Exception as e:
        console.print(f"[red]DB Error marking message as processed:[/red] {str(e)}")
        return False

