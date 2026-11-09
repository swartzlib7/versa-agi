"""`agictl desktop` — COA observes and operates the Primary User's desktop (HeteroSphere).

`desktop look pu` reads the tree of every screen, each app window summed up; `--window <id>`
reads one window's controls. `desktop <step> pu [element]` takes one Perceptron step on the
primary screen, the same steps the Primary User takes: open, load, unload, activate, close,
expand, fullscreen, restore for an open interface, minimize for an open app, and back (Escape).
`desktop click pu <id>` and `desktop right-click pu <id>` press an app's control.
`desktop see pu [--screen N]` takes a picture of screens.
HeteroSphere asks the Primary User to allow looking, operating, and pictures separately
(pictures on each screen), unless a standing grant is active. Contract:
design/spec/state/state_computer_use.md §1, §2.4 and state_versa_desktop.md §1.8.
"""

from __future__ import annotations

import grp
import json
import os
import socket
import stat
import sys

import click

SOCKET_PATH = os.environ.get("HETEROSPHERE_SOCKET", "/tmp/heterosphere/observe.sock")
SOCKET_GROUP = "coa"
# HeteroSphere's alert closes after 30 s; leave room for the reply.
REPLY_TIMEOUT_S = 40
MAX_REPLY_BYTES = 4 * 1024 * 1024

STEPS = {
    "open": ("Move the computer into the sphere, so its Do and Have items appear; or name `user` to move User in.", "optional"),
    "load": ("Load a shown Do or Have item into the center. Nothing runs yet.", True),
    "unload": ("Take a loaded item back out of the center.", True),
    "activate": ("Activate what is loaded. A confirming item arms first; activate again to confirm.", False),
    "close": ("Move the computer (or User) back out of the sphere: Do and Have hide and the center clears.", False),
    "expand": ("Expand the open interface, if it allows that.", False),
    "fullscreen": ("Show the open interface full screen, if it allows that.", False),
    "restore": ("Bring the open interface back from full screen, or from expanded to its card.", False),
    "minimize": ("Minimize the open app: its box closes and the app keeps running.", False),
    "back": ("Go back one step, as Escape does: out of full screen, then close the interface, the load, or an opened part.", False),
    "press": ("Press one calculator key: 0-9, dot, plus, minus, times, divide, equals, clear, or back.", True),
    "clip-use": ("Make clipboard copy N the one every app pastes; it moves to the top as 1. "
                 "N is its number in the `clipboard` node.", True),
    "clip-remove": ("Remove clipboard copy N. Removing the current one makes the next one current.", True),
    "clip-clear": ("Empty the clipboard: every copy and what a paste would use.", False),
    "click": ("Click an app's control (an `atspi-…` id from your last look) at the centre of its box. "
              "The reply's tree is read 0.6 s later.", True),
    "right-click": ("Right-click an app's control (an `atspi-…` id from your last look). A menu that opens is a "
                    "`menu` node in the reply's tree; click one of its items to pick it.", True),
    "double-click": ("Double-click an app's control (an `atspi-…` id from your last look): open a file or a folder.",
                     True),
    "type": ("Click an app's field (an `atspi-…` id of role text) and type TEXT at its cursor, at most 2000 "
             "characters. Never a password field. Send `key pu ctrl+a` first to replace what is there.", "text"),
    "key": ("Press keys in the app that has the keys: enter, escape, tab, backspace, delete, up, down, left, "
            "right, home, end, pageup, pagedown, space, f1-f12, with ctrl+, alt+, shift+ (ctrl+c, ctrl+shift+n). "
            "Never Super or Ctrl+Alt+Backspace/Delete/F-keys.", "keys"),
}

