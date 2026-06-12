# ─────────────────────────────────────────────────────
# Versa AGi — Model Context Window Registry
#
# Reads context window data from the unified models.ini [catalog]
# (overlaying the pipeline-owned [context_windows] for registry-added locals).
# Falls back to a built-in map when models.ini is unavailable.
# Used by: agent_harness.py, agitop, agictl
# ─────────────────────────────────────────────────────

import configparser
import os

# Canonical models.ini path (deployed alongside setup.ini)
# Dev fallback: src/models.ini (next to src/setup.ini)
_MODELS_INI_PATHS = [
    "/etc/versa-agi/models.ini",
    os.path.join(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))), "models.ini"),
]

# Built-in fallback map — used only when models.ini is unavailable.
# Kept in sync with models.ini [context_windows] section.
_FALLBACK_CONTEXT_MAP: dict[str, tuple[int, int]] = {
    # Cloud models (context managed server-side, max = actual limit for trimmer)
    # Generic prefixes are conservative — per-model entries in models.ini win.
    "gemini":       (0, 1000000),
    "grok":         (0, 1000000),
    "gpt":          (0, 131072),
    "claude":       (0, 200000),
    # Qwen 3
    "qwen3:32b":    (32768, 131072),
    "qwen3.6:35b":  (32768, 131072),
    "qwen3:30b":    (32768, 131072),
    "qwen3:8b":     (16384, 131072),
    "qwen3:4b":     (8192, 32768),
    # Gemma 4
    "gemma4:26b":   (32768, 262144),
    "gemma4:e4b":   (32768, 131072),
    "gemma4:e2b":   (16384, 131072),
    "gemma4:31b":   (32768, 262144),
    # Llama
    "llama3.1":     (16384, 131072),
    "llama3.2":     (16384, 131072),
    "llama4":       (32768, 131072),
    # DeepSeek
    "deepseek-r1":  (32768, 131072),
}

DEFAULT_NUM_CTX: int = 4096  # Ollama default when model not in map

# Picklist increments (tokens) — filtered per-model to max_ctx
NUM_CTX_OPTIONS: list[tuple[int, str]] = [
    (4096,    "4K"),
    (8192,    "8K"),
    (16384,   "16K"),
    (32768,   "32K"),
    (65536,   "64K"),
    (131072,  "128K"),
    (262144,  "256K"),
    (524288,  "512K"),
    (1048576, "1M"),
    (2097152, "2M"),
]


def _load_context_map() -> dict[str, tuple[int, int]]:
    """Load context window map from models.ini.

    Edition 2.x: the unified [catalog]/[catalog_custom] sections are the
    authoritative source (ctx_recommended,ctx_max per row). The pipeline-owned
    [context_windows] section is overlaid *underneath* — it fills in keys not in
    the catalog (e.g. SYCL-registry-added local models) but never overrides a
    catalog row, so dashboard/CLI edits always win. Falls back to the built-in
    map if models.ini is unavailable / yields nothing.
    """
    ini = configparser.ConfigParser(delimiters=('=',))
    for path in _MODELS_INI_PATHS:
        if os.path.exists(path):
            ini.read(path)
            break

    result: dict[str, tuple[int, int]] = {}

    # Base layer: legacy / registry-owned [context_windows] (fills gaps)
    if ini.has_section("context_windows"):
        for key, value in ini.items("context_windows"):
            try:
                parts = value.strip().split(",")
                if len(parts) == 2:
                    result[key.strip()] = (int(parts[0].strip()), int(parts[1].strip()))
            except (ValueError, IndexError):
                continue

    # Authoritative layer: unified catalog (wins over the base layer)
    for section in ("catalog", "catalog_custom"):
        if not ini.has_section(section):
            continue
        for key, raw in ini.items(section):
            parts = raw.split("|")
            if len(parts) < 7:
                continue
            try:
                rec = int(parts[4].strip() or "0")
                mx = int(parts[5].strip() or "0")
            except ValueError:
                continue
            if mx > 0:
                result[key.strip()] = (rec, mx)

    return result if result else _FALLBACK_CONTEXT_MAP.copy()


# Module-level cache — loaded once on import
MODEL_CONTEXT_MAP: dict[str, tuple[int, int]] = _load_context_map()


