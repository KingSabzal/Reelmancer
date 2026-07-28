"""Video gallery: storage, metadata, search, filters, statistics and auto-cleanup."""

from __future__ import annotations

import json
import logging
import os
import shutil
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

LOGGER = logging.getLogger("gallery")
if not LOGGER.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("[%(name)s] %(message)s"))
    LOGGER.addHandler(_h)
LOGGER.setLevel(logging.INFO)

from utility.core.naming import output_stem, unique_path
from utility.core.paths import (
    GALLERY_DIR,
    GALLERY_METADATA as METADATA_FILE,
    GALLERY_PACKAGES as PACKAGES_DIR,
    GALLERY_THUMBS as THUMBS_DIR,
    GALLERY_VIDEOS as VIDEOS_DIR,
)


class VideoGalleryManager:
    """CRUD plus statistics for generated videos."""

    def __init__(self, max_videos: int = 1000, cleanup_threshold: int = 500, autocleanup: bool = True):
        self.max_videos = max_videos
        self.cleanup_threshold = cleanup_threshold
        self.autocleanup = autocleanup
        for folder in (GALLERY_DIR, VIDEOS_DIR, THUMBS_DIR, PACKAGES_DIR):
            os.makedirs(folder, exist_ok=True)
        self.metadata: List[Dict[str, Any]] = self._load()

    # ------------------------------------------------------------------
    def _load(self) -> List[Dict[str, Any]]:
        """Load the gallery metadata from disk."""
        if os.path.exists(METADATA_FILE):
            try:
                with open(METADATA_FILE, "r", encoding="utf-8") as handle:
                    return json.load(handle)
            except (OSError, json.JSONDecodeError) as exc:
                LOGGER.warning("Could not read gallery metadata: %s", exc)
        return []

    def _save(self) -> None:
        """Persist the gallery metadata to disk."""
        with open(METADATA_FILE, "w", encoding="utf-8") as handle:
            json.dump(self.metadata, handle, indent=2, ensure_ascii=False)

    # ------------------------------------------------------------------
    def create_entry(self, title: str, topic: str, style: str) -> Dict[str, Any]:
        """Register a new processing entry and return it."""
        entry = {
            "video_id": str(uuid.uuid4()),
            "title": title,
            "topic": topic,
            "style": style,
            "duration_seconds": 0,
            "aspect_ratio": "",
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "file_path": "",
            "thumbnail_path": "",
            "file_size_mb": 0.0,
            "resolution": "",
            "voice_used": "",
            "music_used": "",
            "tags": [],
            "status": "processing",
        }
        self.metadata.insert(0, entry)
        self._save()
        return entry

    def finalize_entry(
        self,
        video_id: str,
        source_path: str,
        duration_seconds: float,
        aspect_ratio: str,
        resolution: str,
        voice_used: str = "",
        music_used: str = "",
        tags: Optional[List[str]] = None,
        seo_packages: Optional[Dict[str, Any]] = None,
        upload_title: str = "",
    ) -> Optional[Dict[str, Any]]:
        """Move the rendered file into the gallery and complete its metadata.

        The stored file is named after the YouTube upload title with a hyphen between
        every word, so the gallery folder stays readable instead of holding a pile of
        raw ids. The internal ``video_id`` is still the key used by the metadata.
        """
        entry = self.get(video_id)
        if not entry:
            return None
        stem = output_stem(
            upload_title or (seo_packages or {}).get("youtube", {}).get("title", ""),
            entry.get("topic", ""),
            video_id,
        )
        entry["file_stem"] = stem
        if upload_title:
            # Show the real upload title in the gallery, not the raw topic.
            entry["title"] = upload_title
        target = unique_path(VIDEOS_DIR, stem, ".mp4")
        try:
            if os.path.abspath(source_path) != os.path.abspath(target):
                shutil.move(source_path, target)
        except OSError as exc:
            LOGGER.error("Could not move rendered video into the gallery: %s", exc)
            entry["status"] = "failed"
            self._save()
            return entry

        entry.update(
            {
                "file_path": target,
                "duration_seconds": round(float(duration_seconds), 2),
                "aspect_ratio": aspect_ratio,
                "resolution": resolution,
                "file_size_mb": round(os.path.getsize(target) / (1024 * 1024), 2),
                "voice_used": voice_used,
                "music_used": music_used,
                "tags": tags or [],
                "status": "completed",
                "thumbnail_path": self.make_thumbnail(target, stem),
            }
        )
        if seo_packages:
            package_path = unique_path(PACKAGES_DIR, stem, ".json")
            with open(package_path, "w", encoding="utf-8") as handle:
                json.dump(seo_packages, handle, indent=2, ensure_ascii=False)
            entry["seo_package_path"] = package_path
        self._save()
        self.auto_cleanup()
        return entry

    def mark_failed(self, video_id: str, reason: str = "") -> None:
        """Flag an entry as failed."""
        entry = self.get(video_id)
        if entry:
            entry["status"] = "failed"
            entry["error"] = reason[:500]
            self._save()

    def make_thumbnail(self, video_path: str, stem: str) -> str:
        """Extract a JPEG thumbnail from the first seconds of the video."""
        thumb_path = unique_path(THUMBS_DIR, stem, ".jpg")
        try:
            from utility.audio.audio_mixer import run_ffmpeg

            run_ffmpeg(["-ss", "1", "-i", video_path, "-frames:v", "1", "-vf", "scale=480:-1", thumb_path])
            return thumb_path if os.path.exists(thumb_path) else ""
        except Exception as exc:  # noqa: BLE001
            LOGGER.info("Thumbnail extraction failed: %s", exc)
            return ""

    # ------------------------------------------------------------------
    def get(self, video_id: str) -> Optional[Dict[str, Any]]:
        """Return one entry by id."""
        for entry in self.metadata:
            if entry["video_id"] == video_id:
                return entry
        return None

    def delete(self, video_id: str) -> bool:
        """Delete a video, its thumbnail, its package and its metadata row."""
        entry = self.get(video_id)
        if not entry:
            return False
        for key in ("file_path", "thumbnail_path", "seo_package_path"):
            path = entry.get(key)
            if path and os.path.exists(path):
                try:
                    os.remove(path)
                except OSError as exc:
                    LOGGER.info("Could not delete %s: %s", path, exc)
        self.metadata = [e for e in self.metadata if e["video_id"] != video_id]
        self._save()
        return True

    def search(
        self,
        query: str = "",
        style: str = "",
        aspect_ratio: str = "",
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        sort_by: str = "date",
        descending: bool = True,
    ) -> List[Dict[str, Any]]:
        """Search, filter and sort the gallery."""
        query = (query or "").lower().strip()
        results = []
        for entry in self.metadata:
            haystack = " ".join(
                [entry.get("title", ""), entry.get("topic", ""), " ".join(entry.get("tags", []))]
            ).lower()
            if query and query not in haystack:
                continue
            if style and entry.get("style") != style:
                continue
            if aspect_ratio and entry.get("aspect_ratio") != aspect_ratio:
                continue
            created = entry.get("created_at", "")
            if date_from and created < date_from:
                continue
            if date_to and created > date_to + "T23:59:59":
                continue
            results.append(entry)

        keys = {
            "date": lambda e: e.get("created_at", ""),
            "duration": lambda e: e.get("duration_seconds", 0),
            "size": lambda e: e.get("file_size_mb", 0),
            "title": lambda e: e.get("title", "").lower(),
        }
        results.sort(key=keys.get(sort_by, keys["date"]), reverse=descending)
        return results

    def statistics(self) -> Dict[str, Any]:
        """Aggregate gallery statistics for the UI."""
        completed = [e for e in self.metadata if e.get("status") == "completed"]
        distribution: Dict[str, int] = {}
        for entry in completed:
            distribution[entry.get("style", "Unknown")] = (
                distribution.get(entry.get("style", "Unknown"), 0) + 1
            )
        return {
            "total_videos": len(completed),
            "total_duration_seconds": round(sum(e.get("duration_seconds", 0) for e in completed), 1),
            "total_size_mb": round(sum(e.get("file_size_mb", 0) for e in completed), 2),
            "style_count": len(distribution),
            "style_distribution": dict(
                sorted(distribution.items(), key=lambda item: item[1], reverse=True)
            ),
        }

    def auto_cleanup(self) -> int:
        """Delete the oldest 10% of videos once the threshold is exceeded."""
        if not self.autocleanup:
            return 0
        completed = [e for e in self.metadata if e.get("status") == "completed"]
        if len(completed) <= self.cleanup_threshold:
            return 0
        completed.sort(key=lambda e: e.get("created_at", ""))
        remove_count = max(1, int(len(completed) * 0.10))
        removed = 0
        for entry in completed[:remove_count]:
            if self.delete(entry["video_id"]):
                removed += 1
        LOGGER.info("Auto-cleanup removed %d old videos.", removed)
        return removed

    def load_packages(self, video_id: str) -> Optional[Dict[str, Any]]:
        """Return the stored SEO packages for a video."""
        entry = self.get(video_id)
        path = (entry or {}).get("seo_package_path")
        if path and os.path.exists(path):
            with open(path, "r", encoding="utf-8") as handle:
                return json.load(handle)
        return None