ERROR_TEXT = {
    "not_running": "The Primary User's desktop (HeteroSphere) is not running.",
    "unsafe": "The HeteroSphere socket directory is not owned by the coa group or is open to others. Not connecting.",
    "rejected": "The Primary User said no.",
    "timeout": "The Primary User did not answer within 30 seconds.",
    "busy": "Another request is already waiting for the Primary User's answer.",
    "bad_request": "HeteroSphere did not accept the request.",
    "not_ready": "HeteroSphere is still starting.",
    "no_reply": "HeteroSphere closed the connection without a reply.",
    "locked": "The Primary User's desktop is locked.",
    "no_screen": "No such screen. `desktop look pu` lists them as screen-<n>.",
    "capture_failed": "HeteroSphere could not take the picture.",
    "no_window": "No such window on that screen. `desktop look pu` lists the windows.",
}


def _socket_dir_problem(path: str) -> str | None:
    directory = os.path.dirname(path)
    try:
        st = os.lstat(directory)
    except FileNotFoundError:
        return "not_running"
    if not stat.S_ISDIR(st.st_mode):
        return "unsafe"
    try:
        gid = grp.getgrnam(SOCKET_GROUP).gr_gid
    except KeyError:
        return "unsafe"
    if st.st_gid != gid or st.st_mode & 0o007:
        return "unsafe"
    if not os.path.exists(path):
        return "not_running"
    return None


def request_look(agent: str, display_name: str, path: str = SOCKET_PATH, window: str = "",
                 screen: int = 0) -> dict:
    """Send one look request and return HeteroSphere's reply (or a local error). With
    `window`, only that window's controls (and its app's open menu) on `screen`."""
    request = {"op": "look", "target": "pu", "agent": agent, "display_name": display_name}
    if window:
        request["window"] = window
        request["screen"] = screen
    return _request(request, path)


def request_act(agent: str, display_name: str, step: str, element: str = "",
                path: str = SOCKET_PATH, text: str = "", keys: str = "") -> dict:
    """Send one step and return HeteroSphere's reply (or a local error). `text` is what
    `type` types; `keys` what `key` presses."""
    request = {"op": "act", "target": "pu", "agent": agent, "display_name": display_name, "step": step}
    if element:
        request["id"] = element
    if text:
        request["text"] = text
    if keys:
        request["keys"] = keys
    return _request(request, path)


def request_see(agent: str, display_name: str, screens: list[int] | None = None,
                path: str = SOCKET_PATH) -> dict:
    """Ask for a picture of each screen (all when `screens` is empty). Each screen asks the
    Primary User on that screen unless it already may be seen."""
    request = {"op": "see", "target": "pu", "agent": agent, "display_name": display_name}
    if screens:
        request["screens"] = list(screens)
    return _request(request, path)


def _request(request: dict, path: str) -> dict:
    problem = _socket_dir_problem(path)
    if problem:
        return {"ok": False, "error": problem}

    payload = json.dumps(request)
    chunks: list[bytes] = []
    size = 0
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(REPLY_TIMEOUT_S)
            sock.connect(path)
            sock.sendall(payload.encode() + b"\n")
            while True:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
                if b"\n" in chunk or size > MAX_REPLY_BYTES:
                    break
    except (FileNotFoundError, ConnectionRefusedError):
        return {"ok": False, "error": "not_running"}
    except socket.timeout:
        return {"ok": False, "error": "timeout"}
    except OSError as exc:
        return {"ok": False, "error": "not_running", "detail": str(exc)}

    raw = b"".join(chunks).split(b"\n", 1)[0]
    if not raw:
        return {"ok": False, "error": "no_reply"}
    try:
        reply = json.loads(raw)
    except ValueError:
        return {"ok": False, "error": "bad_request"}
    return reply if isinstance(reply, dict) else {"ok": False, "error": "bad_request"}


def _display_name(get_config) -> str:
    identity = (get_config() or {}).get("identity", {}) or {}
    return str(identity.get("first_name") or "COA").strip() or "COA"


