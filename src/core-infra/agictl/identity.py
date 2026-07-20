
import db_connect
import os
import json
import re
import urllib.request
import urllib.error
import sqlite3
from rich.console import Console

console = Console()

VV_API_BASE = "https://us-central1-versavoice-s777.cloudfunctions.net/api/v1"


def api_request(endpoint, token, method="GET", body=None):
    url = VV_API_BASE + endpoint
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    data = json.dumps(body).encode("utf-8") if body else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)

    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        console.print(f"[bold red]HTTP Error {e.code}:[/bold red] {e.read().decode('utf-8')}")
        return None
    except Exception as e:
        console.print(f"[bold red]Network Error:[/bold red] {str(e)}")
        return None


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
    """Prefer email+key match for COA; else exact first+last on subAccounts."""
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
):
    """
    Provisions a VersaVoice sub-account for the given agent and synchronizes the registry.

    COA reuse: when install_email is set, match existing API sub-accounts by
    (agiInstallEmail + agiAgentKey) before creating a duplicate.
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

    if existing_id:
        console.print(f"Found existing sub_account_id in config: {existing_id}")
        verify = api_request(f"/accounts/{existing_id}", token)
        if verify:
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
            console.print(f"Sub-account {existing_id} no longer exists on VersaVoice — clearing stale config")
            if "versavoice" in config_data:
                config_data["versavoice"]["sub_account_id"] = None
                config_data["versavoice"]["status"] = None

    console.print(
        f"Scanning sponsor sub-accounts for reuse "
        f"(key={resolved_key}, email={'set' if norm_email else 'none'}, "
        f"name='{first_name} {last_name}')..."
    )
    account_data = api_request("/account", token)
    match_id = None
    if account_data:
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
        console.print(
            f"\n[bold yellow]Attention Primary User:[/bold yellow] You must open the VersaVoice App "
            f"and verify the outstanding '{first_name} {last_name}' connection request before this "
            f"agent can interact externally.\n"
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
