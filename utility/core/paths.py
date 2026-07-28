"""Single source of truth for project paths.

The project root is found by walking upwards until a root marker is seen rather
than by counting parent directories. Counting breaks the moment a module moves
one level deeper; the marker search keeps working from any depth and from any
working directory.

Two top-level folders hold everything the application reads and writes:

``assets/``
    Everything the application needs but does not deliver: downloaded fonts,
    cached API responses, trend history and temporary render scratch files.
    Safe to delete at any time; all of it is regenerated.

``output/``
    The finished videos and everything belonging to them - thumbnails and the
    SEO upload packages. This is the only folder a user needs to back up.
"""

from __future__ import annotations

import os

# Files that only ever exist at the top of the project.
ROOT_MARKERS = ("pyproject.toml", "app.py")


def _find_project_root() -> str:
    """Walk upwards from this file until a directory holding a root marker is found."""
    current = os.path.dirname(os.path.abspath(__file__))
    while True:
        if any(os.path.exists(os.path.join(current, m)) for m in ROOT_MARKERS):
            return current
        parent = os.path.dirname(current)
        if parent == current:  # reached the filesystem root
            return os.getcwd()
        current = parent


PROJECT_ROOT = _find_project_root()

CONFIG_FILE = os.path.join(PROJECT_ROOT, "config.json")

# ----------------------------------------------------------------------
# assets/ - inputs, caches and scratch space
# ----------------------------------------------------------------------
ASSETS_DIR = os.path.join(PROJECT_ROOT, "assets")
FONT_CACHE_DIR = os.path.join(ASSETS_DIR, "fonts")
API_CACHE_DIR = os.path.join(ASSETS_DIR, "api_cache")
MUSIC_DIR = os.path.join(ASSETS_DIR, "music")
TREND_CACHE_FILE = os.path.join(ASSETS_DIR, "trend_cache.json")
TREND_HISTORY_FILE = os.path.join(ASSETS_DIR, "trend_history.json")

# Temporary render files. Inside assets/ because they are scratch, not output.
WORK_DIR = os.path.join(ASSETS_DIR, "temp")
BIN_DIR = os.path.join(ASSETS_DIR, "bin")

# ----------------------------------------------------------------------
# output/ - the finished videos
# ----------------------------------------------------------------------
OUTPUT_DIR = os.path.join(PROJECT_ROOT, "output")
GALLERY_DIR = OUTPUT_DIR
GALLERY_VIDEOS = os.path.join(OUTPUT_DIR, "videos")
GALLERY_THUMBS = os.path.join(OUTPUT_DIR, "thumbnails")
GALLERY_PACKAGES = os.path.join(OUTPUT_DIR, "packages")
GALLERY_METADATA = os.path.join(OUTPUT_DIR, "videos_metadata.json")


def ensure_runtime_dirs() -> None:
    """Create every directory the application writes to."""
    for directory in (
        ASSETS_DIR, FONT_CACHE_DIR, API_CACHE_DIR, MUSIC_DIR, WORK_DIR,
        OUTPUT_DIR, GALLERY_VIDEOS, GALLERY_THUMBS, GALLERY_PACKAGES,
    ):
        os.makedirs(directory, exist_ok=True)
