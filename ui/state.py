"""Shared UI state and grouping helpers used by more than one tab.

These live outside the individual tab modules so that two tabs importing the
same helper never pull one another in as a side effect.
"""

from __future__ import annotations

from typing import Dict

from utility.core.config_manager import get_config
from utility.content.video_styles import VIDEO_STYLES
from utility.publishing.video_gallery_manager import VideoGalleryManager

CONFIG = get_config()


def style_groups() -> Dict[str, list]:
    """Group the 122 video styles into browsable categories."""
    buckets: Dict[str, list] = {}
    themes = {
        "Cinematic & Film": ["Cinematic", "Epic Trailer", "Noir", "Film", "Found Footage",
                              "Mockumentary", "Biopic", "Documentary", "Vintage", "Luxury"],
        "Horror & Dark": ["Horror", "Ghost", "Zombie", "Vampire", "Cryptid", "Gothic",
                           "Dark Moody", "Apocalypse", "Dystopian", "Thriller", "True Crime"],
        "Science & Space": ["Space", "Galaxy", "Nebula", "Black Hole", "Astronomy", "Physics",
                             "Quantum", "Biology", "DNA", "Microscope", "Mars Colony",
                             "Rocket Launch", "Multiverse", "Time Travel", "Simulation Theory"],
        "Technology": ["Tech Review", "AI & Tech", "Robot", "Cyborg", "Cyberpunk", "Singularity",
                        "Crypto", "Gaming", "Product Demo"],
        "Nature & Places": ["Nature", "Wildlife", "Ocean", "Forest", "Mountain", "Desert",
                             "Arctic", "Volcanic", "Waterfall", "Aurora", "Urban", "Travel Vlog"],
        "Education & Explainer": ["Explainer", "Tutorial", "How-To", "Guide", "Lecture",
                                   "Masterclass", "TED Talk", "Deep Dive", "Breakdown",
                                   "Analysis", "Data Story", "Visualization", "Timeline",
                                   "Kids Educational", "FAQ", "Troubleshooting", "Onboarding"],
        "Business & Money": ["Business", "Finance", "Motivational", "Speech", "Interview",
                              "Podcast Style", "Review", "News Report", "Street Interview"],
        "Story & Fantasy": ["Fantasy", "Sci-Fi", "Dragon", "Phoenix", "Mystery", "Adventure",
                             "Action", "Romance", "Alternate History", "What If", "Historical",
                             "Lost Civilization", "Atlantis", "UFO", "Alien", "Steampunk"],
        "Lifestyle & Creative": ["Comedy", "Anime", "Fashion", "Cooking", "Fitness", "DIY",
                                  "Sports", "Meditation", "ASMR", "Quote", "Countdown",
                                  "Minimalist", "Abstract", "Surreal", "Motion Graphics"],
        "Retro & Aesthetic": ["Retro 80s", "Vaporwave", "Neon", "Bright Cheerful", "Utopian"],
    }
    assigned = set()
    for group, names in themes.items():
        members = [n for n in names if n in VIDEO_STYLES]
        if members:
            buckets[group] = members
            assigned.update(members)
    leftovers = [n for n in VIDEO_STYLES if n not in assigned]
    if leftovers:
        buckets["Other"] = sorted(leftovers)
    return buckets


def caption_groups() -> Dict[str, list]:
    """Group caption styles by their declared category."""
    from utility.video.caption_styles import CAPTION_STYLES

    buckets: Dict[str, list] = {}
    for name, style in CAPTION_STYLES.items():
        buckets.setdefault(style["category"].title(), []).append(name)
    return buckets


def voice_groups(options: list) -> Dict[str, list]:
    """Group voice option labels by accent, keeping multi-word accents intact."""
    buckets: Dict[str, list] = {"Auto": ["Auto (recommended)"]}
    # Regions with only one or two voices are merged so the filter stays short.
    regions = {
        "American": "American", "British": "British & Irish", "Irish": "British & Irish",
        "Australian": "Australia & NZ", "New Zealander": "Australia & NZ",
        "Canadian": "North America (other)", "European": "European",
        "Indian": "South Asia", "Singaporean": "Asia Pacific", "Filipino": "Asia Pacific",
        "Hong Kong": "Asia Pacific", "Asian": "Asia Pacific",
        "South African": "Africa", "Kenyan": "Africa", "Nigerian": "Africa",
        "Tanzanian": "Africa", "Latin": "Latin",
    }
    for option in options[1:]:
        accent = "Other"
        if " - " in option:
            tail = option.split(" - ", 1)[1]
            for name in sorted(regions, key=len, reverse=True):
                if tail.startswith(name):
                    accent = regions[name]
                    break
        buckets.setdefault(accent, []).append(option)
    return buckets


def gallery() -> VideoGalleryManager:
    """Return a gallery manager built from the current configuration."""
    return VideoGalleryManager(
        max_videos=int(CONFIG.get("gallery_max_videos", 1000)),
        cleanup_threshold=int(CONFIG.get("gallery_cleanup_threshold", 500)),
        autocleanup=bool(CONFIG.get("gallery_autocleanup", True)),
    )


# ----------------------------------------------------------------------
