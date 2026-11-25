# Skill: Email Access — Admin (operations)

> **Trigger**: Check, send, search, reply, forward, or file email; run closed-mode inbox processing; harvest unknown senders for PU whitelist review.
> **Scope**: All agents (`all`). Only when FEATURE AVAILABILITY does **not** say Organization is OFF — email credentials live in the Organization domain.

> **Harness tools:** Examples use shell form (`agictl group …` / shell scripts). In a work cycle, call the matching tool (`agictl_execute`, `agictl_organization`, …) and pass only the part **after** `agictl` as the `command` argument when using agictl. Never prefix `agictl` in the argument. Full map: **cli_reference_agent.md** (*Harness tool invocation*).

## Purpose

Operational guide for the Versa AGi email access system — unread checks, send/reply/forward, folders, closed-mode routing, and wake-cycle workflows.

**Feature gate:** If spawn context says Organization is OFF, do not use email access (`email_admin` / `email_technical`) or Organization credentials.

Technical details (library, credentials, providers, troubleshooting): **email_technical.md**.

Library and scripts live in the shared **AGi-Tools** project (`email_client.py`, `check_email.sh`, `send_email.sh`). Add AGi-Tools to `sys.path` / cwd from your workspace — do not hard-code another agent’s home path.

## Quick Start

### Check unread

```bash
./AGi-Tools/check_email.sh user@example.com --unread --limit 10
```

```python
from email_client import EmailClient
client = EmailClient("user@example.com", credential_id=1)
unread = client.get_unread(limit=10)
for m in unread:
    print(m["from"], m["subject"], m["date"])
```

### Send

```bash
./AGi-Tools/send_email.sh user@example.com --to recipient@example.com --subject Hello --body Test
```

```python
client = EmailClient("user@example.com", credential_id=1)
client.send_email(to="recipient@example.com", subject="Hello", body="Test")
```

## Operations

```python
client.get_recent(folder="INBOX", limit=20)
client.search_messages({"from_": "google"}, limit=10)
client.search_messages({"subject": "invoice"}, limit=10)
client.search_messages({"body": "payment"}, limit=10)
client.get_message(uid=123, folder="INBOX")
client.get_attachments(uid=123, download_dir="workspace/tmp/attachments")
client.reply_to(uid=123, body="Thanks!", reply_all=False)
client.forward_email(uid=123, to="colleague@example.com", comment="FYI")
client.mark_read(uid=123)
client.mark_unread(uid=123)
client.move_message(uid=123, dest_folder="Archive")
client.delete_message(uid=123)
client.list_folders()
```

## Folder system

| Folder | Purpose |
|--------|---------|
| **INBOX** | Default. Closed mode: whitelist + unknown stay here until processed or directed |
| **Handled** | Processed by the agent (replied, filed, or task created) |
| **System** | Automated notifications (noreply, security, cloud alerts). Auto-filed in closed mode |
| **Promotional** | Marketing / newsletters. Auto-filed in closed mode |
| **Blacklisted** | Senders on the blacklist. Auto-filed in closed mode |

```python
client.folder_exists("Handled")
client.create_folder("Handled")
client.get_handled_folder()       # "Handled"
client.mark_handled(uid=123)
client.get_handled(limit=20)
```

Set `processing.auto_move_handled` to `true` in the credential config to move messages to Handled after reply/forward.

## Processing mode

Set in credential config:

- **open** — process mail from any address
- **closed** (default) — system/promotional/blacklist auto-filed; whitelist + unknown stay in INBOX

```python
messages = client.get_unread(limit=20)
filtered = client.filter_by_mode(messages, whitelist=["known@example.com"])
```

### Closed mode classification priority

1. **Blacklist** → Blacklisted folder  
2. **Whitelist** → stay INBOX  
3. **System** (noreply / notifications / alert patterns) → System  
4. **Promotional** (newsletter / promo / unsubscribe patterns) → Promotional  
5. **Unknown** → stay INBOX for PU whitelist review  

**Reply-ability rule:** reply-able humans/orgs → whitelist consideration; no-reply automation → System; promo content → Promotional even if reply-able.

```python
messages = client.get_unread(limit=50)
results = client.process_closed_mode(messages, dry_run=False)
# total, whitelist, system, promotional, blacklisted, unknown, routed, harvested_addresses
classification = client.classify_message(msg)
result = client.route_message(msg, dry_run=False)
```

## Blacklist

Plain-text file (one address per line, `#` comments). Config: `processing.blacklist_file` (often `AGi-Tools/blacklist.txt`).

```python
client.load_blacklist()
client.is_blacklisted("spam@example.com")
```

## Address harvesting

Unknown senders (not whitelist/blacklist/system/promo) stay in INBOX. After `process_closed_mode`, use `harvested_addresses` → craft a list for the PU → add approved addresses to `processing.whitelist`. Never reply to unknown senders without PU approval.

## Wake-cycle workflow (closed mode)

Default monitoring: **agent wake** (not script tasks) **3× daily** (e.g. 9:00 / 14:00 / 17:00 local) unless PU sets another cadence.

1. `get_unread` → `process_closed_mode`  
2. Whitelisted: batch by sender; map to project/game; create task(s) if real work  
3. Reply when appropriate; `mark_handled`  
4. Harvest unknowns → message PU for whitelist  
5. `cycle end`  

**Batching:** multiple unread from one sender → one consolidated task when related; one reply covering all points.

## Anti-spam

Whitelist first, blacklist second, per-sender batching, no task if no project/game mapping, escalate odd patterns to PU, never auto-reply to unknowns.

## Notes

- Disconnect is handled by the library (`try/finally` / context manager)
- Never log credentials in messages, task progress, or awareness
- IMAP + SMTP only — no POP3
