
import db_connect
import hashlib
import os
import json
import pathlib
import re
import urllib.request
import urllib.error
import sqlite3
from rich.console import Console

console = Console()

VV_API_BASE = "https://us-central1-versavoice-s777.cloudfunctions.net/api/v1"

# Normal COA: shared key so the same PU email reuses one VersaVoice identity.
# Sentinel: host-stable coa-s-<12 hex> from /etc/machine-id (persisted in setup.ini).
SHARED_COA_AGENT_KEY = "coa"
SENTINEL_AGENT_KEY_PREFIX = "coa-s-"
SENTINEL_AGENT_KEY_RE = re.compile(r"^coa-s-[0-9a-f]{12}$")


def _is_shared_coa_agent_key(agent_key: str) -> bool:
    return (agent_key or "").strip() == SHARED_COA_AGENT_KEY


def home_coa_key_in_use(token: str, install_email: str) -> bool:
    """True if the sponsor already has a sub-account on ``agiAgentKey=coa``."""
    account_data = api_request("/account", token)
    if not account_data:
        raise RuntimeError("Could not read VersaVoice account for home-COA collision check")
    match = _find_sub_account(
        account_data, "", "", install_email, SHARED_COA_AGENT_KEY
    )
    return match is not None


def derive_sentinel_agent_key(host_material: str) -> str:
    """Host-stable VersaVoice agiAgentKey for a Sentinel install."""
    material = (host_material or "").strip()
    if not material:
        raise ValueError("host_material is required")
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:12]
    return f"{SENTINEL_AGENT_KEY_PREFIX}{digest}"


def read_host_material(machine_id_path: str = "/etc/machine-id") -> str:
    """Prefer systemd machine-id; otherwise a one-shot random hex (caller must persist)."""
    try:
        text = pathlib.Path(machine_id_path).read_text(encoding="utf-8").strip()
        if text:
            return text
    except OSError:
        pass
    return os.urandom(16).hex()


_last_error_code = ""


def last_error_code() -> str:
    """``error`` field of the last HTTP error body (e.g. ``api_account_pending``)."""
    return _last_error_code


def api_request(endpoint, token, method="GET", body=None, with_status=False):
    """JSON body or None. ``with_status=True`` returns ``(body, http_status)``;
    status 0 = network error or unreadable body."""
    global _last_error_code
    _last_error_code = ""
    url = VV_API_BASE + endpoint
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    data = json.dumps(body).encode("utf-8") if body else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)

    result, status = None, 0
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            status = response.status
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        status = e.code
        raw = e.read().decode("utf-8", errors="replace")
        try:
            _last_error_code = str(json.loads(raw).get("error") or "")
        except Exception:
            pass
        console.print(f"[bold red]HTTP Error {e.code}:[/bold red] {raw}")
    except Exception as e:
        console.print(f"[bold red]Network Error:[/bold red] {str(e)}")
        if status == 200:
            status = 0
    return (result, status) if with_status else result


def _api_unavailable(status: int) -> bool:
    """Outage / throttle — says nothing about the token or the sub-account."""
    return status == 0 or status in (408, 429) or status >= 500


def _normalize_install_email(email: str | None) -> str:
    return (email or "").strip().lower()


def _resolve_agent_key(agent_user: str, agents_db: str) -> str:
    """Stable agents.db ``name`` (e.g. coa), not the configurable OS username."""
    if not os.path.exists(agents_db):
        return agent_user
    try:
        conn = db_connect.connect_compat(agents_db, timeout=5)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT name FROM agents WHERE name = ? OR os_user = ?",
            (agent_user, agent_user),
        ).fetchone()
        conn.close()
        if row and row["name"]:
            return str(row["name"])
    except Exception:
        pass
    return agent_user


def _find_sub_account(account_data: dict, first_name: str, last_name: str,
                      install_email: str, agent_key: str) -> str | None:
    """Match by (agiInstallEmail + agiAgentKey). Name fallback is Normal COA only."""
    subs = account_data.get("subAccounts") or account_data.get("connections") or []
    norm_email = _normalize_install_email(install_email)

    if norm_email and agent_key:
        for sub in subs:
            sub_email = _normalize_install_email(
                sub.get("agiInstallEmail") or sub.get("agi_install_email")
            )
            sub_key = (sub.get("agiAgentKey") or sub.get("agi_agent_key") or "").strip()
            if sub_email == norm_email and sub_key == agent_key:
                return sub.get("subAccountId") or sub.get("uid")

    if not _is_shared_coa_agent_key(agent_key):
        return None

    for sub in subs:
        fn = sub.get("firstName") or ""
        ln = sub.get("lastName") or ""
        if not fn and sub.get("displayName"):
            parts = str(sub["displayName"]).rsplit(" ", 1)
            fn = parts[0] if parts else ""
            ln = parts[1] if len(parts) > 1 else ""
        if fn == first_name and ln == last_name:
            return sub.get("subAccountId") or sub.get("uid")
    return None


