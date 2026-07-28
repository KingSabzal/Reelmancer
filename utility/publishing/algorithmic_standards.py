"""YouTube 2026 algorithmic targets and synthetic media disclosure helpers."""

from __future__ import annotations

from typing import Any, Dict

SHORTS_MAX_DURATION = 120  # seconds; below this the video is a vertical Short

TARGETS: Dict[str, Any] = {
    "ctr_target_percent": 10.0,
    "average_view_duration_percent": 55.0,
    "hook_retention_percent": 70.0,
    # A visual change is a cut or a new clip. A content interrupt is a change in
    # what is being said. These were previously one number, which is wrong: a
    # genuine change of content every four seconds would be incoherent in a
    # ten-minute video, while a cut every ninety seconds would feel static.
    "visual_change_interval_seconds": 4.0,
    "content_interrupt_interval_seconds_short": 6.0,
    "content_interrupt_interval_seconds_longform": 75.0,
    "min_sfx_per_minute": 5,
    "min_visual_changes_per_minute": 15,
    # The hook window differs by lane. On a swipeable Shorts feed the keep-or-swipe
    # decision is reflexive and lands at or before one second, so the hook is the
    # opening frame. The older three-second figure came from long-form discovery.
    "hook_length_seconds_short": 1.0,
    "hook_length_seconds_longform": 10.0,
    "cta_position_percent": 90.0,
    "open_loop_payoff_percent": 80.0,
    "loudness_lufs": -14.0,
}

# Kept so existing callers and saved reports do not break.
TARGETS["pattern_interrupt_interval_seconds"] = TARGETS["visual_change_interval_seconds"]
TARGETS["hook_length_seconds"] = TARGETS["hook_length_seconds_short"]


def hook_length_target(duration_seconds: float) -> float:
    """Seconds the hook has, by lane."""
    return (
        TARGETS["hook_length_seconds_short"]
        if duration_seconds < SHORTS_MAX_DURATION
        else TARGETS["hook_length_seconds_longform"]
    )


def content_interrupt_target(duration_seconds: float) -> float:
    """Seconds between changes of content, by lane."""
    return (
        TARGETS["content_interrupt_interval_seconds_short"]
        if duration_seconds < SHORTS_MAX_DURATION
        else TARGETS["content_interrupt_interval_seconds_longform"]
    )

# Shorts research: 15-45s consistently retains best, and the Shorts algorithm ranks
# on percentage watched, not total watch time. A 20s Short watched 90% beats a
# 2-minute Short watched 30%.
SHORTS_OPTIMAL_RANGE = (15, 45)
SHORTS_HARD_MAX = 180  # YouTube raised the Shorts limit to 3 minutes in Oct 2024
LONGFORM_OPTIMAL_RANGE = (600, 900)  # 10-15 min sweet spot for faceless education

SYNTHETIC_MEDIA_DISCLOSURE = (
    "Disclosure: This video was produced with the help of automated tools. "
    "The narration is synthetic (text-to-speech) and the edit was assembled "
    "programmatically from royalty-free stock footage. No footage was generated "
    "by AI video models; all visuals are real, license-free stock or public domain material."
)

UI_DISCLOSURE_REMINDER = (
    "Reminder: in YouTube Studio, open the video details and tick "
    "'Altered or synthetic content' before publishing. This video uses a synthetic voice."
)


def aspect_ratio_for_duration(duration_seconds: float) -> str:
    """Return 9:16 for Shorts (< 120s) and 16:9 for long-form."""
    return "9:16" if duration_seconds < SHORTS_MAX_DURATION else "16:9"


def resolution_for_aspect(aspect_ratio: str) -> tuple[int, int]:
    """Return the render resolution for an aspect ratio."""
    return (1080, 1920) if aspect_ratio == "9:16" else (1920, 1080)


def word_count_for_duration(duration_seconds: float) -> int:
    """Words needed at the 2026 target pace of 140 words per minute."""
    return int((duration_seconds / 60) * 140)


def expected_visual_changes(duration_seconds: float) -> int:
    """Minimum number of visual changes for the duration."""
    return int(duration_seconds / 60 * TARGETS["min_visual_changes_per_minute"])


def expected_sfx_count(duration_seconds: float) -> int:
    """Minimum number of sound effects for the duration."""
    return int(duration_seconds / 60 * TARGETS["min_sfx_per_minute"])


def duration_advice(duration_seconds: float) -> Dict[str, Any]:
    """Assess a chosen duration against the 2026 retention research."""
    low, high = SHORTS_OPTIMAL_RANGE
    if duration_seconds < SHORTS_MAX_DURATION:
        if low <= duration_seconds <= high:
            verdict, note = "optimal", f"Inside the {low}-{high}s range that retains best."
        elif duration_seconds < low:
            verdict, note = "short", "Very short; make sure a complete idea still lands."
        else:
            verdict, note = "long", (
                f"Over {high}s. Shorts rank on percentage watched, so a tighter cut "
                "usually outperforms. Only go longer if every second earns its place."
            )
        return {"format": "Shorts", "verdict": verdict, "note": note,
                "optimal_range": [low, high]}
    lo, hi = LONGFORM_OPTIMAL_RANGE
    if lo <= duration_seconds <= hi:
        verdict, note = "optimal", "Inside the 10-15 minute sweet spot for faceless content."
    elif duration_seconds < lo:
        verdict, note = "short", (
            "Long-form rewards total watch time; under 10 minutes leaves reach unused."
        )
    else:
        verdict, note = "long", "Over 15 minutes; retention percentage usually falls."
    return {"format": "Long-form", "verdict": verdict, "note": note,
            "optimal_range": [lo, hi]}


def compliance_report(duration_seconds: float, sfx_count: int, visual_changes: int) -> Dict[str, Any]:
    """Compare a produced video against the 2026 quantitative targets."""
    needed_sfx = expected_sfx_count(duration_seconds)
    needed_visuals = expected_visual_changes(duration_seconds)
    return {
        "duration_seconds": round(duration_seconds, 2),
        "sfx_count": sfx_count,
        "sfx_target": needed_sfx,
        "sfx_ok": sfx_count >= needed_sfx,
        "visual_changes": visual_changes,
        "visual_changes_target": needed_visuals,
        "visual_changes_ok": visual_changes >= needed_visuals,
        "aspect_ratio": aspect_ratio_for_duration(duration_seconds),
        "loudness_lufs": TARGETS["loudness_lufs"],
    }
