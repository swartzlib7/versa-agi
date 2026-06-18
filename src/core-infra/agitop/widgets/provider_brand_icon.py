"""Render small provider brand marks in the Textual terminal (half-block pixels)."""

from __future__ import annotations

import os
import subprocess
from functools import lru_cache
from pathlib import Path

try:
    from PIL import Image
except ImportError:
    Image = None  # type: ignore[misc, assignment]
from rich.segment import Segment
from rich.style import Style
from rich.text import Text
from textual.widget import Widget

# Catalog slug → shipped asset basename (without extension)
_ICON_BASENAME = {
    "google": "google-gemini-icon",
    "openai": "openai-blossom",
    "anthropic": "anthropic-claude-symbol",
    "xai": "xai",
    "openrouter": "openrouter",
}

# Brand styling for import chips / modal chrome (hex — Textual truecolor)
PROVIDER_BRAND = {
    "google": {
        "label": "Google",
        "border": "#4285F4",
        "background": "#4285F4 28%",
        "text": "#E8F0FE",
        "accent": "#34A853",
    },
    "openai": {
        "label": "OpenAI",
        "border": "#10A37F",
        "background": "#10A37F 25%",
        "text": "#E6FFF6",
        "accent": "#10A37F",
    },
    "anthropic": {
        "label": "Anthropic",
        "border": "#C15F3C",
        "background": "#C15F3C 30%",
        "text": "#FFF4EF",
        "accent": "#C15F3C",
    },
    "xai": {
        "label": "xAI",
        "border": "#FFFFFF",
        "background": "#1A1A1A 85%",
        "text": "#F5F5F5",
        "accent": "#FFFFFF",
    },
    "openrouter": {
        "label": "OpenRouter",
        "border": "#94A3B8",
        "background": "#94A3B8 22%",
        "text": "#F1F5F9",
        "accent": "#94A3B8",
    },
}


def _assets_dir() -> Path:
    here = Path(__file__).resolve().parent.parent / "assets" / "providers"
    if here.is_dir():
        return here
    # Dev fallback: repo docs bundle
    repo = Path(__file__).resolve().parents[5] / "docs" / "brand" / "providers"
    return repo if repo.is_dir() else here


def _resolve_png_path(slug: str) -> Path | None:
    base = _ICON_BASENAME.get(slug)
    if not base:
        return None
    assets = _assets_dir()
    png = assets / f"{base}.png"
    if png.is_file():
        return png
    svg = assets / f"{base}.svg"
    if not svg.is_file():
        # xAI ships as xai-grok-icon.png
        alt = assets / "xai-grok-icon.png"
        if slug == "xai" and alt.is_file():
            return alt
        return None
    try:
        subprocess.run(
            ["convert", "-background", "none", str(svg), "-resize", "32x32", str(png)],
            check=True,
            capture_output=True,
            timeout=10,
        )
        return png if png.is_file() else None
    except (OSError, subprocess.SubprocessError):
        return None


def _rgb_style(r: int, g: int, b: int, a: int = 255) -> Style:
    if a < 40:
        return Style()
    return Style(color=f"rgb({r},{g},{b})")


def _fallback_monogram(slug: str) -> tuple[Segment, ...]:
    """Colored initial when Pillow or assets are unavailable."""
    brand = PROVIDER_BRAND.get(slug, {})
    letter = (brand.get("label") or slug[:1] or "?")[0].upper()
    border = brand.get("border", "#888888")
    text = brand.get("text", "#FFFFFF")
    return (
        Segment(
            f" {letter} ",
            Style(bold=True, color=text, bgcolor=border),
        ),
    )


@lru_cache(maxsize=16)
def icon_segments(slug: str, width: int = 10) -> tuple[Segment, ...]:
    """Half-block raster of a provider icon (2 terminal rows tall)."""
    if Image is None:
        return _fallback_monogram(slug)

    path = _resolve_png_path(slug)
    if not path:
        return _fallback_monogram(slug)

    img = Image.open(path).convert("RGBA")
    # Half-block encoding: 2 terminal rows = 4 image pixel rows
    height = 4
    img = img.resize((width, height), Image.Resampling.LANCZOS)
    segments: list[Segment] = []
    for y in range(0, height, 2):
        for x in range(width):
            top = img.getpixel((x, y))
            bottom = img.getpixel((x, y + 1)) if y + 1 < height else (0, 0, 0, 0)
            ta, ba = top[3], bottom[3]
            if ta < 40 and ba < 40:
                segments.append(Segment(" "))
                continue
            if ta >= 40 and ba >= 40:
                segments.append(
                    Segment(
                        "▀",
                        Style(color=f"rgb({top[0]},{top[1]},{top[2]})",
                              bgcolor=f"rgb({bottom[0]},{bottom[1]},{bottom[2]})"),
                    )
                )
            elif ta >= 40:
                segments.append(Segment("▀", _rgb_style(*top[:3], ta)))
            else:
                segments.append(Segment("▄", _rgb_style(*bottom[:3], ba)))
        if y + 2 < height:
            segments.append(Segment("\n"))
    return tuple(segments)


def _segments_to_text(segments: tuple[Segment, ...]) -> Text:
    """Convert Rich segments to a Text renderable for Textual 8.x."""
    text = Text()
    for seg in segments:
        if seg.text == "\n":
            text.append("\n")
        else:
            text.append(seg.text, style=seg.style)
    return text


class ProviderBrandIcon(Widget):
    """Tiny truecolor provider mark (2 rows × ~10 cols)."""

    DEFAULT_CSS = """
    ProviderBrandIcon {
        width: auto;
        min-width: 10;
        height: 2;
        content-align: center middle;
        padding: 0 1;
    }
    """

    def __init__(self, slug: str, *, width: int = 10, **kwargs) -> None:
        super().__init__(**kwargs)
        self._slug = slug
        self._width = width

    def render(self):
        return _segments_to_text(icon_segments(self._slug, self._width))


def provider_brand_class(slug: str, prefix: str = "provider-brand") -> str:
    """CSS class token for a provider slug."""
    safe = (slug or "unknown").replace("/", "-")
    return f"{prefix}-{safe}"


def provider_import_button_label(slug: str, label: str = "") -> str:
    """Rich markup label for a branded import button."""
    brand = PROVIDER_BRAND.get(slug, {})
    text = brand.get("text", "#FFFFFF")
    caption = label or brand.get("label") or slug
    return f"[bold {text}]{caption}[/]"
