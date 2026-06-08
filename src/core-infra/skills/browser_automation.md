# Skill: Browser Automation — Headless Chromium

> **Trigger**: Use this skill when you need to navigate web pages, extract content from URLs, fill forms, click elements, or capture screenshots. All operations use a sandboxed headless Chromium browser running as your agent user.

## Prerequisites

- System-wide: `setup.ini [browser] enabled=true`
- Per-agent: `browser_enabled=1` in agents.db (set via agitop Agent Settings)
- Chromium installed for your OS user (`playwright install chromium`)

If either prerequisite is unmet, the `agictl_browser` harness tool will not be available and CLI commands will return an access-denied error.

## Commands

```bash
agictl browser goto "<url>"                           # Load page, return text content
agictl browser goto "<url>" --screenshot              # Load page + save screenshot
agictl browser click "<url>" "<selector>"             # Navigate then click an element
agictl browser fill "<url>" "<selector>" "<value>"    # Navigate then fill a form field
agictl browser screenshot "<url>"                     # Capture visible viewport
agictl browser screenshot "<url>" --full-page         # Capture full scrollable page
agictl browser extract "<url>"                        # Extract all text content
agictl browser extract "<url>" --selector "<css>"     # Extract text from specific elements
agictl browser extract "<url>" --selector "<css>" --attribute "<attr>"  # Extract attribute values
```

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
- JavaScript-heavy SPAs may require `goto` before `extract` to let content render
- Maximum content extraction is truncated to prevent token overflow
- Cannot interact with browser devtools or modify browser settings

## COA Delegation

COA can enable/disable browser access for sub-agents:

```bash
sudo agictl browser enable <agent_name>    # Set browser_enabled=1, install Chromium
sudo agictl browser disable <agent_name>   # Set browser_enabled=0, cleanup cache
```

These commands are **COA-only** (protected agent guard enforced).