def _bound_agent_key(account_data: dict | None, sub_id: str) -> str | None:
    """agiAgentKey on the sponsor sub-account with this id, if listed."""
    if not account_data or not sub_id:
        return None
    subs = account_data.get("subAccounts") or account_data.get("connections") or []
    for sub in subs:
        sid = sub.get("subAccountId") or sub.get("uid")
        if sid == sub_id:
            key = (sub.get("agiAgentKey") or sub.get("agi_agent_key") or "").strip()
            return key or None
    return None


def _should_reuse_config_id(resolved_key: str, bound_key: str | None) -> bool:
    """Reuse local ``sub_account_id`` only when it belongs to this host's key.

    Normal COA (``coa``): a live config id is always reused (home reinstall).
    Sentinel host-stable key: reuse only if the bound account's ``agiAgentKey``
    matches. A leftover home-COA id (key ``coa``) must not skip registration.
    """
    if _is_shared_coa_agent_key(resolved_key):
        return True
    return (bound_key or "").strip() == (resolved_key or "").strip()


def _sync_display_name(token: str, sub_id: str, first_name: str, last_name: str) -> None:
    """PATCH first/last when reusing an account under a new call sign."""
    result = api_request(
        f"/accounts/{sub_id}",
        token,
        method="PUT",
        body={"firstName": first_name, "lastName": last_name},
    )
    if result:
        console.print(f"Updated display name on {sub_id}: {first_name} {last_name}")
    else:
        console.print(
            f"[bold yellow]WARN:[/bold yellow] Could not PATCH name on {sub_id} "
            f"(reuse still bound locally)."
        )


def _token_owner(account_data: dict | None) -> str:
    """``Name (email)`` of the VersaVoice account that owns the API token."""
    if not account_data:
        return "unknown"
    name = (account_data.get("displayName") or "").strip()
    email = (account_data.get("email") or "").strip()
    uid = (account_data.get("uid") or "").strip()
    label = name or uid or "unknown"
    return f"{label} ({email})" if email else label


def _report_unresolved(status: int, bound_id: str | None) -> None:
    """Explain why provision stopped without scanning or registering."""
    keep = f"; keeping sub_account_id {bound_id}" if bound_id else ""
    if _api_unavailable(status):
        console.print(
            f"[bold yellow]VersaVoice API unavailable (HTTP {status or 'network'}){keep}. "
            f"Not scanning or registering — re-run setup when the API is back.[/bold yellow]"
        )
    else:
        console.print(
            f"[bold red]VersaVoice API refused the request (HTTP {status}){keep}. "
            f"Check the sponsor API token.[/bold red]"
        )


