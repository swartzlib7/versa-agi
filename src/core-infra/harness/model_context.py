# ─────────────────────────────────────────────────────
# Versa AGi — Model Context Window Registry
#
# Reads context window data from models.ini [context_windows].
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
    # Cloud models (context managed server-side)
    "gemini":       (0, 0),
    "grok":         (0, 0),
    "gpt":          (0, 0),
    "claude":       (0, 0),
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
    """Load context window map from models.ini [context_windows] section.

    Returns the parsed map, or the built-in fallback if models.ini
    is unavailable or the section is missing.
    """
    ini = configparser.ConfigParser(delimiters=('=',))
    for path in _MODELS_INI_PATHS:
        if os.path.exists(path):
            ini.read(path)
            break

    if not ini.has_section("context_windows"):
        return _FALLBACK_CONTEXT_MAP.copy()

    result: dict[str, tuple[int, int]] = {}
    for key, value in ini.items("context_windows"):
        try:
            parts = value.strip().split(",")
            if len(parts) == 2:
                recommended = int(parts[0].strip())
                max_ctx = int(parts[1].strip())
                result[key.strip()] = (recommended, max_ctx)
        except (ValueError, IndexError):
            continue

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
