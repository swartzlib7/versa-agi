# Skill: Email Access — Technical reference

> **Trigger**: Credential setup, IMAP/SMTP connection issues, provider settings, library API details, or troubleshooting email access.
> **Scope**: All agents (`all`). Only when FEATURE AVAILABILITY does **not** say Organization is OFF — system credentials are Organization records (`agictl organization credential`).

> **Harness tools:** Examples use shell form (`agictl group …`). In a work cycle, call the matching tool and pass only the part **after** `agictl` as the `command` argument. Never prefix `agictl` in the argument. Full map: **cli_reference_agent.md** (*Harness tool invocation*).

## Purpose

Technical reference for Versa AGi email access: `email_client.py` architecture, credential schema, IMAP/SMTP patterns, security, providers, and troubleshooting.

**Feature gate:** If spawn context says Organization is OFF, do not use email access or Organization credential commands.

Operations / wake workflows: **email_admin.md**.

## Architecture

Credential config → `email_client.py` (AGi-Tools) → shell scripts / agent Python.

| Piece | Role |
|-------|------|
| `email_client.py` | Library (IMAP read + SMTP send + folders + closed-mode routing) |
| `check_email.sh` / `send_email.sh` | Thin shell entry points in AGi-Tools |
| System credential | Preferred — `agictl organization credential` + `credential_id=` |
| Local credentials file | Optional project-local JSON (mode `600`) — e.g. under project `email-access` |

**Protocols:** IMAP (read) + SMTP (send). **POP3 is not supported.**

### Dependencies

- Python 3.12+
- `imap-tools` (pip)
- stdlib: `smtplib`, `email`, `ssl`, `json`

## Credential configuration

Two sources:

1. **System credential record** (preferred): `EmailClient(addr, credential_id=N)` via Organization credentials  
2. **Local file**: project credentials JSON with mode `600`

### Nested format (preferred)

```json
{
  "email_address": "user@example.com",
  "auth": {"type": "basic", "username": "...", "password": "..."},
  "imap": {"host": "...", "port": 993, "encryption": "ssl"},
  "smtp": {"host": "...", "port": 587, "encryption": "starttls"},
  "processing": {"mode": "closed", "handled_folder": "Handled"}
}
```

### Flat format (legacy, still accepted)

```json
{
  "email_address": "user@example.com",
  "imap_host": "...", "imap_port": 993, "imap_ssl": true,
  "smtp_host": "...", "smtp_port": 587, "smtp_tls": true,
  "username": "...", "password": "...", "processing_mode": "closed"
}
```

Project `email-access` may hold `CONFIGURATION_SCHEMA.md` for the full field list.

### Security

- Credential files mode `600`
- Never echo passwords in messages, task progress, or awareness
- IMAP SSL/TLS; SMTP STARTTLS (or SSL per provider)

## Library reference (`EmailClient`)

```python
from email_client import EmailClient
client = EmailClient("user@example.com", credential_id=1)
# or local file default in project credentials path
client = EmailClient("user@example.com")
```

**Read:** `list_folders`, `get_unread`, `get_recent`, `search_messages`, `get_message`, `get_attachments`  
**Send:** `send_email`, `reply_to`, `forward_email`  
**Manage:** `mark_read` / `mark_unread`, `move_message`, `delete_message`  
**Folders:** `create_folder`, `folder_exists`, `get_handled_folder`, `mark_handled`, `get_handled`  
**Closed mode:** `filter_by_mode`, `classify_message`, `route_message`, `process_closed_mode`, `load_blacklist`, `is_blacklisted`, `harvest_addresses`

Message dicts include: `uid`, `from`, `to`, `cc`, `subject`, `date`, truncated `text`/`html`, `flags`, `attachments`, `headers`.

### CLI entry (from AGi-Tools)

```bash
python3 email_client.py user@example.com unread --limit 10
python3 email_client.py user@example.com recent --limit 5
python3 email_client.py user@example.com folders
python3 email_client.py user@example.com send --to recipient@example.com --subject Test --body Hello
python3 email_client.py user@example.com search --from google --limit 5
```

## Provider settings (common)

| Provider | IMAP | SMTP | Notes |
|----------|------|------|-------|
| Gmail | imap.gmail.com:993 SSL | smtp.gmail.com:587 STARTTLS | App Password + 2SV; enable IMAP |
| Outlook / M365 | outlook.office365.com:993 SSL | smtp.office365.com:587 STARTTLS | |
| Yahoo | imap.mail.yahoo.com:993 SSL | smtp.mail.yahoo.com:587 STARTTLS | |

## Closed-mode config (`processing`)

| Field | Default | Description |
|-------|---------|-------------|
| `mode` | `closed` | `open` or `closed` |
| `whitelist` | `[]` | Allowed senders |
| `blacklist` / `blacklist_file` | — | Inline list or file path |
| `system_folder` | `System` | Auto-file target |
| `promotional_folder` | `Promotional` | Auto-file target |
| `blacklisted_folder` | `Blacklisted` | Auto-file target |
| `handled_folder` | `Handled` | After agent processing |
| `auto_move_handled` | `false` | Move after reply/forward |

Classification priority: blacklist → whitelist → system → promotional → unknown. Heuristics for system/promo senders and subjects live in the library (noreply, newsletter, mailchimp, etc.).

## Troubleshooting

- **AUTHENTICATIONFAILED** — use app password where required; check typos  
- **Timeout** — host/port/firewall  
- **Folder not found** — `create_folder` / `list_folders`  
- **SSL errors** — match encryption to port (ssl@993 vs starttls@587)
