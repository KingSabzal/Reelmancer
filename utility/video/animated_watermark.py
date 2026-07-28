"""Animated watermark: subtle, continuously moving, never overlapping the captions."""

from __future__ import annotations

import logging
import math
import random
from typing import Any, Dict, List, Tuple

from utility.core import compat  # noqa: F401  (restores Pillow constants for MoviePy)
from utility.video.safe_zone_manager import SafeZoneManager
from utility.video.text_renderer import make_text_clip

LOGGER = logging.getLogger("animated_watermark")
if not LOGGER.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("[%(name)s] %(message)s"))
    LOGGER.addHandler(_h)
LOGGER.setLevel(logging.INFO)

SPEED_FACTOR = {"slow": 0.5, "medium": 1.0, "fast": 1.8}


class AnimatedWatermark:
    """Creates a moving text/handle/logo overlay clip."""

    def __init__(self, settings: Dict[str, Any], frame_size: Tuple[int, int], platform: str = "youtube_shorts"):
        self.settings = settings or {}
        self.width, self.height = frame_size
        self.safe = SafeZoneManager(platform, self.width, self.height)
        self.random = random.Random(settings.get("seed", 2026))

    # ------------------------------------------------------------------
    def _waypoints(self, duration: float, element: Tuple[int, int]) -> List[Tuple[float, int, int]]:
        """Generate timed target positions inside the watermark safe region."""
        region = self.safe.watermark_region(self.settings.get("safe_zone_padding", 0.05))
        element_w, element_h = element
        interval = float(self.settings.get("change_interval", 5))
        interval = max(3.0, min(interval, 10.0))
        pattern = self.settings.get("movement_pattern", "random_smooth")

        max_x = max(region["left"], region["right"] - element_w)
        max_y = max(region["top"], region["bottom"] - element_h)

        points: List[Tuple[float, int, int]] = []
        steps = max(2, int(duration / interval) + 1)
        for index in range(steps + 1):
            t = min(index * interval, duration)
            if pattern == "circular":
                angle = 2 * math.pi * index / max(steps, 1)
                cx = (region["left"] + max_x) / 2
                cy = (region["top"] + max_y) / 2
                rx = (max_x - region["left"]) / 2
                ry = (max_y - region["top"]) / 2
                x = int(cx + rx * math.cos(angle))
                y = int(cy + ry * math.sin(angle))
            elif pattern == "diagonal":
                progress = (index % 2 == 0)
                x = region["left"] if progress else max_x
                y = region["top"] if progress else max_y
            else:  # random_smooth and random_jump
                x = self.random.randint(region["left"], max_x)
                y = self.random.randint(region["top"], max_y)
            points.append((t, x, y))
        return points

    def _position_function(self, points: List[Tuple[float, int, int]]):
        """Interpolate smoothly (or jump) between waypoints."""
        jump = self.settings.get("movement_pattern") == "random_jump"
        speed = SPEED_FACTOR.get(self.settings.get("movement_speed", "slow"), 0.5)

        def position(t):
            """Interpolated (x, y) for a given timestamp."""
            scaled = t * speed
            previous = points[0]
            for point in points:
                if point[0] <= scaled:
                    previous = point
                else:
                    if jump:
                        return (previous[1], previous[2])
                    span = max(point[0] - previous[0], 0.001)
                    ratio = (scaled - previous[0]) / span
                    eased = ratio * ratio * (3 - 2 * ratio)  # smoothstep
                    x = previous[1] + (point[1] - previous[1]) * eased
                    y = previous[2] + (point[2] - previous[2]) * eased
                    return (int(x), int(y))
            return (previous[1], previous[2])

        return position

    # ------------------------------------------------------------------
    def build_clip(self, duration: float):
        """Return a MoviePy clip for the watermark, or None when disabled."""
        if not self.settings.get("enabled", False) or duration <= 0:
            return None

        watermark_type = self.settings.get("type", "handle")
        opacity = float(self.settings.get("opacity", 0.25))
        opacity = max(0.10, min(opacity, 0.50))

        clip = None
        if watermark_type == "logo":
            path = self.settings.get("content", "")
            if not path:
                return None
            try:
                from moviepy.editor import ImageClip

                target_height = int(self.height * float(self.settings.get("font_size_ratio", 0.03)) * 2.2)
                clip = ImageClip(path).resize(height=target_height)
            except Exception as exc:  # noqa: BLE001
                LOGGER.info("Logo watermark failed to load: %s", exc)
                return None
        else:
            text = str(self.settings.get("content", "@YourChannel")).strip()
            if not text:
                return None
            font_size = max(14, int(self.height * float(self.settings.get("font_size_ratio", 0.03))))
            try:
                clip = make_text_clip(
                    text,
                    self.settings.get("font_path"),
                    font_size,
                    color=self.settings.get("color", "#FFFFFF"),
                    stroke_color=self.settings.get("stroke_color", "#000000"),
                    stroke_width=int(self.settings.get("stroke_width", 1)),
                )
            except Exception as exc:  # noqa: BLE001
                LOGGER.info("Text watermark failed: %s", exc)
                return None

        clip = clip.set_duration(duration).set_opacity(opacity)
        points = self._waypoints(duration, (clip.w or 100, clip.h or 40))
        clip = clip.set_position(self._position_function(points))
        LOGGER.info("Animated watermark enabled (%s, %d waypoints).", watermark_type, len(points))
        return clip


def default_watermark_settings() -> Dict[str, Any]:
    """Return the default watermark configuration block."""
    from utility.core.config_manager import DEFAULT_CONFIG

    return dict(DEFAULT_CONFIG["watermark"])