def provision_identity(
    agent_user,
    token,
    first_name,
    last_name,
    language,
    country,
    voice,
    agents_db,
    install_email: str | None = None,
    agent_key: str | None = None,
    update: bool = False,
):
    """
    Provisions a VersaVoice sub-account for the given agent and synchronizes the registry.

    COA reuse: when install_email is set, match existing API sub-accounts by
    (agiInstallEmail + agiAgentKey) before creating a duplicate. Normal COA
    uses agiAgentKey=coa (email reuse is intended). Sentinel uses a host-stable
    key so a second box does not bind the home identity.

    ``update=True`` (setup ``--update``): when the bound ``sub_account_id`` is
    gone and no match is found, stop instead of registering a replacement. The
    API returns 404 both for a deleted account and for one owned by another
    sponsor, so a wrong token must not silently create a new identity.
    """
    # ── Username Format Guard ────────────────────────────
    if not re.match(r'^[a-z][a-z0-9_-]{0,31}$', agent_user):
        console.print(f"[bold red]BLOCKED: '{agent_user}' is not a valid OS username. "
                      f"Use the agent's OS user (e.g. 'coa'), not the display name.[/bold red]")
        return False

    # ── DB-Level Guard ────────────────────────────────────
    if os.path.exists(agents_db):
        try:
            conn = db_connect.connect_compat(agents_db, timeout=5)
            conn.row_factory = sqlite3.Row
            agent_row = conn.execute(
                "SELECT name, os_user FROM agents WHERE name = ? OR os_user = ?",
                (agent_user, agent_user),
            ).fetchone()
            conn.close()

            if not agent_row:
                console.print(f"[bold red]BLOCKED: '{agent_user}' is not a registered agent in agents.db. "
                              f"Only registered agents may be provisioned.[/bold red]")
                return False
        except Exception as e:
            console.print(f"[bold red]BLOCKED: Failed to verify agent record — {e}[/bold red]")
            return False
    else:
        console.print(f"[bold red]BLOCKED: agents.db not found at {agents_db}. Cannot verify agent record.[/bold red]")
        return False

    resolved_key = (agent_key or "").strip() or _resolve_agent_key(agent_user, agents_db)
    norm_email = _normalize_install_email(install_email)

    # ── Config-Level Duplicate Guard ─────────────────────
    config_file = f"/etc/versa-agi/{agent_user}_config.json"
    existing_id = None
    config_data = {}

    if os.path.exists(config_file):
        with open(config_file, "r") as f:
            try:
                config_data = json.load(f)
                existing_id = config_data.get("versavoice", {}).get("sub_account_id")
            except Exception as e:
                console.print(f"[bold yellow]WARN: Failed to parse existing config ({str(e)})[/bold yellow]")

    gone_id = None
    if existing_id:
        console.print(f"Found existing sub_account_id in config: {existing_id}")
        verify, verify_status = api_request(
            f"/accounts/{existing_id}", token, with_status=True
        )
        pending = verify_status == 403 and last_error_code() == "api_account_pending"
        live = verify_status == 200 and bool(verify)
        if verify_status != 404 and not (live or pending):
            _report_unresolved(verify_status, existing_id)
            return False
        if live or pending:
            bound_key = None
            account_data = None
            if pending or not _is_shared_coa_agent_key(resolved_key):
                account_data, account_status = api_request(
                    "/account", token, with_status=True
                )
                if account_status != 200 or not account_data:
                    _report_unresolved(account_status, existing_id)
                    return False
                console.print(f"API token owner: {_token_owner(account_data)}")
                bound_key = _bound_agent_key(account_data, existing_id)
            if not _should_reuse_config_id(resolved_key, bound_key):
                console.print(
                    f"Config sub_account_id {existing_id} is not this host's "
                    f"VersaVoice key ({resolved_key}); not reusing — will register "
                    f"a new identity."
                )
                if "versavoice" in config_data:
                    config_data["versavoice"]["sub_account_id"] = None
                    config_data["versavoice"]["status"] = None
            elif pending:
                console.print(
                    f"[bold yellow]Sub-account {existing_id} is waiting for "
                    f"{_token_owner(account_data)} to accept its connection request "
                    f"in VersaVoice AI.[/bold yellow] Keeping it. The agent cannot "
                    f"interact externally until the request is accepted."
                )
                _write_identity(
                    existing_id, "registered", config_data, config_file, agents_db,
                    first_name, last_name, language, country, voice, agent_user,
                )
                return True
            else:
                console.print(f"Identity resolved: {existing_id} (from config)")
                cur_fn = (verify.get("firstName") or "").strip()
                cur_ln = (verify.get("lastName") or "").strip()
                if cur_fn != first_name or cur_ln != last_name:
                    _sync_display_name(token, existing_id, first_name, last_name)
                _write_identity(
                    existing_id, "active", config_data, config_file, agents_db,
                    first_name, last_name, language, country, voice, agent_user,
                )
                return True
        else:
            console.print(
                f"Sub-account {existing_id} not found under this API token's account "
                f"(deleted, or owned by another VersaVoice account)."
            )
            gone_id = existing_id
            if "versavoice" in config_data:
                config_data["versavoice"]["sub_account_id"] = None
                config_data["versavoice"]["status"] = None

    console.print(
        f"Scanning sponsor sub-accounts for reuse "
        f"(key={resolved_key}, email={'set' if norm_email else 'none'}, "
        f"name='{first_name} {last_name}')..."
    )
    account_data, account_status = api_request("/account", token, with_status=True)
    if account_status != 200 or not account_data:
        _report_unresolved(account_status, None)
        return False
    owner = _token_owner(account_data)
    console.print(f"API token owner: {owner}")
    match_id = _find_sub_account(
        account_data, first_name, last_name, norm_email, resolved_key
    )

    if match_id:
        console.print(f"Found existing VersaVoice account: {match_id}")
        _sync_display_name(token, match_id, first_name, last_name)
        _write_identity(
            match_id, "active", config_data, config_file, agents_db,
            first_name, last_name, language, country, voice, agent_user,
        )
        return True

    if update and gone_id:
        console.print(
            f"[bold red]Not registering a new VersaVoice identity during an update.[/bold red]\n"
            f"This install is bound to sub-account {gone_id}, but the API token's account "
            f"({owner}) cannot see it, and no matching sub-account was found.\n"
            f"Most likely [versavoice] api_token in /etc/versa-agi/setup.ini belongs to "
            f"the wrong Primary User. Fix the token and run the update again.\n"
            f"If {gone_id} was deleted on purpose, clear versavoice.sub_account_id in "
            f"{config_file} and run the update again to register a new identity.\n"
            f"The config was not changed."
        )
        return False

    # Map voice preference to VersaVoice API fields
    chromosome_map = {"female": "Y", "male": "X", "reflective": "Reflective"}
    voice_selection_map = {"female": "female", "male": "male", "reflective": None}
    chromosome = chromosome_map.get(voice, "Y")
    voice_selection = voice_selection_map.get(voice, "female")

    console.print(f"Registering new sub-account: {first_name} {last_name} (chromosome: {chromosome})...")
    reg_body = {
        "firstName": first_name,
        "lastName": last_name,
        "spokenLanguage": language,
        "countryOfBirth": country if country != "" else None,
        "chromosome": chromosome,
        "voiceSelection": voice_selection,
        "role": None,
        "abilities": [],
        "agiAgentKey": resolved_key,
    }
    if norm_email:
        reg_body["agiInstallEmail"] = norm_email
    # Bare call sign without parentheses for telemetry
    bare = last_name.strip("()")
    if bare and bare != last_name:
        reg_body["agiCallSign"] = bare

    reg_result = api_request("/accounts/register", token, method="POST", body=reg_body)
    if not reg_result:
        console.print("[bold red]Registration failed — no response from API.[/bold red]")
        return False

    new_id = reg_result.get("subAccountId") or reg_result.get("uid")
    if not new_id:
        console.print(f"[bold red]Registration response did not contain a sub-account ID: {reg_result}[/bold red]")
        return False

    reused = bool(reg_result.get("reused"))
    if reused:
        console.print(f"Reused existing sub-account (server idempotent): {new_id}")
        _sync_display_name(token, new_id, first_name, last_name)
        status = "active"
    else:
        console.print(f"Registered new sub-account: {new_id}")
        status = "registered"
        recipient = owner if owner != "unknown" else "the API token's owner"
        console.print(
            f"\n[bold yellow]Attention Primary User:[/bold yellow] A new connection request "
            f"for '{first_name} {last_name}' was sent to {recipient}. Open the VersaVoice AI app "
            f"as that user and accept it before this agent can interact externally.\n"
        )

    _write_identity(
        new_id, status, config_data, config_file, agents_db,
        first_name, last_name, language, country, voice, agent_user,
    )
    return True


