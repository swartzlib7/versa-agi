"""agictl utility + model modality-map command groups (TD-UTIL-001)."""
from __future__ import annotations

import db_connect


import json
import os
import sqlite3
import subprocess
import sys

import click

from modality_maps import (
    default_map_for_catalog_entry,
    load_modality_map,
    parse_map_json,
    save_modality_map,
    seed_all_modality_maps,
)
from utility_store import (
    VALID_OUTPUT_MODALITIES,
    add_utility_model,
    freeze_active_utility_tasks,
    get_utility_model,
    list_due_utility_tasks,
    list_utility_models,
    parse_input_files_json,
    remove_utility_model,
    update_utility_model,
)
from harness.utility_runner import UtilityRunError, run_utility_model


def _queue_utility_spawn_wake(spawn_agent: str, result: dict) -> None:
    """Queue a lifeline wake for an agent after a successful Utility Task."""
    agent = (spawn_agent or "").strip()
    if not agent:
        return
    wake_dir = f"/var/lib/versa-agi/{agent}"
    os.makedirs(wake_dir, exist_ok=True)
    wake_path = os.path.join(wake_dir, "utility_wake.json")
    payload = {
        "task_id": result.get("task_id"),
        "run_id": result.get("run_id"),
        "utility_model_id": result.get("utility_model_id"),
        "output_dir": result.get("output_dir"),
        "artifacts": result.get("artifacts", []),
    }
    with open(wake_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)


def _vv_utility_alert(message: str) -> None:
    """Best-effort VersaVoice message to Primary User."""
    config_path = os.environ.get("AGICTL_CONFIG", "/etc/versa-agi/coa_config.json")
    sponsor_uid = ""
    try:
        with open(config_path, encoding="utf-8") as f:
            sponsor_uid = json.load(f).get("primary_user", {}).get("uid", "")
    except Exception:
        pass
    if not sponsor_uid:
        return
    env = os.environ.copy()
    env.setdefault("AGICTL_CONFIG", "/etc/versa-agi/coa_config.json")
    subprocess.run(
        ["/usr/local/bin/agictl", "message", "send", sponsor_uid, message, "--mode", "typed"],
        env=env,
        capture_output=True,
        timeout=30,
    )


# ── Utility Model execution dispatch ──────────────────────────────────────────
# Default harness venv (carries the model SDKs: openai + langchain*). agictl runs
# under a lighter venv that intentionally omits them — see _run_utility_model().
_HARNESS_VENV_DEFAULT = "/usr/local/lib/versa-agi/venv"
_HARNESS_RESULT_MARKER = "@@UTIL_RUN_RESULT@@"


def _harness_python() -> str | None:
    """Resolve the harness venv interpreter (the one carrying the model SDKs)."""
    venv = (os.environ.get("VERSA_HARNESS_VENV") or _HARNESS_VENV_DEFAULT).strip()
    cand = os.path.join(venv, "bin", "python")
    return cand if os.path.exists(cand) else None


def _run_utility_model(um_id: str, **params):
    """Execute a Utility Model run, routed to the **harness venv**.

    RATIONALE — the two-venv split. agictl/agitop run under a deliberately
    lightweight venv (``/opt/versa-agi/venv``: click/rich/textual/psutil/Pillow)
    so the CLI and dashboard stay small and fast. The model SDKs (``openai``, the
    full ``langchain*`` stack) live ONLY in the harness venv
    (``/usr/local/lib/versa-agi/venv``). Utility Model generation is harness code
    (``harness/utility_runner.py`` + ``harness/generation.py``) that agictl merely
    *dispatches*, so we execute it under the harness interpreter — same
    ``CORE_INFRA`` code tree (this file's grandparent dir, forwarded via
    ``PYTHONPATH``), different interpreter — instead of duplicating the
    heavyweight SDK stack into the agictl venv.

    The subprocess inherits agictl's (watchdog) environment, so the
    watchdog-owned provider-key file stays readable for key injection. Falls back
    to in-process execution when the harness venv is unavailable or is the same
    venv we are already running in (dev/test and single-venv installs).
    """
    py = _harness_python()
    # Compare venv ROOTS, not the resolved interpreter: a venv's bin/python is a
    # symlink to the shared base interpreter, so os.path.realpath() of two
    # DIFFERENT venvs collapses to the same /usr/bin/pythonX — which would make
    # the bridge wrongly fall back to in-process (the exact bug this guards).
    harness_root = os.path.dirname(os.path.dirname(py)) if py else None
    if not py or harness_root == sys.prefix:
        # In-process fallback — current interpreter already carries the SDKs.
        return run_utility_model(um_id, **params)

    core_infra = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = os.environ.copy()
    existing_pp = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = core_infra + (os.pathsep + existing_pp if existing_pp else "")

    proc = subprocess.run(
        [py, "-m", "harness.utility_run_cli", json.dumps({"um_id": um_id, **params})],
        env=env,
        capture_output=True,
        text=True,
    )

    payload = None
    for line in (proc.stdout or "").splitlines():
        if line.startswith(_HARNESS_RESULT_MARKER):
            try:
                payload = json.loads(line[len(_HARNESS_RESULT_MARKER):])
            except ValueError:
                payload = None
    if payload is None:
        detail = (proc.stderr or proc.stdout or "").strip()[:500]
        raise UtilityRunError(
            "harness_bridge",
            f"Harness run produced no result (exit {proc.returncode}): {detail}",
        )
    if payload.get("success"):
        return payload["result"]
    raise UtilityRunError(
        payload.get("code") or "utility_error",
        payload.get("error") or "Utility run failed",
    )


