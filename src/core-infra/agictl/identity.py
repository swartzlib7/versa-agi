import os
import json
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
        with urllib.request.urlopen(req, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        console.print(f"[bold red]HTTP Error {e.code}:[/bold red] {e.read().decode('utf-8')}")
        return None
    except Exception as e:
        console.print(f"[bold red]Network Error:[/bold red] {str(e)}")
        return None

def provision_identity(agent_user, token, first_name, last_name, language, country, voice, agents_db):
    """
    Provisions a VersaVoice sub-account for the given agent and synchronizes the registry.
    """
    # ── Username Format Guard ────────────────────────────
    # agent_user MUST be the OS username (lowercase, no spaces).
    # This prevents config files like "Sienna_config.json" from being created
    # when display names are passed instead of OS usernames.
    import re
    if not re.match(r'^[a-z][a-z0-9_-]{0,31}$', agent_user):
        console.print(f"[bold red]BLOCKED: '{agent_user}' is not a valid OS username. "
                      f"Use the agent's OS user (e.g. 'actingcoach'), not the display name.[/bold red]")
        return False

    # ── DB-Level Guard ──────────────────────────────────
    # Only allow provisioning for agents registered in agents.db.
    if os.path.exists(agents_db):
        try:
            conn = sqlite3.connect(agents_db, timeout=5)
            conn.row_factory = sqlite3.Row
            agent_row = conn.execute(
                "SELECT name FROM agents WHERE name = ?", (agent_user,)
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

    # ── Config-Level Duplicate Guard ─────────────────────
    # sub_account_id is stored in the config file, not the DB.
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
            return True
        else:
            console.print(f"Sub-account {existing_id} no longer exists on VersaVoice — clearing stale config")
            if "versavoice" in config_data:
                config_data["versavoice"]["sub_account_id"] = None
                config_data["versavoice"]["status"] = None

    console.print(f"Scanning sponsor connections for '{first_name} {last_name}'...")
    account_data = api_request("/account", token)
    match_id = None
    
    if account_data and "connections" in account_data:
        for conn in account_data["connections"]:
            if conn.get("firstName") == first_name and conn.get("lastName") == last_name:
                match_id = conn.get("uid")
                break
                
    if match_id:
        console.print(f"Found existing VersaVoice account: {match_id}")
        _write_identity(match_id, "active", config_data, config_file, agents_db, first_name, last_name, language, country, voice, agent_user)
        return True
        
    # Map voice preference to VersaVoice API fields
    chromosome_map = {"female": "Y", "male": "X", "reflective": "Reflective"}
    voice_selection_map = {"female": "female", "male": "male", "reflective": None}
    chromosome = chromosome_map.get(voice, "Y")  # Default to female
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
        "abilities": []
    }
    
    reg_result = api_request("/accounts/register", token, method="POST", body=reg_body)
    if not reg_result:
        console.print("[bold red]Registration failed — no response from API.[/bold red]")
        return False
        
    new_id = reg_result.get("subAccountId") or reg_result.get("uid")
    if not new_id:
        console.print(f"[bold red]Registration response did not contain a sub-account ID: {reg_result}[/bold red]")
        return False
        
    console.print(f"Registered new sub-account: {new_id}")
    _write_identity(new_id, "registered", config_data, config_file, agents_db, first_name, last_name, language, country, voice, agent_user)
    
    console.print(f"\n[bold yellow]Attention Primary User:[/bold yellow] You must open the VersaVoice App and verify the outstanding '{first_name} {last_name}' connection request before this agent can interact externally.\n")
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
    # Sub-agents need the sponsor's api_token for inbox sync and sending,
    # and primary_user for context injection and freeze notifications.
    coa_config_path = "/etc/versa-agi/coa_config.json"
    if os.path.exists(coa_config_path):
        try:
            with open(coa_config_path, "r") as cf:
                coa_config = json.load(cf)
            # Inject api_token if not already set
            coa_token = coa_config.get("versavoice", {}).get("api_token")
            if coa_token and not config_data["versavoice"].get("api_token"):
                config_data["versavoice"]["api_token"] = coa_token
            # Inject primary_user section
            coa_pu = coa_config.get("primary_user")
            if coa_pu and "primary_user" not in config_data:
                config_data["primary_user"] = coa_pu
        except Exception:
            pass  # Non-fatal — setup.sh --update can catch up later
    
    with open(config_file, "w") as f:
        json.dump(config_data, f, indent=2)
        
    if agent_user:
        import subprocess
        subprocess.run(["chown", f"watchdog:{agent_user}", config_file], check=False)
        subprocess.run(["chmod", "640", config_file], check=False)
        
    console.print(f"Wrote identity + sub_account_id to {config_file}")
    