def register(cli, *, json_response, get_config):
    """Attach the `desktop` group to the root agictl `cli`."""

    def only_coa(doing: str) -> None:
        # AGICTL_AGENT_USER is the wrapper-set OS caller; only COA may use the desktop.
        if os.environ.get("AGICTL_AGENT_USER", "") != "coa":
            json_response(False, error=f"Permission denied: only COA can {doing} the Primary User's desktop")
            sys.exit(1)

    def emit(success: bool, **fields) -> None:
        # Compact: a look reaches COA whole within her desktop tool's limit (§4.35 L4).
        print(json.dumps({"success": success, **fields}, separators=(",", ":")))

    def respond(reply: dict) -> None:
        result = {key: reply[key] for key in ("tree", "window", "menus", "pictures") if key in reply}
        if reply.get("ok"):
            emit(True, grant=reply.get("grant"), **result)
            return
        code = str(reply.get("error") or "no_reply")
        message = reply.get("reason") if code == "refused" else ERROR_TEXT.get(code, code)
        emit(False, error=code, message=message, **result)
        sys.exit(1)

    @cli.group("desktop")
    def desktop():
        """Observe and operate the Primary User's desktop (HeteroSphere). COA only."""
        pass

    @desktop.group("look")
    def look():
        """Read a desktop tree."""
        pass

    @look.command("pu")
    @click.option("--window", "window", default="",
                  help="A window's id from a look (window, window-dialog, popup-lightbox): only its controls.")
    @click.option("--screen", "screen", type=click.IntRange(0, 15), default=0,
                  help="The screen that window is on (screen-<n>). Default 0.")
    def look_pu(window, screen):
        """Read the Primary User's desktop tree, every screen. Each app window is summed up
        (`controls`, `advice`); the window that has the keys and any open menu come in full.
        With --window, only that window's controls. They allow or reject each look unless they
        already granted one for a while."""
        only_coa("observe")
        respond(request_look("coa", _display_name(get_config), window=window, screen=screen))

    @desktop.group("see")
    def see():
        """Take a picture of desktop screens."""
        pass

    @see.command("pu")
    @click.option("--screen", "screens", type=click.IntRange(0, 15), multiple=True,
                  help="Screen index from `desktop look pu` (screen-<n>); repeat for several. Default: every screen.")
    def see_pu(screens):
        """Take a PNG picture of the Primary User's screens. Each screen asks them on that
        screen unless it may already be seen. Prints each picture's path; read it with
        agictl_view_image."""
        only_coa("see")
        respond(request_see("coa", _display_name(get_config), list(screens)))

    def add_step(step: str, help_text: str, takes_element: bool | str) -> None:
        group = desktop.group(step, help=f"{help_text} (Perceptron step)")(lambda: None)

        def run(element: str = "") -> None:
            only_coa("operate")
            respond(request_act("coa", _display_name(get_config), step, element or ""))

        if takes_element == "text":
            def run_type(element: str, text: str) -> None:
                only_coa("operate")
                respond(request_act("coa", _display_name(get_config), step, element, text=text))
            group.command("pu", help=f"{help_text} ELEMENT is the field's id from `desktop look pu`.")(
                click.argument("element")(click.argument("text")(run_type)))
        elif takes_element == "keys":
            def run_keys(keys: str) -> None:
                only_coa("operate")
                respond(request_act("coa", _display_name(get_config), step, keys=keys))
            group.command("pu", help=help_text)(click.argument("keys")(run_keys))
        elif takes_element == "optional":
            group.command("pu", help=f"{help_text} ELEMENT, if given, is the id from `desktop look pu`.")(
                click.argument("element", required=False, default="")(run))
        elif takes_element:
            group.command("pu", help=f"{help_text} ELEMENT is the item id from `desktop look pu`.")(
                click.argument("element")(run))
        else:
            group.command("pu", help=help_text)(run)

    for step, (help_text, takes_element) in STEPS.items():
        add_step(step, help_text, takes_element)