def register(
    cli,
    *,
    json_response,
    tasks_reader,
    require_pu_or_coa,
    load_catalog,
    tasks_db: str,
):
    @cli.group()
    def utility():
        """Utility Model profiles and one-shot runs (Phase F)."""
        pass

    @utility.group("model")
    def utility_model():
        """CRUD for Utility Model profiles."""
        pass

    @utility_model.command("list")
    @click.option("--table", "as_table", is_flag=True)
    @click.option("--enabled-only", is_flag=True)
    def um_list(as_table, enabled_only):
        rows = list_utility_models(enabled_only=enabled_only)
        if as_table:
            if not rows:
                click.echo("No Utility Models.")
                return
            click.echo(f"{'ID':<24} {'LABEL':<22} {'MODEL':<28} {'OUT':<6} {'PATH':<30} ON")
            for r in rows:
                click.echo(
                    f"{r['id']:<24} {(r.get('label') or '')[:22]:<22} "
                    f"{(r.get('catalog_model') or '')[:28]:<28} "
                    f"{(r.get('output_modality') or ''):<6} "
                    f"{(r.get('output_path') or '')[:30]:<30} "
                    f"{'Y' if r.get('enabled') else 'N'}"
                )
            return
        json_response(True, count=len(rows), utility_models=rows)

    @utility_model.command("show")
    @click.argument("um_id")
    def um_show(um_id):
        row = get_utility_model(um_id)
        if not row:
            json_response(False, error=f"Utility Model '{um_id}' not found")
            sys.exit(1)
        json_response(True, utility_model=row)

    @utility_model.command("add")
    @click.option("--id", "um_id", required=True)
    @click.option("--label", required=True)
    @click.option("--catalog-model", required=True)
    @click.option("--output-modality", required=True, type=click.Choice(VALID_OUTPUT_MODALITIES))
    @click.option("--output-path", default=".agent/utility")
    @click.option("--system-prompt", default=None)
    @click.option("--system-prompt-file", type=click.Path(exists=True), default=None)
    @click.option("--run-as-agent", default="coa")
    @click.option("--config-json", default=None)
    @click.option("--disabled", is_flag=True)
    def um_add(um_id, label, catalog_model, output_modality, output_path, system_prompt,
               system_prompt_file, run_as_agent, config_json, disabled):
        require_pu_or_coa()
        cat = load_catalog()
        if catalog_model not in cat or not cat[catalog_model].get("enabled"):
            json_response(False, error=f"Catalog model '{catalog_model}' not found or disabled")
            sys.exit(1)
        if get_utility_model(um_id):
            json_response(False, error=f"Utility Model '{um_id}' already exists")
            sys.exit(1)
        if system_prompt_file:
            with open(system_prompt_file, encoding="utf-8") as f:
                system_prompt = f.read()
        if not (system_prompt or "").strip():
            json_response(False, error="--system-prompt or --system-prompt-file required")
            sys.exit(1)
        try:
            add_utility_model(
                um_id=um_id,
                label=label,
                catalog_model=catalog_model,
                system_prompt=system_prompt,
                output_modality=output_modality,
                output_path=output_path,
                run_as_agent=run_as_agent,
                config_json=config_json,
                enabled=not disabled,
            )
            json_response(True, action="utility_model_add", id=um_id)
        except Exception as e:
            json_response(False, error=str(e))
            sys.exit(1)

    @utility_model.command("update")
    @click.argument("um_id")
    @click.option("--label", default=None)
    @click.option("--catalog-model", default=None)
    @click.option("--output-modality", type=click.Choice(VALID_OUTPUT_MODALITIES), default=None)
    @click.option("--output-path", default=None)
    @click.option("--system-prompt", default=None)
    @click.option("--system-prompt-file", type=click.Path(exists=True), default=None)
    @click.option("--run-as-agent", default=None)
    @click.option("--config-json", default=None)
    @click.option("--enabled/--disabled", default=None)
    def um_update(um_id, label, catalog_model, output_modality, output_path, system_prompt,
                  system_prompt_file, run_as_agent, config_json, enabled):
        require_pu_or_coa()
        if not get_utility_model(um_id):
            json_response(False, error=f"Utility Model '{um_id}' not found")
            sys.exit(1)
        if system_prompt_file:
            with open(system_prompt_file, encoding="utf-8") as f:
                system_prompt = f.read()
        fields = {
            "label": label,
            "catalog_model": catalog_model,
            "output_modality": output_modality,
            "output_path": output_path,
            "system_prompt": system_prompt,
            "run_as_agent": run_as_agent,
            "config_json": config_json,
            "enabled": enabled,
        }
        if not update_utility_model(um_id, fields):
            json_response(False, error="No fields to update")
            sys.exit(1)
        json_response(True, action="utility_model_update", id=um_id)

    @utility_model.command("remove")
    @click.argument("um_id")
    def um_remove(um_id):
        require_pu_or_coa()
        if not remove_utility_model(um_id):
            json_response(False, error=f"Utility Model '{um_id}' not found")
            sys.exit(1)
        json_response(True, action="utility_model_remove", id=um_id)

    @utility.command("run")
    @click.argument("um_id")
    @click.option("--output-dir", default=None)
    @click.option("--input-files", default=None, help="Comma-separated local paths")
    @click.option("--vars", default=None, help="JSON object for {{var}} substitution")
    @click.option("--task-id", type=int, default=None)
    @click.option("--context-agent", default=None, help="Override run context OS agent user")
    @click.option("--agent-workspace", default=None, help="Agent workspace root (lifeline-supplied; resolves relative output paths)")
    @click.option("--dry-run", is_flag=True)
    def utility_run(um_id, output_dir, input_files, vars, task_id, context_agent, agent_workspace, dry_run):
        paths = [p.strip() for p in (input_files or "").split(",") if p.strip()]
        vars_map = None
        if vars:
            try:
                vars_map = json.loads(vars)
            except json.JSONDecodeError:
                json_response(False, error="--vars must be valid JSON")
                sys.exit(1)

        ctx = context_agent
        task_override = None
        if task_id:
            t = tasks_reader.get_task(task_id)
            if not t:
                json_response(False, error=f"Task {task_id} not found")
                sys.exit(1)
            ctx = t.get("assigned_to") or ctx
            task_override = t.get("utility_output_override")
            if not paths and t.get("utility_input_files"):
                paths = parse_input_files_json(t.get("utility_input_files"))

        try:
            result = _run_utility_model(
                um_id,
                output_dir=output_dir,
                input_files=paths,
                vars_map=vars_map,
                task_id=task_id,
                context_agent=ctx,
                task_output_override=task_override,
                agent_home=agent_workspace,
                dry_run=dry_run,
            )
            payload = dict(result)
            payload.pop("success", None)
            json_response(True, **payload)
        except UtilityRunError as e:
            json_response(False, error=e.message, code=e.code)
            sys.exit(1)
        except Exception as e:
            json_response(False, error=str(e), code="utility_error")
            sys.exit(1)

    @utility.command("run-due-tasks", hidden=True)
    @click.option("--agent", "agent_name", required=True)
    @click.option("--agent-workspace", default=None, help="Agent workspace root (lifeline-supplied; resolves relative output paths without a registry read)")
    def utility_run_due_tasks(agent_name, agent_workspace):
        """Lifeline: execute due Utility Tasks for an agent (no harness spawn)."""
        due = list_due_utility_tasks(agent_name)
        results = []
        for t in due:
            tid = t["id"]
            um_id = t.get("utility_model_id")
            if not um_id:
                continue
            alerts = {"start": False, "stop": False}
            try:
                if t.get("utility_start_alert"):
                    _vv_utility_alert(
                        f"Utility task #{tid} started — {t.get('title', '')} ({um_id}) on {agent_name}",
                    )
                    alerts["start"] = True
                paths = parse_input_files_json(t.get("utility_input_files"))
                result = _run_utility_model(
                    um_id,
                    input_files=paths,
                    task_id=tid,
                    context_agent=agent_name,
                    task_output_override=t.get("utility_output_override"),
                    agent_home=agent_workspace,
                )
                conn = db_connect.connect_compat(tasks_db, timeout=5)
                conn.execute(
                    "UPDATE tasks SET status='done', completed_at=datetime('now'), "
                    "updated_at=datetime('now') WHERE id=?",
                    (tid,),
                )
                note = (
                    f"UM run {result.get('run_id')}: "
                    f"{len(result.get('artifacts', []))} artifact(s) at {result.get('output_dir')}"
                )
                conn.execute(
                    "INSERT INTO task_progress (task_id, agent_name, note) VALUES (?, ?, ?)",
                    (tid, agent_name, note),
                )
                # Rolling 7-day retention for utility journals (not injected
                # into agent system prompts — see lifeline TASK_PROGRESS).
                conn.execute(
                    "DELETE FROM task_progress WHERE task_id = ? "
                    "AND created_at < datetime('now', '-7 days')",
                    (tid,),
                )
                conn.commit()
                conn.close()
                if t.get("utility_stop_alert"):
                    _vv_utility_alert(f"Utility task #{tid} done — {um_id}: {note}")
                    alerts["stop"] = True
                spawn_agent = (t.get("utility_spawn_agent") or "").strip()
                if spawn_agent:
                    _queue_utility_spawn_wake(spawn_agent, result)
                    result["spawn_agent"] = spawn_agent
                    result["spawn_queued"] = True
                result["task_id"] = tid
                result["alerts_sent"] = alerts
                results.append(result)
            except UtilityRunError as e:
                conn = db_connect.connect_compat(tasks_db, timeout=5)
                conn.execute(
                    "UPDATE tasks SET status='blocked', updated_at=datetime('now') WHERE id=?",
                    (tid,),
                )
                conn.execute(
                    "INSERT INTO task_progress (task_id, agent_name, note) VALUES (?, ?, ?)",
                    (tid, agent_name, f"UM failed ({e.code}): {e.message}"),
                )
                conn.execute(
                    "DELETE FROM task_progress WHERE task_id = ? "
                    "AND created_at < datetime('now', '-7 days')",
                    (tid,),
                )
                conn.commit()
                conn.close()
                if t.get("utility_stop_alert"):
                    _vv_utility_alert(f"Utility task #{tid} failed — {um_id}: {e.code}: {e.message}")
                    alerts["stop"] = True
                results.append({"success": False, "task_id": tid, "code": e.code, "error": e.message})
        json_response(True, agent=agent_name, count=len(results), runs=results)

    @utility.command("freeze-tasks", hidden=True)
    def utility_freeze_tasks():
        """Freeze all active utility tasks (lifeline/admin — feature disabled)."""
        require_pu_or_coa()
        n = freeze_active_utility_tasks()
        json_response(True, action="utility_freeze_tasks", frozen_count=n)

    @utility.command("drain-runs-log", hidden=True)
    def utility_drain_runs_log():
        """Truncate the Utility runs log (watchdog owns the file; agitop calls via sudo)."""
        require_pu_or_coa()
        runs_log = "/var/lib/versa-agi/utility-runs/runs.log"
        try:
            if os.path.exists(runs_log):
                with open(runs_log, "w", encoding="utf-8"):
                    pass
            json_response(True, action="utility_drain_runs_log", path=runs_log)
        except OSError as e:
            json_response(False, error=str(e))
            sys.exit(1)


