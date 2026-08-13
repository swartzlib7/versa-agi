"""Ephemeral per-spawn model routing — pool build, preferred-map resolve, COA guards."""
from __future__ import annotations

import db_connect


import json
import os
import re
import sqlite3
import sys


def _ensure_model_catalog_on_path() -> None:
    """Make `model_catalog` importable across all runtime layouts.

    `model_catalog.py` ships in the core-infra root. The harness package, however,
    may be deployed under /usr/local/lib/versa-agi/harness/ where model_catalog is
    NOT copied, while the canonical copy lives in the deployed core-infra tree
    (VERSA_CORE_INFRA / /home/watchdog/core-infra) or the dev source tree. Probe
    the known locations and prepend the first that actually contains the module.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.dirname(here),                 # parent of harness/ (dev + lib layouts)
        os.environ.get("VERSA_CORE_INFRA", ""),
        "/home/watchdog/core-infra",
        "/usr/local/lib/versa-agi",
    ]
    for cand in candidates:
        if cand and os.path.isfile(os.path.join(cand, "model_catalog.py")):
            if cand not in sys.path:
                sys.path.insert(0, cand)
            return
    # Preserve prior behavior if the module wasn't located on disk.
    parent = os.path.dirname(here)
    if parent not in sys.path:
        sys.path.insert(0, parent)


_ensure_model_catalog_on_path()
from model_catalog import (
    WORK_MODALITIES,
    load_catalog,
    load_providers,
    provider_is_enabled,
    read_setup_value,
    resolve_local_provider,
)


def _agents_db() -> str:
    return os.environ.get("AGICTL_AGENTS_DB", "/var/lib/versa-agi/agents.db")


# COA will implement in this cycle (not merely discuss coding for another agent)
_COA_DIRECT_CODE = re.compile(
    r"(?:\bI(?:'ll| will)\b|\blet me\b).{0,48}\b(?:fix|implement|edit|write|patch|refactor|code)\b|"
    r"\b(?:edit|modify|update|fix|patch|implement|refactor)\s+(?:the\s+)?"
    r"(?:file|code|script|function|module|handler|cli)\b|"
    r"[`'\"]?[\w./-]+\.(?:py|ts|tsx|js|sh|md|json|yaml|ini)[`'\"]?|"
    r"\b(?:apply|run)\s+(?:the\s+)?(?:fix|patch|diff)\b",
    re.I | re.S,
)


def coa_will_code_directly(wake_prompt: str) -> bool:
    """True when the wake prompt indicates COA will write/edit code this cycle."""
    return bool(_COA_DIRECT_CODE.search(wake_prompt or ""))


def clamp_coa_work_modality(modality: str | None, wake_prompt: str) -> str | None:
    """Downgrade code → balanced for COA unless the wake prompt signals direct implementation."""
    if not modality or modality != "code":
        return modality
    if coa_will_code_directly(wake_prompt):
        return "code"
    return "balanced"


def load_model_feedback() -> list[dict]:
    db = _agents_db()
    if not os.path.isfile(db):
        return []
    try:
        conn = db_connect.connect_compat(db, timeout=5)
        conn.row_factory = sqlite3.Row
        rows = [dict(r) for r in conn.execute("SELECT * FROM model_feedback").fetchall()]
        conn.close()
        return rows
    except Exception:
        return []


def _provider_available(provider: str, mclass: str) -> bool:
    if mclass == "local":
        if read_setup_value("local_ai", "enabled", "false").lower() != "true":
            return False
        if provider not in ("ollama", "llamacpp"):
            return False
        providers = load_providers()
        if not provider_is_enabled(provider, providers):
            return False
        try:
            active = resolve_local_provider()
        except Exception:
            active = resolve_local_provider(
                read_setup_value("local_ai", "gpu_backend", "standard"),
            )
        return provider == active
    if mclass == "cloud":
        return True
    if mclass == "third_party":
        return read_setup_value("third_party", f"{provider}_enabled", "false").lower() == "true"
    return True


def build_routing_context(
    *,
    agent_name: str,
    assigned_model: str,
    routing_enabled: bool,
    routing_mode: str | None = None,
    required_input_modalities: list[str] | None = None,
) -> dict | None:
    if not routing_enabled:
        return None

    mode = (routing_mode or read_setup_value("agent", "model_routing_mode", "pool")).strip().lower()
    if mode not in ("pool", "preferred"):
        mode = "pool"

    cat = load_catalog()
    providers = load_providers()
    assigned_meta = cat.get(assigned_model, {})
    assigned_work = assigned_meta.get("work_modality", "balanced")
    req_inputs = required_input_modalities or ["text"]

    feedback = [
        {
            "catalog_key": r["catalog_key"],
            "work_modality": r.get("work_modality"),
            "preference": r["preference"],
            "task_hint": r.get("task_hint"),
            "note": r.get("note"),
        }
        for r in load_model_feedback()
    ]

    is_coa = agent_name == "coa"

    def _candidate_row(key: str, m: dict) -> dict:
        return {
            "key": key,
            "work_modality": m.get("work_modality", "balanced"),
            "input": m.get("input_modalities", "text"),
            "output": m.get("output_modalities", "text"),
            "label": m.get("label", key),
        }

    def _input_ok(key: str, m: dict) -> bool:
        return _model_supports_inputs(key, m, req_inputs, cat, providers)

    def _eligible(key: str, m: dict) -> bool:
        if not m.get("enabled") or not m.get("router_eligible"):
            return False
        if is_coa and not m.get("coa"):
            return False
        if not _provider_available(m.get("provider", ""), m.get("class", "")):
            return False
        if not _input_ok(key, m):
            return False
        return True

    if mode == "preferred":
        return {
            "mode": "preferred",
            "assigned_model": assigned_model,
            "assigned_work_modality": assigned_work,
            "required_input_modalities": req_inputs,
            "preferred_map": {
                wm: read_setup_value("model_routing", wm, "")
                for wm in WORK_MODALITIES
            },
            "pu_feedback": feedback,
        }

    candidates = []
    for key, m in sorted(cat.items()):
        if key == assigned_model:
            continue
        if _eligible(key, m):
            candidates.append(_candidate_row(key, m))

    if not candidates:
        return None

    return {
        "mode": "pool",
        "assigned_model": assigned_model,
        "assigned_work_modality": assigned_work,
        "required_input_modalities": req_inputs,
        "candidates": candidates,
        "pu_feedback": feedback,
    }


def _model_supports_inputs(
    key: str,
    model: dict,
    required_input_modalities: list[str],
    catalog: dict,
    providers: dict,
) -> bool:
    inputs = {
        item.strip()
        for item in model.get("input_modalities", "text").split(",")
        if item.strip()
    }
    from model_drivers.registry import resolve_model_driver

    return all(
        modality in inputs
        and (
            modality == "text"
            or resolve_model_driver(
                key,
                "input",
                modality,
                catalog=catalog,
                providers=providers,
            )
            is not None
        )
        for modality in required_input_modalities
    )


def _validate_catalog_key(
    key: str | None,
    cat: dict,
    is_coa: bool,
    *,
    required_input_modalities: list[str] | None = None,
    providers: dict | None = None,
) -> str | None:
    if not key or key not in cat:
        return None
    m = cat[key]
    if not m.get("enabled"):
        return None
    if is_coa and not m.get("coa"):
        return None
    if required_input_modalities and not _model_supports_inputs(
        key,
        m,
        required_input_modalities,
        cat,
        providers or load_providers(),
    ):
        return None
    return key


def _pick_pool_by_work_modality(routing: dict, work_modality: str | None) -> str | None:
    """Fallback when triage classifies tier but omits recommended_model."""
    if not work_modality:
        return None
    candidates = routing.get("candidates") or []
    if not candidates:
        return None
    feedback = routing.get("pu_feedback") or []
    avoid = {
        f["catalog_key"]
        for f in feedback
        if f.get("preference") == "avoid"
        and (not f.get("work_modality") or f.get("work_modality") == work_modality)
    }
    prefer = [
        f["catalog_key"]
        for f in feedback
        if f.get("preference") == "prefer"
        and (not f.get("work_modality") or f.get("work_modality") == work_modality)
    ]
    matching = [c for c in candidates if c.get("work_modality") == work_modality and c["key"] not in avoid]
    if not matching:
        return None
    prefer_set = set(prefer)
    for c in matching:
        if c["key"] in prefer_set:
            return c["key"]
    return matching[0]["key"]


def resolve_execution_model(
    routing: dict | None,
    triage_result,
    assigned_model: str,
    agent_name: str = "coa",
    wake_prompt: str = "",
) -> tuple[str, str, str | None]:
    if not routing:
        return assigned_model, "none", None

    cat = load_catalog()
    providers = load_providers()
    is_coa = agent_name == "coa"
    work_modality = getattr(triage_result, "required_work_modality", None)
    recommended = getattr(triage_result, "recommended_model", None)

    if is_coa:
        clamped = clamp_coa_work_modality(work_modality, wake_prompt)
        if clamped != work_modality:
            work_modality = clamped
            # Pool pick was likely code-tier; re-resolve after orchestration clamp
            recommended = None
        elif coa_will_code_directly(wake_prompt) and work_modality in (None, "balanced", "reasoning"):
            # Triage under-classified direct implementation — still allow code routing
            work_modality = "code"

    def _validate(key: str | None) -> str | None:
        return _validate_catalog_key(
            key,
            cat,
            is_coa,
            required_input_modalities=routing.get("required_input_modalities") or ["text"],
            providers=providers,
        )

    mode = routing.get("mode", "pool")

    if mode == "preferred" and work_modality:
        pref = (routing.get("preferred_map") or {}).get(work_modality, "").strip()
        resolved = _validate(pref)
        if resolved and resolved != assigned_model:
            return resolved, "preferred", work_modality
        if recommended:
            allowed = {c["key"] for c in routing.get("candidates", [])}
            resolved = _validate(recommended)
            if resolved and (not allowed or resolved in allowed) and resolved != assigned_model:
                return resolved, "pool", work_modality
        fallback = _validate(_pick_pool_by_work_modality(routing, work_modality))
        if fallback and fallback != assigned_model:
            return fallback, "pool", work_modality
        return assigned_model, "none", work_modality

    if recommended:
        allowed = {c["key"] for c in routing.get("candidates", [])}
        resolved = _validate(recommended)
        if resolved and resolved in allowed and resolved != assigned_model:
            return resolved, "pool", work_modality

    if mode == "pool" and work_modality:
        fallback = _validate(_pick_pool_by_work_modality(routing, work_modality))
        if fallback and fallback != assigned_model:
            return fallback, "pool", work_modality

    return assigned_model, "none", work_modality


def detect_required_input_modalities(attachment_paths: list[str] | None) -> list[str]:
    mods = {"text"}
    if not attachment_paths:
        return sorted(mods)
    for path in attachment_paths:
        ext = os.path.splitext(path)[1].lower()
        if ext in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".heic"):
            mods.add("image")
        elif ext in (".mp3", ".wav", ".ogg", ".m4a", ".flac", ".aac"):
            mods.add("audio")
        elif ext in (".mp4", ".mov", ".avi", ".webm", ".mkv"):
            mods.add("video")
    return sorted(mods)


def read_output_routing_map() -> dict[str, str]:
    """Preferred generation model per output modality (from setup.ini [output_routing])."""
    from model_catalog import read_output_routing_map as _read_map
    return _read_map()


def resolve_output_model(output_modality: str, agent_name: str = "coa") -> str | None:
    """Resolve preferred catalog key for a generation output type (utility runner, Phase F)."""
    from model_catalog import OUTPUT_DELIVERY_MODALITIES, load_catalog, read_output_routing_map

    om = (output_modality or "").strip().lower()
    if om not in OUTPUT_DELIVERY_MODALITIES:
        return None
    key = (read_output_routing_map().get(om) or "").strip()
    if not key:
        return None
    cat = load_catalog()
    m = cat.get(key)
    if not m or not m.get("enabled"):
        return None
    outputs = {x.strip() for x in m.get("output_modalities", "text").split(",") if x.strip()}
    if om not in outputs:
        return None
    from model_drivers.registry import resolve_model_driver

    if resolve_model_driver(key, "output", om, catalog=cat) is None:
        return None
    return key


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Build spawn routing JSON for lifeline")
    parser.add_argument("--agent", required=True)
    parser.add_argument("--assigned-model", required=True)
    parser.add_argument("--routing-enabled", type=int, default=0)
    parser.add_argument("--routing-mode", default="")
    parser.add_argument("--attachments", default="", help="Comma-separated attachment paths")
    parser.add_argument(
        "--attachments-file",
        default="",
        help="Newline-separated attachment paths (preferred — lifeline uses this)",
    )
    args = parser.parse_args()

    paths: list[str] = []
    if args.attachments_file and os.path.isfile(args.attachments_file):
        with open(args.attachments_file, "r", encoding="utf-8") as af:
            paths = [ln.strip() for ln in af if ln.strip()]
    elif args.attachments:
        paths = [p for p in args.attachments.split(",") if p.strip()]
    ctx = build_routing_context(
        agent_name=args.agent,
        assigned_model=args.assigned_model,
        routing_enabled=bool(args.routing_enabled),
        routing_mode=args.routing_mode or None,
        required_input_modalities=detect_required_input_modalities(paths),
    )
    print(json.dumps(ctx) if ctx else "")