def get_model_context(model_name: str) -> tuple[int, int]:
    """Return (recommended, max) context window for a model.

    Uses longest prefix match against MODEL_CONTEXT_MAP.
    Returns (DEFAULT_NUM_CTX, DEFAULT_NUM_CTX) for unknown models.
    Returns (0, 0) for cloud models (context managed server-side).
    """
    if not model_name:
        return (DEFAULT_NUM_CTX, DEFAULT_NUM_CTX)

    # Exact match first
    if model_name in MODEL_CONTEXT_MAP:
        return MODEL_CONTEXT_MAP[model_name]

    # Prefix match (longest wins)
    best_match = ""
    for key in MODEL_CONTEXT_MAP:
        if model_name.startswith(key) and len(key) > len(best_match):
            best_match = key

    if best_match:
        return MODEL_CONTEXT_MAP[best_match]

    return (DEFAULT_NUM_CTX, DEFAULT_NUM_CTX)


def get_server_ctx_ceiling() -> int | None:
    """Read the server's configured per-slot context size from setup.ini.

    For Intel/remote backends, sycl_ctx_size is the per-slot context ceiling.
    The server's --ctx-size is total (per_slot × parallel), but each agent
    request occupies one slot with at most sycl_ctx_size tokens.
    Returns None for standard (Ollama) backend where ctx is managed
    per-request dynamically.
    """
    import configparser
    setup_ini_paths = [
        "/etc/versa-agi/setup.ini",
        os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                     "setup.ini"),
    ]
    for path in setup_ini_paths:
        if os.path.exists(path):
            cfg = configparser.ConfigParser()
            cfg.read(path)
            backend = cfg.get("local_ai", "gpu_backend", fallback="standard")
            if backend in ("intel", "remote"):
                try:
                    return int(cfg.get("local_ai", "sycl_ctx_size", fallback="4096"))
                except ValueError:
                    return 4096
            return None  # Standard backend — no ceiling
    return None


def get_num_ctx_options(model_name: str, server_ctx_ceiling: int | None = None) -> list[tuple[str, int]]:
    """Return filtered picklist of (label, value) for a model's context window.

    Entries beyond the model's max are excluded.
    When server_ctx_ceiling is provided (Intel/remote), entries are also
    capped to the server's configured --ctx-size.
    Returns empty list for cloud models (context not applicable).
    """
    _, max_ctx = get_model_context(model_name)
    if max_ctx == 0:
        return []  # Cloud models — not applicable
    # Apply server ceiling if provided (Intel/remote backend)
    effective_max = min(max_ctx, server_ctx_ceiling) if server_ctx_ceiling else max_ctx
    return [(label, value) for value, label in NUM_CTX_OPTIONS if value <= effective_max]


def is_cloud_model(model_name: str) -> bool:
    """Check if a model is cloud-managed (context handled server-side)."""
    recommended, _ = get_model_context(model_name)
    return recommended == 0


# ── Trimmer Budget Constants ──
# HEADROOM is applied to the TOKEN window first (the unit the API enforces),
# then converted to chars with a deliberately conservative density.
#
# Why 3 chars/token (not 4): the June 2026 token-limit incident measured the
# real density of agent histories (tool-call JSON, ids, structured args) at
# ~3.35 chars/token (9.38 MB ↔ ~2.8M tokens). Converting at 4 chars/token
# produced a char budget that already equaled ~100% of the model's token
# window, silently consuming the entire headroom. 3 chars/token keeps the
# trimmed payload at or below the intended token fraction even for the
# densest tool-heavy content.
TRIMMER_HEADROOM = 0.80      # fraction of the model token window the trimmer may fill
TRIMMER_CHARS_PER_TOKEN = 3  # conservative density (measured worst case ~3.35)


def get_trimmer_char_limit(model_name: str, num_ctx: int = 0) -> int:
    """Return the character budget for pre_model_hook context trimming.

    The budget is computed in TOKENS first (window × headroom), then converted
    to chars at a conservative 3 chars/token:

      Local models with custom num_ctx: num_ctx × 0.80 × 3 chars/token.
      Cloud/local models from registry: max_tokens × 0.80 × 3 chars/token.
      Fallback: 96,000 chars (~32K tokens).

    NOTE: this is the raw model budget. The harness additionally subtracts the
    system prompt and tool schema sizes (which are sent with every request but
    are not part of the graph message state) before using it for trimming.
    """
    # Local model with explicit custom num_ctx
    if num_ctx and num_ctx > 0:
        return int(num_ctx * TRIMMER_HEADROOM * TRIMMER_CHARS_PER_TOKEN)

    # Cloud or local model — lookup max from registry
    _, max_tokens = get_model_context(model_name)
    if max_tokens and max_tokens > 0:
        return int(max_tokens * TRIMMER_HEADROOM * TRIMMER_CHARS_PER_TOKEN)

    # Unknown model — conservative fallback (~32K tokens)
    return 96_000

