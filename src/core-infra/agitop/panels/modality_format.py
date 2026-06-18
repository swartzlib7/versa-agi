"""Shared modality label formatting for agitop model tables and forms."""

MODALITY_SYMBOLS = {
    "text": "📝",
    "image": "🖼",
    "audio": "🔊",
    "video": "🎬",
}
MODALITY_ORDER = ("text", "image", "audio", "video")


def format_modality_labels(csv: str) -> str:
    """Render modalities as icon + name (e.g. 📝 text, 🖼 image)."""
    mods = {m.strip().lower() for m in (csv or "text").split(",") if m.strip()}
    parts = [f"{MODALITY_SYMBOLS[m]} {m}" for m in MODALITY_ORDER if m in mods]
    return ", ".join(parts) if parts else "—"


def format_io_modalities(input_csv: str, output_csv: str) -> str:
    """Render input → output modalities with icons on both sides."""
    inp = format_modality_labels(input_csv)
    out = format_modality_labels(output_csv)
    return f"{inp} → {out}"
