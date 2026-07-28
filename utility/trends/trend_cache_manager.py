"""Trend caching (1 hour TTL) and suggestion history (last 100 titles)."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

LOGGER = logging.getLogger("trend_cache")
if not LOGGER.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("[%(name)s] %(message)s"))
    LOGGER.addHandler(_h)
LOGGER.setLevel(logging.INFO)

from utility.core.paths import ASSETS_DIR, TREND_CACHE_FILE, TREND_HISTORY_FILE as HISTORY_FILE


class TrendCacheManager:
    """Persists raw trend payloads and the rolling suggestion history."""

    def __init__(self, ttl_minutes: int = 60, history_size: int = 100):
        self.ttl = timedelta(minutes=ttl_minutes)
        self.history_size = history_size
        os.makedirs(ASSETS_DIR, exist_ok=True)

    # ------------------------------------------------------------------
    def _read(self, path: str, default: Any) -> Any:
        """Read a JSON file, returning a default on any error."""
        if not os.path.exists(path):
            return default
        try:
            with open(path, "r", encoding="utf-8") as handle:
                return json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            LOGGER.info("Could not read %s: %s", path, exc)
            return default

    def _write(self, path: str, data: Any) -> None:
        """Write data to a JSON file."""
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, ensure_ascii=False)

    # ------------------------------------------------------------------
    def get_cached_trends(self) -> Optional[Dict[str, Any]]:
        """Return cached trend data when it is still fresh."""
        payload = self._read(TREND_CACHE_FILE, None)
        if not payload:
            return None
        try:
            stamp = datetime.fromisoformat(payload["cached_at"])
        except (KeyError, ValueError):
            return None
        if datetime.now(timezone.utc) - stamp > self.ttl:
            LOGGER.info("Trend cache expired.")
            return None
        return payload

    def save_trends(self, raw: Dict[str, Any], analyzed: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Persist a fresh scan."""
        payload = {
            "cached_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "raw": raw,
            "analyzed": analyzed,
        }
        self._write(TREND_CACHE_FILE, payload)
        return payload

    def clear_cache(self) -> None:
        """Delete the cached trend payload."""
        if os.path.exists(TREND_CACHE_FILE):
            os.remove(TREND_CACHE_FILE)
            LOGGER.info("Trend cache cleared.")

    def age_minutes(self) -> Optional[float]:
        """Minutes since the last successful scan."""
        payload = self._read(TREND_CACHE_FILE, None)
        if not payload:
            return None
        try:
            stamp = datetime.fromisoformat(payload["cached_at"])
        except (KeyError, ValueError):
            return None
        return round((datetime.now(timezone.utc) - stamp).total_seconds() / 60, 1)

    def last_updated_label(self) -> str:
        """Human readable freshness label for the UI."""
        age = self.age_minutes()
        if age is None:
            return "Never scanned"
        if age < 1:
            return "Last updated: just now"
        return f"Last updated: {int(age)} minutes ago"

    # ------------------------------------------------------------------
    def history(self) -> List[Dict[str, Any]]:
        """Return the stored suggestion history (newest first)."""
        return self._read(HISTORY_FILE, [])

    def history_titles(self) -> List[str]:
        """Just the titles from history, for uniqueness checks."""
        return [item.get("title", "") for item in self.history()]

    def add_to_history(self, suggestions: List[Dict[str, Any]]) -> None:
        """Append new suggestions, keeping only the most recent N."""
        items = self.history()
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        for suggestion in suggestions:
            items.insert(
                0,
                {
                    "title": suggestion.get("title", ""),
                    "category": suggestion.get("category", ""),
                    "viral_score": suggestion.get("viral_score", 0),
                    "created_at": stamp,
                },
            )
        self._write(HISTORY_FILE, items[: self.history_size])

    def clear_history(self) -> None:
        """Delete the suggestion history."""
        if os.path.exists(HISTORY_FILE):
            os.remove(HISTORY_FILE)
            LOGGER.info("Trend suggestion history cleared.")