def register_modality_map_commands(model_group, *, json_response, require_pu_or_coa, load_catalog):
    @model_group.group("modality-map")
    def modality_map():
        """Per-catalog input/output extension maps."""
        pass

    @modality_map.command("show")
    @click.argument("catalog_key")
    def mm_show(catalog_key):
        data = load_modality_map(catalog_key)
        if not data:
            cat = load_catalog()
            m = cat.get(catalog_key)
            if not m:
                json_response(False, error=f"Catalog key '{catalog_key}' not found")
                sys.exit(1)
            data = default_map_for_catalog_entry(m)
        json_response(True, catalog_key=catalog_key, map=data)

    @modality_map.command("set")
    @click.argument("catalog_key")
    @click.option("--json-file", type=click.Path(exists=True), required=True)
    def mm_set(catalog_key, json_file):
        require_pu_or_coa()
        cat = load_catalog()
        if catalog_key not in cat:
            json_response(False, error=f"Catalog key '{catalog_key}' not found")
            sys.exit(1)
        with open(json_file, encoding="utf-8") as f:
            data = parse_map_json(f.read())
        if not data or "input" not in data or "output" not in data:
            json_response(False, error="JSON must contain input and output objects")
            sys.exit(1)
        save_modality_map(catalog_key, data)
        json_response(True, action="modality_map_set", catalog_key=catalog_key)

    @modality_map.command("reset")
    @click.argument("catalog_key")
    def mm_reset(catalog_key):
        require_pu_or_coa()
        cat = load_catalog()
        m = cat.get(catalog_key)
        if not m:
            json_response(False, error=f"Catalog key '{catalog_key}' not found")
            sys.exit(1)
        save_modality_map(catalog_key, default_map_for_catalog_entry(m))
        json_response(True, action="modality_map_reset", catalog_key=catalog_key)

    @modality_map.command("seed")
    def mm_seed():
        require_pu_or_coa()
        n = seed_all_modality_maps()
        json_response(True, action="modality_map_seed", seeded=n)
