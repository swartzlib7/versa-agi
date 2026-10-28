"""`agictl desktop` — COA observes and operates the Primary User's desktop (HeteroSphere).

`desktop look pu` reads the tree. `desktop <step> pu [element]` takes one Perceptron
step, the same steps the Primary User takes: open, load, unload, activate, close,
expand, fullscreen, restore for an open interface, minimize for an open app, and back (Escape).
HeteroSphere asks the Primary User to allow looking and operating separately, unless
a standing grant is active. Contract: design/spec/state/state_computer_use.md §1 and
state_versa_desktop.md §1.8.
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


def request_look(agent: str, display_name: str, path: str = SOCKET_PATH) -> dict:
    """Send one look request and return HeteroSphere's reply (or a local error)."""
    return _request({"op": "look", "target": "pu", "agent": agent, "display_name": display_name}, path)


def request_act(agent: str, display_name: str, step: str, element: str = "",
                path: str = SOCKET_PATH) -> dict:
    """Send one Perceptron step and return HeteroSphere's reply (or a local error)."""
    request = {"op": "act", "target": "pu", "agent": agent, "display_name": display_name, "step": step}
    if element:
        request["id"] = element
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

    def respond(reply: dict) -> None:
        if reply.get("ok"):
            json_response(True, grant=reply.get("grant"), tree=reply.get("tree"))
            return
        code = str(reply.get("error") or "no_reply")
        message = reply.get("reason") if code == "refused" else ERROR_TEXT.get(code, code)
        extra = {"tree": reply["tree"]} if "tree" in reply else {}
        json_response(False, error=code, message=message, **extra)
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
    def look_pu():
        """Read the Primary User's desktop tree. They allow or reject each look
        unless they already granted one for a while."""
        only_coa("observe")
        respond(request_look("coa", _display_name(get_config)))

    def add_step(step: str, help_text: str, takes_element: bool | str) -> None:
        group = desktop.group(step, help=f"{help_text} (Perceptron step)")(lambda: None)

        def run(element: str = "") -> None:
            only_coa("operate")
            respond(request_act("coa", _display_name(get_config), step, element or ""))

        if takes_element == "optional":
            group.command("pu", help=f"{help_text} ELEMENT, if given, is the id from `desktop look pu`.")(
                click.argument("element", required=False, default="")(run))
        elif takes_element:
            group.command("pu", help=f"{help_text} ELEMENT is the item id from `desktop look pu`.")(
                click.argument("element")(run))
        else:
            group.command("pu", help=help_text)(run)

    for step, (help_text, takes_element) in STEPS.items():
        add_step(step, help_text, takes_element)
