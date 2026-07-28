"""Platform-specific safe zone management for captions, watermarks and overlays."""

from __future__ import annotations

from typing import Any, Dict, Tuple

SAFE_ZONES: Dict[str, Dict[str, float]] = {
    "youtube_shorts": {"top": 0.10, "bottom": 0.20, "left": 0.05, "right": 0.05},
    "youtube_longform": {"top": 0.05, "bottom": 0.10, "left": 0.05, "right": 0.05},
    "tiktok": {"top": 0.15, "bottom": 0.25, "left": 0.05, "right": 0.05},
    "instagram_reels": {"top": 0.10, "bottom": 0.20, "left": 0.05, "right": 0.05},
}


class SafeZoneManager:
    """Computes pixel-space safe areas for a given platform and frame size."""

    def __init__(self, platform: str = "youtube_shorts", width: int = 1080, height: int = 1920):
        self.platform = platform if platform in SAFE_ZONES else "youtube_shorts"
        self.width = width
        self.height = height

    @classmethod
    def for_aspect(cls, aspect_ratio: str, platform: str | None = None) -> "SafeZoneManager":
        """Build a manager from an aspect ratio, choosing a sensible platform."""
        if aspect_ratio == "9:16":
            return cls(platform or "youtube_shorts", 1080, 1920)
        return cls(platform or "youtube_longform", 1920, 1080)

    @property
    def margins(self) -> Dict[str, float]:
        """Fractional margins for the current platform."""
        return SAFE_ZONES[self.platform]

    def box(self) -> Dict[str, int]:
        """Return the safe rectangle in pixels."""
        margins = self.margins
        left = int(self.width * margins["left"])
        right = int(self.width * (1 - margins["right"]))
        top = int(self.height * margins["top"])
        bottom = int(self.height * (1 - margins["bottom"]))
        return {
            "left": left,
            "top": top,
            "right": right,
            "bottom": bottom,
            "width": right - left,
            "height": bottom - top,
        }

    def caption_band(self) -> Tuple[int, int]:
        """Return the vertical (top, bottom) band reserved for captions."""
        box = self.box()
        band_height = int(self.height * 0.18)
        top = box["bottom"] - band_height
        return top, box["bottom"]

    def caption_position(self, position: str, text_height: int) -> Tuple[str, int]:
        """Return a MoviePy-compatible position for a caption of a given height."""
        box = self.box()
        if position == "top":
            return ("center", box["top"])
        if position == "center":
            return ("center", int((self.height - text_height) / 2))
        return ("center", box["bottom"] - text_height)

    def clamp(self, x: int, y: int, element_width: int, element_height: int) -> Tuple[int, int]:
        """Clamp an element's top-left corner into the safe box."""
        box = self.box()
        x = max(box["left"], min(x, box["right"] - element_width))
        y = max(box["top"], min(y, box["bottom"] - element_height))
        return x, y

    def watermark_region(self, padding: float = 0.05) -> Dict[str, int]:
        """Safe region for the animated watermark, excluding the caption band."""
        left = int(self.width * padding)
        right = int(self.width * (1 - padding))
        top = int(self.height * padding)
        caption_top, _ = self.caption_band()
        bottom = max(top + int(self.height * 0.15), caption_top - int(self.height * 0.03))
        return {
            "left": left,
            "top": top,
            "right": right,
            "bottom": bottom,
            "width": right - left,
            "height": bottom - top,
        }

    def as_dict(self) -> Dict[str, Any]:
        """Serializable description of the current safe zone setup."""
        return {
            "platform": self.platform,
            "frame": [self.width, self.height],
            "margins": self.margins,
            "box": self.box(),
        }
