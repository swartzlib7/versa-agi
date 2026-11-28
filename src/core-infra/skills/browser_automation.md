# Skill: Browser Automation — a real browser for pages that need one: click, fill in and send a form, sign in and stay signed in, screenshots

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
agictl browser run "<url>" --steps '<json list>'      # Several steps on one page: fill a form and send it, sign in
agictl browser fill "<url>" "<selector>" "<value>"    # Fill one field. Nothing is submitted: the page closes with the command
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

Each command opens the page, acts and closes it. A field filled by one command is gone in the next, so a form is filled **and sent in one command**, with `browser run`:

```bash
agictl browser run "https://example.com/search" --steps '[{"do":"fill","selector":"#query","value":"versa agi documentation"},{"do":"click","selector":"button[type=submit]"}]'
```

The reply is the page as it is after the last step (`title`, `current_url`, `content`, with the same paging as `goto`).

| Step (`"do"`) | Fields | What it does |
|---|---|---|
| `fill` | `selector`, `value` | Types into a field (an empty value clears it) |
| `click` | `selector` | Clicks, then waits for the page to settle |
| `press` | `selector`, `key` | Presses a key in that element, for example `Enter` |
| `select` | `selector`, `value` | Chooses an option of a drop-down |
| `check` / `uncheck` | `selector` | Ticks or clears a box |
| `wait_for` | `selector` | Waits until that element is there |
| `goto` | `url` | Opens another page in the same browser |

- At most 20 steps, and the whole command has about 100 seconds. Split longer work into several `run` commands that share a session (below).
- If a step fails, the reply says which one (`failed_step`) and holds the page as it was at that point. Read it before you try again.

## Sessions: staying signed in between commands

Without a session every command starts a clean browser. Add `--session <name>` and the cookies and site storage the command ends with are kept under that name, and loaded by the next command that names it. Any browser command takes it.

```bash
# Sign in once
agictl browser run "https://app.example.com/login" --session example --steps '[{"do":"fill","selector":"#email","value":"<email>"},{"do":"fill","selector":"#password","value":"<password>"},{"do":"click","selector":"button[type=submit]"}]'
# Later commands, also in a later cycle, are still signed in
agictl browser goto "https://app.example.com/orders" --session example
agictl browser session list
agictl browser session clear example
```

- **One name per site and account** (`example`, `example-billing`). A name is lower-case letters, digits, `-` and `_`.
- **A session is yours alone.** It lives in your own home and no other agent can use it.
- **Sign in only with an account the Primary User gave you for that purpose.** Never ask a person for their password in a message, and never sign in to someone's personal account because you could.
- **A password in `--steps` is a secret.** Do not copy it into a task, memory, a message or a summary.
- **If the page shows the sign-in again,** the site ended the session: sign in again with `run`. A reply with `session_reset` means the saved session could not be read and was started again.
- **Clear a session** when the work that needed it is finished, or when the Primary User asks.
- A run that fails part-way leaves the session as it was before the command.

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

- Each command starts its own browser and closes it. Cookies and sign-ins carry over only with `--session`; a half-filled form never does (use `browser run`)
- A site that asks for a code sent to a phone or an app each time cannot be signed in to without the person
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
