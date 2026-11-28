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
JSON_KEYS = ("CONTENT", "EXTRACT", "STEP_FAILED", "STEPS_DONE", "SESSION_RESET", "SESSIONS")
PLAIN_KEYS = ("TITLE", "URL", "SCREENSHOT", "FILLED", "SESSION", "CLEARED")

# A named session (state_web_access.md §1.4): the name is a folder name in the
# agent's own home, so it is kept to what a folder name can safely be.
SESSION_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
MAX_STEPS = 20
STEP_FIELDS = {
    "goto": ("url",),
    "fill": ("selector", "value"),
    "click": ("selector",),
    "press": ("selector", "key"),
    "select": ("selector", "value"),
    "check": ("selector",),
    "uncheck": ("selector",),
    "wait_for": ("selector",),
}

_PRELUDE = '''
import sys, os, time, json, glob
sys.path.insert(0, "/usr/local/lib/versa-agi/venv/lib/python3/dist-packages")
for _p in glob.glob("/usr/local/lib/versa-agi/venv/lib/python3.*/site-packages"):
    sys.path.insert(0, _p)
from playwright.sync_api import sync_playwright

USER_AGENT = __USER_AGENT__
TIMEOUT_MS = __TIMEOUT_MS__
SETTLE_MS = __SETTLE_MS__
SESSION = __SESSION__
FALLBACK_USER_AGENT = __FALLBACK_USER_AGENT__
SESSION_ROOT = __SESSION_ROOT__


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


def session_folder():
    """This agent's folder for the named session, or "" when the command has none."""
    if not SESSION:
        return ""
    import pwd
    # The account's own home, not $HOME: the script is started for the agent by another account.
    root = SESSION_ROOT or os.path.join(pwd.getpwuid(os.getuid()).pw_dir,
                                        ".cache", "versa-agi", "browser-sessions")
    folder = os.path.join(root, SESSION)
    os.makedirs(folder, mode=0o700, exist_ok=True)
    for path in (root, folder):          # cookies are credentials: the owner only
        try:
            os.chmod(path, 0o700)
        except OSError:
            pass
    return folder


def _write_private(path, text):
    tmp = path + ".new"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(text)
    os.replace(tmp, path)


def open_page(p, url):
    """A page on ``url``. With a session, the cookies and site storage the last
    command of that session ended with are loaded first (state_web_access.md §1.4)."""
    browser = p.chromium.launch(headless=True)
    folder = session_folder()
    agent = USER_AGENT
    agent_file = os.path.join(folder, "user_agent") if folder else ""
    if not agent and agent_file and os.path.isfile(agent_file):
        with open(agent_file, encoding="utf-8") as handle:
            agent = handle.read().strip()
    if not agent and folder:
        agent = FALLBACK_USER_AGENT          # [browser] user_agent, for a session's first use
    if not agent:
        major = (browser.version or "").split(".")[0] or "120"
        agent = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
                 "Chrome/" + major + ".0.0.0 Safari/537.36")
    if not folder:
        page = browser.new_page(user_agent=agent)
    else:
        # A session keeps the user agent it started with: a sign-in whose
        # browser changes between visits is the kind sites end.
        if not os.path.isfile(agent_file):
            _write_private(agent_file, agent)
        state = os.path.join(folder, "state.json")
        options = {"user_agent": agent}
        if os.path.isfile(state):
            options["storage_state"] = state
        try:
            context = browser.new_context(**options)
        except Exception:
            # A state file that cannot be read must not lock the session out.
            context = browser.new_context(user_agent=agent)
            emit("SESSION_RESET", True)
        page = context.new_page()
    page.goto(url, timeout=TIMEOUT_MS)
    settle(page)
    return browser, page


def finish(browser, page):
    """Close the browser. With a session, first keep what the page ended with:
    every cookie (also the ones a browser drops when it closes) and site storage."""
    folder = session_folder()
    if folder:
        tmp = os.path.join(folder, "state.json.new")
        page.context.storage_state(path=tmp)
        os.chmod(tmp, 0o600)
        os.replace(tmp, os.path.join(folder, "state.json"))
        emit("SESSION", SESSION)
    browser.close()


def run_steps(page, steps):
    """Do the steps in order on one page. Stops at the first that fails and says which."""
    done = 0
    for step in steps:
        kind = step["do"]
        selector = step.get("selector", "")
        try:
            if kind == "goto":
                page.goto(step["url"], timeout=TIMEOUT_MS)
                settle(page)
            elif kind == "fill":
                page.locator(selector).fill(step.get("value", ""), timeout=TIMEOUT_MS)
            elif kind == "click":
                page.locator(selector).click(timeout=TIMEOUT_MS)
                settle(page)
            elif kind == "press":
                page.locator(selector).press(step["key"], timeout=TIMEOUT_MS)
                settle(page)
            elif kind == "select":
                page.locator(selector).select_option(step.get("value", ""), timeout=TIMEOUT_MS)
            elif kind == "check":
                page.locator(selector).check(timeout=TIMEOUT_MS)
            elif kind == "uncheck":
                page.locator(selector).uncheck(timeout=TIMEOUT_MS)
            elif kind == "wait_for":
                page.locator(selector).first.wait_for(timeout=TIMEOUT_MS)
        except Exception as exc:
            emit("STEP_FAILED", {"step": done + 1, "do": kind,
                                 "error": str(exc).splitlines()[0][:300]})
            return False
        done += 1
        emit("STEPS_DONE", done)
    return True


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


def session_error(name) -> str:
    """Empty when ``name`` can be a session name (blank = no session), else a message."""
    text = str(name or "")
    if not text or SESSION_NAME_RE.match(text):
        return ""
    return ("A session name is 1 to 40 characters: lower-case letters, digits, "
            "'-' and '_', starting with a letter or a digit.")


def parse_steps(text):
    """``--steps`` JSON → (steps, error). One of the two is empty.

    A list of 1 to MAX_STEPS objects, each ``{"do": <action>, …}`` with the
    fields of STEP_FIELDS as text. ``goto`` takes http:// and https:// only.
    """
    try:
        steps = json.loads(text or "")
    except ValueError as exc:
        return [], f"--steps is not valid JSON: {exc}"
    if not isinstance(steps, list) or not steps:
        return [], "--steps must be a JSON list with at least one step."
    if len(steps) > MAX_STEPS:
        return [], f"--steps takes at most {MAX_STEPS} steps (got {len(steps)})."
    clean = []
    for number, step in enumerate(steps, 1):
        if not isinstance(step, dict):
            return [], f"Step {number} must be an object like {{\"do\": \"click\", \"selector\": \"…\"}}."
        kind = step.get("do")
        if kind not in STEP_FIELDS:
            return [], f"Step {number}: \"do\" must be one of {', '.join(sorted(STEP_FIELDS))}."
        entry = {"do": kind}
        for field in STEP_FIELDS[kind]:
            value = step.get(field)
            if not isinstance(value, str) or (field != "value" and not value.strip()):
                return [], f"Step {number} ({kind}) needs \"{field}\" as text."
            entry[field] = value
        if kind == "goto" and not entry["url"].startswith(("http://", "https://")):
            return [], f"Step {number}: only http:// and https:// URLs are allowed."
        clean.append(entry)
    return clean, ""


def run_body(url: str, steps: list, *, screenshot_dir: str = "") -> str:
    """Command body for ``browser run``: open, do the steps, report the page."""
    lines = [
        "with sync_playwright() as p:",
        f"    browser, page = open_page(p, {url!r})",
        f"    ok = run_steps(page, json.loads({json.dumps(steps)!r}))",
        '    emit("TITLE", page.title())',
        '    emit("URL", page.url)',
        '    emit("CONTENT", page.locator("body").inner_text(timeout=TIMEOUT_MS))',
    ]
    if screenshot_dir:
        lines += [
            f"    ss_dir = {screenshot_dir!r}",
            "    os.makedirs(ss_dir, exist_ok=True)",
            '    ss_path = os.path.join(ss_dir, f"run_{int(time.time())}.png")',
            "    page.screenshot(path=ss_path, full_page=True)",
            '    emit("SCREENSHOT", ss_path)',
        ]
    lines += [
        "    if ok:",
        "        finish(browser, page)",
        "    else:",
        "        browser.close()   # a run that failed part-way does not change the session",
    ]
    return "\n".join(lines) + "\n"


# Listing and clearing sessions needs no browser: plain scripts, run as the agent.
SESSIONS_LIST_SCRIPT = '''
import json, os, pwd, time
root = os.path.join(pwd.getpwuid(os.getuid()).pw_dir, ".cache", "versa-agi", "browser-sessions")
found = []
if os.path.isdir(root):
    for name in sorted(os.listdir(root)):
        state = os.path.join(root, name, "state.json")
        if os.path.isfile(state):
            found.append({"name": name, "last_used": time.strftime(
                "%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(state)))})
print("SESSIONS:" + json.dumps(found))
'''

SESSION_CLEAR_SCRIPT = '''
import json, os, pwd, shutil
root = os.path.join(pwd.getpwuid(os.getuid()).pw_dir, ".cache", "versa-agi", "browser-sessions")
name = __NAME__
folder = os.path.join(root, name)
existed = os.path.isdir(folder)
if existed:
    shutil.rmtree(folder)
print("CLEARED:" + json.dumps("yes" if existed else "no"))
'''


def session_clear_script(name: str) -> str:
    return SESSION_CLEAR_SCRIPT.replace("__NAME__", repr(str(name)))


def build_script(body: str, *, user_agent: str = "", timeout_ms: int = 30000,
                 settle_wait_ms: int = DEFAULT_SETTLE_SECONDS * 1000, session: str = "",
                 fallback_user_agent: str = "", session_root: str = "") -> str:
    """Prelude + command body. ``body`` uses ``open_page``, ``settle``, ``emit``, ``finish``.

    ``fallback_user_agent`` is what a session starts with when the command names
    none. ``session_root`` is for tests: sessions live in the agent's own home.
    """
    prelude = (
        _PRELUDE
        .replace("__USER_AGENT__", repr(str(user_agent or "")))
        .replace("__TIMEOUT_MS__", str(int(timeout_ms)))
        .replace("__SETTLE_MS__", str(int(settle_wait_ms)))
        .replace("__SESSION__", repr(str(session or "")))
        .replace("__FALLBACK_USER_AGENT__", repr(str(fallback_user_agent or "")))
        .replace("__SESSION_ROOT__", repr(str(session_root or "")))
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
