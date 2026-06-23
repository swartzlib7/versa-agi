"""One-shot Utility Model runner — no LangGraph checkpoint."""

from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from model_catalog import catalog_entry_for_model, load_catalog, read_setup_value
from modality_maps import FILE_MODALITY, validate_input_files, validate_output_artifact
from utility_store import get_utility_model, parse_input_files_json

_RUN_LOCK_DIR = "/var/lib/versa-agi/utility-runs"


class UtilityRunError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


def _substitute_vars(text: str, vars_map: dict[str, Any] | None) -> str:
    if not vars_map:
        return text
    out = text
    for k, v in vars_map.items():
        out = out.replace("{{" + k + "}}", str(v))
    return out


def _agent_home(agent_name: str) -> str:
    """Resolve the agent's workspace root — where its ``.agent/`` directory lives.

    Resolution order (Zero-Trust + Ownership Principle):

    1. ``VERSA_AGENT_WORKSPACE`` env — the authoritative path passed by lifeline
       (watchdog context) via ``--agent-workspace``. The Utility Task path runs
       as the sub-agent (``sudo -u``), which by design **cannot** open
       ``agents.db`` (``watchdog:coa`` 660), so the workspace must be supplied,
       not read from the registry here.
    2. Guarded **read-only** ``agents.db`` lookup — a best-effort fallback that
       only succeeds for callers with registry access (coa / PU manual runs).
       This naturally handles the coa exception (``/home/coa/coa-env``) and
       sub-agent homes (``/home/agi-<name>``).
    3. System data dir — last resort.
    """
    env_ws = (os.environ.get("VERSA_AGENT_WORKSPACE") or "").strip()
    if env_ws:
        return env_ws

    import sqlite3

    db = os.environ.get("AGICTL_AGENTS_DB", "/var/lib/versa-agi/agents.db")
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            row = con.execute(
                "SELECT workspace FROM agents WHERE name=?", (agent_name,)
            ).fetchone()
        finally:
            con.close()
        if row and row[0]:
            return str(row[0])
    except Exception:
        pass
    return f"/var/lib/versa-agi/{agent_name}"


def _resolve_output_dir(
    um: dict[str, Any],
    *,
    output_dir: str | None,
    task_output_override: str | None,
    context_agent: str,
    agent_home: str | None = None,
) -> str:
    raw = (output_dir or task_output_override or um.get("output_path") or "").strip()
    if not raw:
        sub = read_setup_value("utility_models", "default_output_subdir", ".agent/utility")
        raw = sub
    if not os.path.isabs(raw):
        base = (agent_home or "").strip() or _agent_home(context_agent)
        raw = os.path.join(base, raw)
    existed = os.path.isdir(raw)
    os.makedirs(raw, exist_ok=True)
    if not existed:
        # Utility runs execute as watchdog (key-injection elevation), so a freshly
        # created output dir is watchdog-owned. Make it group-writable under
        # agi_agents + setgid so both watchdog (writer) and the owning agent
        # (reader/manager) can use it, and artifacts inherit the shared group.
        # Best-effort: pre-existing agent-owned dirs are provisioned by setup.sh.
        try:
            import grp

            os.chown(raw, -1, grp.getgrnam("agi_agents").gr_gid)
            os.chmod(raw, 0o2775)
        except (OSError, KeyError):
            pass
    return os.path.realpath(raw)


def _acquire_run_lock(task_id: int | None, um_id: str) -> str | None:
    os.makedirs(_RUN_LOCK_DIR, exist_ok=True)
    key = f"task-{task_id}" if task_id else f"um-{um_id}"
    lock_path = os.path.join(_RUN_LOCK_DIR, f"{key}.lock")
    if os.path.exists(lock_path):
        return None
    with open(lock_path, "w", encoding="utf-8") as f:
        f.write(datetime.now(timezone.utc).isoformat())
    return lock_path


def _release_run_lock(lock_path: str | None) -> None:
    if lock_path and os.path.isfile(lock_path):
        try:
            os.remove(lock_path)
        except OSError:
            pass


