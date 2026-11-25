"""Compatibility shim — shipped selection lives in models.ini [shipped_models]."""

from __future__ import annotations

from shipped_models import all_keys, keys_for_provider, load_offerings, recommended_pairs

COA_SHIPPED: list[tuple[str, dict[str, str]]] = [
    (label, keys) for _, label, keys in load_offerings()
]

__all__ = ["COA_SHIPPED", "all_keys", "keys_for_provider", "recommended_pairs"]
