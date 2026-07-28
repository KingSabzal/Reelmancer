"""Pillow-based text rasterizer.

MoviePy's TextClip requires an ImageMagick installation, which is a fragile extra
dependency on Windows. Rendering the text with Pillow keeps the system lightweight
and removes that requirement completely.
"""

from __future__ import annotations

import logging
import os
from typing import List, Optional, Tuple

from utility.core import compat  # noqa: F401  (restores Pillow constants for MoviePy)
import numpy as np
from PIL import Image, ImageDraw, ImageFont

LOGGER = logging.getLogger("text_renderer")
if not LOGGER.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("[%(name)s] %(message)s"))
    LOGGER.addHandler(_h)
LOGGER.setLevel(logging.INFO)

FALLBACK_FONTS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
]


def _cached_fonts() -> List[str]:
    """Fonts the caption renderer already downloaded into assets/fonts.

    Without this the renderer only looked at a handful of system paths. A bare
    Linux container has none of them, so captions fell back to Pillow's bitmap
    font and came out pixelated, even though usable TrueType files were sitting
    in the project's own font cache.
    """
    try:
        from utility.core.paths import FONT_CACHE_DIR

        if not os.path.isdir(FONT_CACHE_DIR):
            return []
        names = sorted(
            name for name in os.listdir(FONT_CACHE_DIR)
            if name.lower().endswith((".ttf", ".otf"))
        )
        # Bold reads better on video, so prefer it when both exist.
        names.sort(key=lambda name: 0 if "bold" in name.lower() else 1)
        return [os.path.join(FONT_CACHE_DIR, name) for name in names]
    except Exception:  # noqa: BLE001 - font discovery must never break a render
        return []


def _system_fonts() -> List[str]:
    """Any TrueType font installed on this machine, wherever it lives."""
    found: List[str] = []
    for directory in (
        "/usr/share/fonts", "/usr/local/share/fonts",
        os.path.expanduser("~/.fonts"), "C:/Windows/Fonts",
        "/System/Library/Fonts", "/Library/Fonts",
    ):
        if not os.path.isdir(directory):
            continue
        try:
            for root, _dirs, files in os.walk(directory):
                for name in files:
                    if name.lower().endswith((".ttf", ".otf")):
                        found.append(os.path.join(root, name))
                if len(found) > 40:
                    break
        except OSError:
            continue
        if found:
            break
    found.sort(key=lambda path: 0 if "bold" in os.path.basename(path).lower() else 1)
    return found


def load_font(font_path: Optional[str], size: int) -> ImageFont.FreeTypeFont:
    """Load a TrueType font, searching every place one might exist."""
    candidates = (
        ([font_path] if font_path else [])
        + _cached_fonts()          # fonts this project downloaded
        + FALLBACK_FONTS           # the usual system locations
        + _system_fonts()          # anything else installed
    )
    for candidate in candidates:
        if not candidate:
            continue
        try:
            return ImageFont.truetype(candidate, size)
        except (OSError, ValueError):
            continue
    LOGGER.warning(
        "No TrueType font found anywhere; captions will use Pillow's bitmap font "
        "and will look pixelated. On Debian or Ubuntu: apt-get install fonts-dejavu-core"
    )
    return ImageFont.load_default()


def hex_to_rgba(color: str, alpha: int = 255) -> Tuple[int, int, int, int]:
    """Convert a #RRGGBB or #RRGGBBAA string to an RGBA tuple."""
    color = (color or "#FFFFFF").strip()
    if not color.startswith("#"):
        named = {"white": "#FFFFFF", "black": "#000000", "transparent": "#00000000"}
        color = named.get(color.lower(), "#FFFFFF")
    value = color.lstrip("#")
    if len(value) == 8:
        r, g, b, a = (int(value[i : i + 2], 16) for i in (0, 2, 4, 6))
        return (r, g, b, a)
    if len(value) != 6:
        return (255, 255, 255, alpha)
    r, g, b = (int(value[i : i + 2], 16) for i in (0, 2, 4))
    return (r, g, b, alpha)


def wrap_text(text: str, font: ImageFont.FreeTypeFont, max_width: int) -> List[str]:
    """Greedy word wrap to fit a pixel width."""
    words = text.split()
    if not words:
        return []
    lines: List[str] = []
    current = words[0]
    for word in words[1:]:
        candidate = f"{current} {word}"
        if font.getbbox(candidate)[2] <= max_width:
            current = candidate
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def render_text_rgba(
    text: str,
    font_path: Optional[str],
    font_size: int,
    color: str = "#FFFFFF",
    stroke_color: str = "#000000",
    stroke_width: int = 0,
    max_width: Optional[int] = None,
    background: Optional[str] = None,
    padding: int = 12,
    line_spacing: float = 1.12,
    align: str = "center",
) -> np.ndarray:
    """Rasterize text to an RGBA numpy array with an optional stroke and background."""
    font = load_font(font_path, font_size)
    lines = wrap_text(text, font, max_width) if max_width else [text]
    if not lines:
        lines = [" "]

    widths, heights = [], []
    for line in lines:
        box = font.getbbox(line, stroke_width=stroke_width)
        widths.append(box[2] - box[0])
        heights.append(box[3] - box[1])

    line_height = int(max(heights or [font_size]) * line_spacing)
    width = max(widths or [font_size]) + padding * 2 + stroke_width * 2
    height = line_height * len(lines) + padding * 2 + stroke_width * 2

    image = Image.new("RGBA", (max(width, 1), max(height, 1)), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    if background:
        if background.startswith("rgba"):
            parts = background[background.index("(") + 1 : background.index(")")].split(",")
            r, g, b = (int(float(p)) for p in parts[:3])
            a = int(float(parts[3]) * 255) if len(parts) > 3 else 255
            fill = (r, g, b, a)
        else:
            fill = hex_to_rgba(background)
        draw.rounded_rectangle([0, 0, width - 1, height - 1], radius=int(font_size * 0.22), fill=fill)

    text_color = hex_to_rgba(color)
    outline = hex_to_rgba(stroke_color)
    y = padding + stroke_width
    for index, line in enumerate(lines):
        line_width = widths[index]
        if align == "left":
            x = padding + stroke_width
        elif align == "right":
            x = width - line_width - padding - stroke_width
        else:
            x = (width - line_width) // 2
        draw.text(
            (x, y),
            line,
            font=font,
            fill=text_color,
            stroke_width=stroke_width,
            stroke_fill=outline if stroke_width else None,
        )
        y += line_height
    return np.array(image)


def make_text_clip(
    text: str,
    font_path: Optional[str],
    font_size: int,
    color: str = "#FFFFFF",
    stroke_color: str = "#000000",
    stroke_width: int = 0,
    max_width: Optional[int] = None,
    background: Optional[str] = None,
):
    """Return a transparent MoviePy ImageClip containing the rendered text."""
    from moviepy.editor import ImageClip

    array = render_text_rgba(
        text,
        font_path,
        font_size,
        color=color,
        stroke_color=stroke_color,
        stroke_width=stroke_width,
        max_width=max_width,
        background=background,
    )
    clip = ImageClip(array[:, :, :3])
    mask = ImageClip(array[:, :, 3] / 255.0, ismask=True)
    return clip.set_mask(mask)
