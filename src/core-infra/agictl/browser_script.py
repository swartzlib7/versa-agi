"""Browser command helpers (state_web_access.md §1.1). Pure: no Playwright, no click.

The Playwright work runs in a separate process as the agent user. This module
builds that script and reads what it prints.
"""

from __future__ import annotations

import json
import re

DEFAULT_MAX_CHARS = 8000
MAX_CHARS_LIMIT = 20000
DEFAULT_SETTLE_SECONDS = 5
USER_AGENT_MAX_CHARS = 300

# Keys whose value is one JSON-encoded line, so page text keeps its newlines.
JSON_KEYS = ("CONTENT", "EXTRACT")
PLAIN_KEYS = ("TITLE", "URL", "SCREENSHOT", "FILLED")

_PRELUDE = '''
import sys, os, time, json, glob
sys.path.insert(0, "/usr/local/lib/versa-agi/venv/lib/python3/dist-packages")
for _p in glob.glob("/usr/local/lib/versa-agi/venv/lib/python3.*/site-packages"):
    sys.path.insert(0, _p)
from playwright.sync_api import sync_playwright

USER_AGENT = __USER_AGENT__
TIMEOUT_MS = __TIMEOUT_MS__
SETTLE_MS = __SETTLE_MS__


def settle(page):
    """Let scripts finish rendering: quiet network, then page text unchanged for
    one second. Bounded by SETTLE_MS; a page that never goes quiet is used as it is."""
    if SETTLE_MS <= 0:
        return
    deadline = time.time() + SETTLE_MS / 1000.0
    try:
        page.wait_for_load_state("networkidle", timeout=SETTLE_MS)
    except Exception:
        pass
    last, quiet_since = None, time.time()
    while time.time() < deadline:
        try:
            size = page.evaluate("document.body ? document.body.innerText.length : 0")
        except Exception:
            break
        now = time.time()
        if size != last:
            last, quiet_since = size, now
        elif now - quiet_since >= 1.0:
            break
        page.wait_for_timeout(200)


def open_page(p, url):
    browser = p.chromium.launch(headless=True)
    agent = USER_AGENT
    if not agent:
        major = (browser.version or "").split(".")[0] or "120"
        agent = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
                 "Chrome/" + major + ".0.0.0 Safari/537.36")
    page = browser.new_page(user_agent=agent)
    page.goto(url, timeout=TIMEOUT_MS)
    settle(page)
    return browser, page


def emit(key, value):
    """One line per value; text goes out JSON-encoded so its newlines survive."""
    print(key + ":" + json.dumps(value))

'''


def user_agent_error(user_agent) -> str:
    """Empty when ``user_agent`` is usable (blank = default), else a message."""
    text = str(user_agent or "")
    if "\n" in text or "\r" in text:
        return "User agent must be a single line."
    if len(text) > USER_AGENT_MAX_CHARS:
        return f"User agent must be at most {USER_AGENT_MAX_CHARS} characters (got {len(text)})."
    return ""


def settle_ms(settle_seconds, timeout_ms: int) -> int:
    """Settle wait in ms: 0 = off, never longer than the page timeout."""
    try:
        seconds = float(str(settle_seconds).strip())
    except (TypeError, ValueError):
        seconds = DEFAULT_SETTLE_SECONDS
    return int(max(0.0, min(seconds * 1000.0, float(timeout_ms))))


def build_script(body: str, *, user_agent: str = "", timeout_ms: int = 30000,
                 settle_wait_ms: int = DEFAULT_SETTLE_SECONDS * 1000) -> str:
    """Prelude + command body. ``body`` uses ``open_page``, ``settle``, ``emit``."""
    prelude = (
        _PRELUDE
        .replace("__USER_AGENT__", repr(str(user_agent or "")))
        .replace("__TIMEOUT_MS__", str(int(timeout_ms)))
        .replace("__SETTLE_MS__", str(int(settle_wait_ms)))
    )
    return prelude + body


def parse_output(output: str) -> dict:
    """``KEY:value`` lines from the script → dict with lower-case keys.

    The last occurrence of a key wins. A ``CONTENT`` line that is not JSON
    (an older script) is kept as plain text.
    """
    found: dict = {}
    for line in (output or "").splitlines():
        key, sep, value = line.partition(":")
        if not sep:
            continue
        if key in JSON_KEYS:
            try:
                found[key.lower()] = json.loads(value)
            except ValueError:
                found[key.lower()] = value
        elif key in PLAIN_KEYS:
            if value[:1] == '"':
                try:
                    value = json.loads(value)
                except ValueError:
                    pass
            found[key.lower()] = value
    return found


def tidy_text(text) -> str:
    """Page text with trailing spaces dropped and runs of blank lines reduced to one."""
    lines = [line.rstrip() for line in str(text or "").replace("\r\n", "\n").split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def clamp_max_chars(max_chars) -> int:
    try:
        value = int(max_chars)
    except (TypeError, ValueError):
        return DEFAULT_MAX_CHARS
    return max(1, min(MAX_CHARS_LIMIT, value))


def page_slice(text: str, offset: int = 0, max_chars: int = DEFAULT_MAX_CHARS) -> dict:
    """One page of ``text`` plus what the agent needs to ask for the next one."""
    total = len(text)
    start = max(0, min(int(offset or 0), total))
    size = clamp_max_chars(max_chars)
    end = min(total, start + size)
    result = {"content": text[start:end], "content_chars": total, "offset": start}
    if end < total:
        result["truncated"] = True
        result["next_offset"] = end
    return result
