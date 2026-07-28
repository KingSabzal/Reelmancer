"""PatternInterruptEngine: a visual change every 3-5 seconds.

Effects are geometric only (zoom, pan, cut, kinetic typography, Ken Burns on stills).
No color grading, no LUT and no color filter is ever applied: the footage keeps its
natural look, as required.
"""

from __future__ import annotations

import logging
import random
from typing import Any, Dict, List, Optional, Tuple

LOGGER = logging.getLogger("pattern_interrupt")
if not LOGGER.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("[%(name)s] %(message)s"))
    LOGGER.addHandler(_h)
LOGGER.setLevel(logging.INFO)

MOTION_EFFECTS = ["zoom_in", "zoom_out", "pan_left", "pan_right", "pan_up", "pan_down", "cut"]


class PatternInterruptEngine:
    """Plans and applies pattern interrupts across the video timeline."""

    def __init__(self, interval: float = 4.0, enabled: bool = True, seed: Optional[int] = None):
        self.interval = max(3.0, min(float(interval), 5.0))
        self.enabled = enabled
        self.random = random.Random(seed)

    def plan(self, duration: float, cue_times: Optional[List[float]] = None) -> List[Dict[str, Any]]:
        """Return the interrupt schedule for the whole video."""
        if not self.enabled or duration <= 0:
            return []
        times: List[float] = []
        if cue_times:
            times.extend(t for t in cue_times if 0 < t < duration)
        current = self.interval
        while current < duration:
            times.append(current)
            current += self.interval
        times = sorted(set(round(t, 2) for t in times))

        # Enforce a minimum 2.5 second spacing so cuts never feel frantic.
        spaced: List[float] = []
        for time_point in times:
            if not spaced or time_point - spaced[-1] >= 2.5:
                spaced.append(time_point)

        schedule = []
        previous = 0.0
        for time_point in spaced:
            effect = self.random.choice(MOTION_EFFECTS)
            schedule.append(
                {
                    "start": previous,
                    "time": time_point,
                    "effect": effect,
                    "kinetic_text": self.random.random() < 0.25,
                }
            )
            previous = time_point
        LOGGER.info("Planned %d pattern interrupts over %.1fs.", len(schedule), duration)
        return schedule

    # ------------------------------------------------------------------
    def apply_motion(self, clip, effect: str, frame_size: Tuple[int, int]):
        """Apply a geometric motion effect to a clip. No color operations."""
        width, height = frame_size
        duration = max(clip.duration or 0.1, 0.1)

        if effect == "cut":
            return clip

        if effect in ("zoom_in", "zoom_out"):
            amount = 0.10
            if effect == "zoom_in":
                scale = lambda t: 1.0 + amount * (t / duration)  # noqa: E731
            else:
                scale = lambda t: 1.0 + amount * (1 - t / duration)  # noqa: E731
            return clip.resize(scale).set_position("center")

        # Pans: oversize slightly, then translate within the frame.
        moved = clip.resize(1.12)

        def position(t):
            """Translated (x, y) for a pan effect at a given timestamp."""
            progress = min(t / duration, 1.0)
            offset_x = int(width * 0.06 * (progress - 0.5) * 2)
            offset_y = int(height * 0.06 * (progress - 0.5) * 2)
            if effect == "pan_left":
                return (-offset_x - int(width * 0.06), "center")
            if effect == "pan_right":
                return (offset_x - int(width * 0.06), "center")
            if effect == "pan_up":
                return ("center", -offset_y - int(height * 0.06))
            return ("center", offset_y - int(height * 0.06))

        return moved.set_position(position)

    def ken_burns(self, image_clip, frame_size: Tuple[int, int], zoom_in: bool = True):
        """Ken Burns motion for a still image (no color modification)."""
        duration = max(image_clip.duration or 4.0, 0.1)
        if zoom_in:
            scale = lambda t: 1.05 + 0.12 * (t / duration)  # noqa: E731
        else:
            scale = lambda t: 1.17 - 0.12 * (t / duration)  # noqa: E731
        return image_clip.resize(scale).set_position("center")

    def kinetic_text_clip(
        self, text: str, start: float, duration: float, frame_size: Tuple[int, int]
    ):
        """Build a simple kinetic typography overlay for a pattern interrupt."""
        from utility.video.text_renderer import make_text_clip

        width, height = frame_size
        try:
            clip = make_text_clip(
                text.upper(),
                None,
                int(height * 0.045),
                color="#FFFFFF",
                stroke_color="#000000",
                stroke_width=2,
                max_width=int(width * 0.8),
            )
        except Exception as exc:  # noqa: BLE001
            LOGGER.info("Kinetic typography clip failed: %s", exc)
            return None
        clip = clip.set_start(start).set_duration(min(duration, 1.4))
        clip = clip.set_position(
            lambda t: ("center", int(height * 0.30 + 22 * max(0.0, 1 - t * 5)))
        )
        return clip

    def count_visual_changes(self, schedule: List[Dict[str, Any]], clip_count: int) -> int:
        """Total visual changes: scheduled interrupts plus underlying clip changes."""
        return len(schedule) + clip_count