def _stamp_last_run() -> None:
    """Record the completion time of the most recent real Utility run.

    Written by the runner (always elevated to ``watchdog`` via the ``agictl``
    wrapper, the owner of ``_RUN_LOCK_DIR``) as a single world-readable marker so
    the dashboard can show a "last run" timestamp without elevation. Best-effort.
    """
    try:
        os.makedirs(_RUN_LOCK_DIR, exist_ok=True)
        with open(os.path.join(_RUN_LOCK_DIR, ".last"), "w", encoding="utf-8") as f:
            f.write(datetime.now(timezone.utc).isoformat())
    except OSError:
        pass


_RUN_LOG = os.path.join(_RUN_LOCK_DIR, "runs.log")


def _log_run(record: dict[str, Any]) -> None:
    """Append a durable one-line JSON record of a Utility run outcome.

    The runner executes as ``watchdog`` (owner of ``_RUN_LOCK_DIR``), so this log
    is always writable and the directory is world-readable for the dashboard / PU.
    Logged for BOTH success and failure so a run is never "completely dark".
    Best-effort — never raises into the run path.
    """
    try:
        os.makedirs(_RUN_LOCK_DIR, exist_ok=True)
        record = {"ts": datetime.now(timezone.utc).isoformat(), **record}
        new = not os.path.exists(_RUN_LOG)
        with open(_RUN_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
        if new:
            try:
                os.chmod(_RUN_LOG, 0o644)
            except OSError:
                pass
    except OSError:
        pass


def _read_file_text(path: str, max_chars: int = 120_000) -> str:
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read(max_chars)


def _invoke_chat_model(catalog_model: str, system_prompt: str, user_content: str) -> str:
    """One-shot text generation via harness LLM helper."""
    import sys

    harness_dir = os.path.dirname(os.path.abspath(__file__))
    parent = os.path.dirname(harness_dir)
    for p in (parent, harness_dir):
        if p not in sys.path:
            sys.path.insert(0, p)

    from agent_harness import get_llm  # type: ignore

    llm = get_llm(catalog_model)
    from langchain_core.messages import HumanMessage, SystemMessage

    messages = [SystemMessage(content=system_prompt)]
    if user_content.strip():
        messages.append(HumanMessage(content=user_content))
    else:
        messages.append(HumanMessage(content="Execute the system prompt and produce the requested output."))

    resp = llm.invoke(messages)
    content = getattr(resp, "content", None)
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
            elif isinstance(block, str):
                parts.append(block)
        return "\n".join(parts).strip()
    return str(content or "").strip()


def _output_filename(output_modality: str, run_id: str, ext: str | None = None) -> str:
    if ext:
        return f"{run_id}.{ext.lstrip('.')}"
    ext_map = {"text": "txt", "image": "png", "audio": "wav", "video": "mp4"}
    return f"{run_id}.{ext_map.get(output_modality, 'bin')}"


def _parse_config_json(raw: str | None) -> dict[str, Any]:
    """Parse the UM ``config_json`` blob into a dict (tolerant of empty/invalid).

    Carries optional generation knobs consumed by ``generate_media`` —
    ``image_config`` (aspect_ratio/image_size), ``voice``, ``audio_format``.
    """
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _finalize_run(
    *,
    out_dir: str,
    run_id: str,
    um_id: str,
    catalog_model: str,
    ctx_agent: str,
    task_id: int | None,
    checked: list[dict[str, Any]],
    artifacts: list[dict[str, Any]],
) -> dict[str, Any]:
    """Write the run manifest (if enabled) and build the success payload.

    Shared by the text and media branches so the two cannot drift.
    """
    write_manifest = read_setup_value("utility_models", "write_manifest", "true").lower() == "true"
    if write_manifest:
        run_entry = {
            "run_id": run_id,
            "utility_model_id": um_id,
            "catalog_model": catalog_model,
            "run_context_agent": ctx_agent,
            "task_id": task_id,
            "artifacts": artifacts,
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }
        # Per-task manifest so concurrent tasks writing to the same output dir do
        # not clobber each other; manual (no-task) runs share manifest.json.
        fname = f"manifest-task-{task_id}.json" if task_id is not None else "manifest.json"
        manifest_path = os.path.join(out_dir, fname)
        # Append: each run adds an entry to the manifest's run history rather than
        # overwriting, so the file is a durable per-task record (newest last).
        manifest: dict[str, Any] = {"task_id": task_id, "runs": []}
        try:
            with open(manifest_path, encoding="utf-8") as mf:
                existing = json.load(mf)
            if isinstance(existing, dict) and isinstance(existing.get("runs"), list):
                manifest = existing
        except (OSError, ValueError):
            pass
        manifest["task_id"] = task_id
        manifest["runs"].append(run_entry)
        manifest["updated_at"] = run_entry["completed_at"]
        # Write atomically via a temp file + os.replace. The stable per-task
        # manifest name means a prior run may have left the file owned by a
        # DIFFERENT user (e.g. coa-owned artifacts vs a watchdog executor), so an
        # in-place open("w") truncate would EACCES. os.replace needs only
        # directory write permission, which every agi_agents member has in the
        # setgid output dir, so it works regardless of the old file's owner.
        tmp_path = os.path.join(out_dir, f".{fname}.{run_id}.tmp")
        try:
            with open(tmp_path, "w", encoding="utf-8") as mf:
                json.dump(manifest, mf, indent=2)
            # 0o664 + inherited agi_agents group (setgid dir) so the next run by
            # either watchdog or the owning agent can rewrite it.
            try:
                os.chmod(tmp_path, 0o664)
            except OSError:
                pass
            os.replace(tmp_path, manifest_path)
        except OSError:
            # Best-effort — never fail an otherwise-successful run on the manifest.
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    _log_run({
        "ok": True,
        "run_id": run_id,
        "um_id": um_id,
        "catalog_model": catalog_model,
        "agent": ctx_agent,
        "task_id": task_id,
        "output_dir": out_dir,
        "artifacts": [a.get("path") for a in artifacts],
    })

    return {
        "success": True,
        "run_id": run_id,
        "utility_model_id": um_id,
        "catalog_model": catalog_model,
        "run_context_agent": ctx_agent,
        "output_dir": out_dir,
        "input_files": [c["path"] for c in checked],
        "artifacts": artifacts,
        "task_id": task_id,
    }


def run_utility_model(
    um_id: str,
    *,
    output_dir: str | None = None,
    input_files: list[str] | None = None,
    vars_map: dict[str, Any] | None = None,
    task_id: int | None = None,
    context_agent: str | None = None,
    task_output_override: str | None = None,
    agent_home: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    um = get_utility_model(um_id)
    if not um:
        raise UtilityRunError("not_found", f"Utility Model '{um_id}' not found")
    if not um.get("enabled"):
        raise UtilityRunError("disabled", f"Utility Model '{um_id}' is disabled")

    catalog_model = (um.get("catalog_model") or "").strip()
    cat = load_catalog()
    entry = catalog_entry_for_model(catalog_model, cat)
    if not entry or not entry.get("enabled"):
        raise UtilityRunError("invalid_model", f"Catalog model '{catalog_model}' not found or disabled")

    output_modality = (um.get("output_modality") or "text").strip().lower()
    ctx_agent = (context_agent or um.get("run_as_agent") or "coa").strip()

    paths = list(input_files or [])
    ok, err, checked = validate_input_files(catalog_model, paths)
    if not ok:
        raise UtilityRunError("input_invalid", err)

    out_dir = _resolve_output_dir(
        um,
        output_dir=output_dir,
        task_output_override=task_output_override,
        context_agent=ctx_agent,
        agent_home=agent_home,
    )

    run_id = f"util-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
    lock = _acquire_run_lock(task_id, um_id)
    if lock is None:
        raise UtilityRunError("running", "A utility run is already in progress for this task/model")

    if dry_run:
        _release_run_lock(lock)
        return {
            "success": True,
            "dry_run": True,
            "utility_model_id": um_id,
            "catalog_model": catalog_model,
            "run_context_agent": ctx_agent,
            "output_dir": out_dir,
            "input_files": [c["path"] for c in checked],
            "output_modality": output_modality,
        }

    try:
        system_prompt = _substitute_vars(um.get("system_prompt") or "", vars_map)

        from model_drivers.output import get_output_driver, has_real_driver

        if output_modality in ("image", "audio", "video"):
            outs = {x.strip() for x in (entry.get("output_modalities") or "").split(",") if x.strip()}
            if output_modality not in outs:
                raise UtilityRunError(
                    "output_mismatch",
                    f"Catalog '{catalog_model}' does not declare output_modality '{output_modality}'",
                )
            if not has_real_driver(output_modality):
                # Registry dispatch — the stub driver raises 'driver_pending' (video has
                # no output model and uses a separate async API).
                get_output_driver(output_modality)()

            # Real media generation (image/audio over chat-completions). This branch
            # MUST early-return — it must not fall through to the text path below.
            from harness.generation import generate_media

            data, ext, mime, transcript = generate_media(
                catalog_model,
                output_modality,
                prompt=system_prompt,
                input_files=checked,
                config=_parse_config_json(um.get("config_json")),
            )
            fname = _output_filename(output_modality, run_id, ext)
            artifact_path = os.path.join(out_dir, fname)
            get_output_driver(output_modality)(artifact_path, data)

            ok_out, out_err = validate_output_artifact(catalog_model, artifact_path, output_modality)
            if not ok_out:
                raise UtilityRunError("output_invalid", out_err)

            artifact = {
                "path": artifact_path,
                "modality": output_modality,
                "ext": ext,
                "bytes": os.path.getsize(artifact_path),
                "mime": mime,
            }
            if transcript:
                artifact["transcript"] = transcript

            return _finalize_run(
                out_dir=out_dir,
                run_id=run_id,
                um_id=um_id,
                catalog_model=catalog_model,
                ctx_agent=ctx_agent,
                task_id=task_id,
                checked=checked,
                artifacts=[artifact],
            )

        # ── Text output — chat or generation catalog keys. ──
        user_parts: list[str] = []
        for item in checked:
            if item["modality"] == FILE_MODALITY:
                user_parts.append(f"--- file: {item['path']} ---\n{_read_file_text(item['path'])}")

        text_out = _invoke_chat_model(catalog_model, system_prompt, "\n\n".join(user_parts))
        if not text_out:
            raise UtilityRunError("empty_response", "Model returned empty text")

        fname = _output_filename("text", run_id)
        artifact_path = os.path.join(out_dir, fname)
        get_output_driver("text")(artifact_path, text_out)

        ok_out, out_err = validate_output_artifact(catalog_model, artifact_path, "text")
        if not ok_out:
            raise UtilityRunError("output_invalid", out_err)

        artifacts = [{
            "path": artifact_path,
            "modality": "text",
            "ext": "txt",
            "bytes": os.path.getsize(artifact_path),
            "mime": "text/plain",
        }]

        return _finalize_run(
            out_dir=out_dir,
            run_id=run_id,
            um_id=um_id,
            catalog_model=catalog_model,
            ctx_agent=ctx_agent,
            task_id=task_id,
            checked=checked,
            artifacts=artifacts,
        )
    except UtilityRunError as e:
        _log_run({
            "ok": False, "code": e.code, "error": e.message,
            "run_id": run_id, "um_id": um_id, "catalog_model": catalog_model,
            "agent": ctx_agent, "task_id": task_id,
        })
        raise
    except Exception as e:  # noqa: BLE001 - durable record before propagating
        _log_run({
            "ok": False, "code": "unexpected", "error": str(e),
            "run_id": run_id, "um_id": um_id, "catalog_model": catalog_model,
            "agent": ctx_agent, "task_id": task_id,
        })
        raise
    finally:
        _stamp_last_run()
        _release_run_lock(lock)
