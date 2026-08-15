"""Model Manager media-wizard helpers (ME-6). No Textual import."""

from __future__ import annotations

from model_media_remote import (  # noqa: F401
    build_gpu_host_agictl_cmd,
    read_local_ai_topology,
    read_tunnel_host,
    watchdog_ssh_key,
)

_LOCAL_CATALOG_PROVIDERS = ("ollama", "llamacpp", "local_media")

_CHAT_IMPORT_CLASSES = frozenset({"chat_gguf", "chat_vlm_mmproj"})
_MEDIA_IMPORT_CLASS = "media_pipeline"


def import_action_enabled(classification: str | None, *, busy: bool = False) -> dict[str, bool]:
    """Which Add Model import buttons are live after inspect (or recipe prefill).

    Inspect stays available so the operator can classify a pasted source.
    SYCL Import is chat/VLM only. Media Import is media_pipeline only.
    Unknown stays inspect-only (CLI would need --confirm-unknown).
    """
    if busy:
        return {"inspect": False, "sycl": False, "media": False}
    kind = (classification or "").strip()
    return {
        "inspect": True,
        "sycl": kind in _CHAT_IMPORT_CLASSES,
        "media": kind == _MEDIA_IMPORT_CLASS,
    }


def use_selected_enabled(*, fetching: bool, has_selection: bool) -> bool:
    """Hugging Face / provider picker: Use selected only when a row is current."""
    return (not fetching) and bool(has_selection)


def media_import_ui_block(topology: str) -> str | None:
    """CLI-on-this-host block. The wizard does not use this for client — it SSHs."""
    from model_media_ingest import topology_media_import_block_reason

    return topology_media_import_block_reason(topology)


def media_import_failure_hint(err: str) -> str:
    """Append a PU-facing hint when remote sudoers or SSH host keys fail."""
    text = (err or "").strip()
    low = text.lower()
    if "password is required" in low or "a terminal is required" in low:
        return (
            f"{text} GPU host sudoers must allow passwordless "
            "'agictl model media' (run setup.sh --update on the server)."
        )
    if "known_hosts" in low:
        return (
            f"{text} SSH could not write watchdog known_hosts; "
            "retry after --update on this laptop."
        )
    return text


def catalog_prefill_from_hf_recipe(model: dict | None) -> dict:
    """Map a ``model media recipes`` row into Add Model fields."""
    row = model or {}
    return {
        "key": row.get("id") or "",
        "label": row.get("label") or row.get("id") or "",
        "class": row.get("class") or "local",
        "provider": row.get("provider") or "local_media",
        "ctx_recommended": 0,
        "ctx_max": 0,
        "enabled": True,
        "coa": False,
        "work_modality": row.get("work_modality") or "local",
        "input_modalities": row.get("input_modalities") or "text",
        "output_modalities": row.get("output_modalities") or "image",
        "router_eligible": False,
        "hf_source": row.get("source") or "",
        "kind": row.get("kind") or "media",
    }


def media_form_prefill(payload: dict | None) -> dict:
    """Catalog fields after a media inspect. Does not create a Utility Profile."""
    bundle = (payload or {}).get("bundle") or {}
    from model_media_ingest import CATALOG_KEY_QWEN_IMAGE, CATALOG_LABELS

    key = (bundle.get("catalog_key_hint") or CATALOG_KEY_QWEN_IMAGE).strip()
    return {
        "key": key,
        "label": CATALOG_LABELS.get(key, f"{key} — Local sd-cli paint"),
        "class": "local",
        "provider": "local_media",
        "work_modality": "local",
        "input_modalities": "text",
        "output_modalities": "image",
        "router_eligible": False,
        "coa": False,
    }


def media_wizard_summary(payload: dict | None, *, topology: str | None = None) -> str:
    """Plain lines for the inspect result Static. Not a SYCL chat model."""
    data = payload or {}
    bundle = data.get("bundle") or {}
    kind = data.get("classification") or "unknown"
    src = data.get("source") or {}
    lines = [
        f"class={kind}  recipe={bundle.get('recipe') or '—'}  "
        f"provider={bundle.get('provider') or 'local_media'}",
        f"repo={src.get('repo_id') or ''}  store={bundle.get('store_dir') or ''}",
    ]
    for component in bundle.get("components") or []:
        role = component.get("role") or "?"
        filename = component.get("filename") or ""
        lines.append(f"  {role}: {filename}")
    for warning in bundle.get("warnings") or []:
        lines.append(f"warn: {warning}")
    if data.get("media_import_ok"):
        lines.append("Use Media Import (not SYCL). Not a chat model. No Utility Profile is created.")
    topo = (topology if topology is not None else read_local_ai_topology()).strip().lower()
    if topo == "client":
        lines.append(
            "Media Import runs on the GPU host over SSH. "
            "Paint from this laptop — the PNG is copied back here."
        )
    elif data.get("media_import_block"):
        lines.append(str(data["media_import_block"]))
    return "\n".join(lines)
