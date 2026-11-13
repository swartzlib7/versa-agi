# Skill: Browser Automation — Headless Chromium

> **Trigger**: Use this skill when a page needs a real browser: it builds its content with scripts, or you need to click, fill a form, or capture a screenshot. To simply read a page (documentation, an article, a reference), use `agictl search read "<url>"` first: it is faster and starts no browser (see the `web_research` skill). All operations here use a sandboxed headless Chromium browser running as your agent user.

> **Harness tools:** Examples use shell form (`agictl group …`). In a work cycle, call the matching tool (`agictl_task`, `agictl_cycle`, …) and pass only the part **after** `agictl` as the `command` argument. Never prefix `agictl` in the argument. Full map: **cli_reference_agent.md** (*Harness tool invocation*).

## Prerequisites

- System-wide: `setup.ini [browser] enabled=true`
- Per-agent: `browser_enabled=1` in agents.db (set via agitop Agent Settings)
- Chromium installed for your OS user (`playwright install chromium`)

If either prerequisite is unmet, the `agictl_browser` harness tool will not be available and CLI commands will return an access-denied error.

## Commands

```bash
agictl browser goto "<url>"                           # Load page, return its text (first 8000 characters)
agictl browser goto "<url>" --offset <next_offset>    # Read on from where the last reply stopped
agictl browser goto "<url>" --max-chars 20000         # Return more per call (limit 20000)
agictl browser goto "<url>" --selector "<css>"        # Text of one part of the page
agictl browser goto "<url>" --screenshot              # Load page + save screenshot
agictl browser click "<url>" "<selector>"             # Navigate then click an element
agictl browser fill "<url>" "<selector>" "<value>"    # Navigate then fill a form field
agictl browser screenshot "<url>"                     # Capture visible viewport
agictl browser screenshot "<url>" --full-page         # Capture full scrollable page
agictl browser extract "<url>"                        # Extract all text content
agictl browser extract "<url>" --selector "<css>"     # Extract text from specific elements
agictl browser extract "<url>" --selector "<css>" --attribute "<attr>"  # Extract attribute values
```

Every command accepts `--user-agent "<text>"` for that call. Without it the browser presents itself as desktop Chrome. Set one when a site asks automated clients to identify themselves, or serves different content to a different browser.

Each command waits for the page's scripts to finish (network quiet, then text unchanged for one second, up to the system settle time) before it reads.

## Reading a long page

`goto` returns `content_chars` (the full length). When more text follows, the reply has `"truncated": true` and `next_offset`. Call `goto` again with `--offset <next_offset>` to read the next part. Use `--selector` to read only the part you need.

## Security Guardrails

1. **URL Validation** — Only `http://` and `https://` URLs are allowed. `file://`, `javascript:`, `data:`, and blank URLs are blocked.
2. **OS-Level Isolation** — All browser processes run as your agent OS user (via `sudo -u`), not as root or watchdog. Each agent's Chromium cache is isolated to `~/.cache/ms-playwright/`.
3. **Timeout Enforcement** — Page loads are capped at the system-wide `[browser] timeout` value (default: 30s). The entire CLI operation has an outer 120-second hard timeout.
4. **No Privilege Escalation** — Browser commands cannot access local filesystem paths or execute JavaScript that escapes the sandbox.

## Screenshot Storage

Screenshots are saved to your workspace:
- **Path**: `workspace/screenshots/browser_<timestamp>.png`
- **Automatic**: `--screenshot` flag on `goto` saves a screenshot alongside the text extraction
- **Explicit**: `browser screenshot` command always captures the viewport

## Usage Patterns

### Research & Data Gathering
```bash
# Extract article text
agictl browser extract "https://docs.example.com/api/v2" --selector "article"

# Get all links from a page
agictl browser extract "https://example.com/resources" --selector "a" --attribute "href"

# Screenshot a dashboard for reporting
agictl browser screenshot "https://status.example.com" --full-page
```

### Form Interaction
```bash
# Fill and submit a search form
agictl browser fill "https://example.com/search" "#query" "versa agi documentation"
agictl browser click "https://example.com/search" "button[type=submit]"
```

### Page Verification
```bash
# Check if a deployment is live
agictl browser goto "https://app.example.com/health" --screenshot
```

## Return Format

All commands return JSON:

```json
{
  "success": true,
  "url": "https://example.com",
  "title": "Example Domain",
  "content": "...",
  "content_chars": 23110,
  "offset": 0,
  "truncated": true,
  "next_offset": 8000,
  "screenshot": "/home/agi-name/workspace/screenshots/browser_20260607_120000.png"
}
```

On failure:
```json
{
  "success": false,
  "error": "Timeout waiting for page load"
}
```

## Limitations

- No persistent browser sessions — each command spawns a fresh Chromium instance
- No cookie/session management across commands
- A page that keeps loading past the settle time is read as it is at that moment
- A user agent does not pass a CAPTCHA or a login wall. If a page shows one, report it and use another source
- Cannot interact with browser devtools or modify browser settings

## COA Delegation

COA can enable/disable browser access for sub-agents:

```bash
sudo agictl browser enable <agent_name>    # Set browser_enabled=1, install Chromium
sudo agictl browser disable <agent_name>   # Set browser_enabled=0, cleanup cache
```

These commands are **COA-only** (protected agent guard enforced).