def _write_identity(sub_id, status, config_data, config_file, agents_db, fn, ln, lang, country, voice, agent_user=None):
    if "versavoice" not in config_data:
        config_data["versavoice"] = {}
    if "identity" not in config_data:
        config_data["identity"] = {}

    config_data["versavoice"]["sub_account_id"] = sub_id
    config_data["versavoice"]["status"] = status

    config_data["identity"]["first_name"] = fn
    config_data["identity"]["last_name"] = ln
    config_data["identity"]["language"] = lang
    config_data["identity"]["country"] = country if country != "" else None
    config_data["identity"]["voice"] = voice

    # ── Inherit shared credentials from COA config ──
    coa_config_path = "/etc/versa-agi/coa_config.json"
    if os.path.exists(coa_config_path):
        try:
            with open(coa_config_path, "r") as cf:
                coa_config = json.load(cf)
            coa_token = coa_config.get("versavoice", {}).get("api_token")
            if coa_token and not config_data["versavoice"].get("api_token"):
                config_data["versavoice"]["api_token"] = coa_token
            coa_pu = coa_config.get("primary_user")
            if coa_pu and "primary_user" not in config_data:
                config_data["primary_user"] = coa_pu
        except Exception:
            pass

    with open(config_file, "w") as f:
        json.dump(config_data, f, indent=2)

    if agent_user:
        import subprocess
        subprocess.run(["chown", f"watchdog:{agent_user}", config_file], check=False)
        subprocess.run(["chmod", "640", config_file], check=False)

    console.print(f"Wrote identity + sub_account_id to {config_file}")
