"""24-hour disk cache for API GET requests.

The Pixabay API terms require that responses are cached for 24 hours and that no
systematic mass querying takes place. This module provides a small, dependency-free
disk cache used by MediaSourceManager for every API call, which also keeps the
system fast and well inside the 100 requests / 60 seconds rate limit.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from typing import Any, Dict, Optional, Tuple

LOGGER = logging.getLogger("api_cache")
if not LOGGER.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("[%(name)s] %(message)s"))
    LOGGER.addHandler(_h)
LOGGER.setLevel(logging.INFO)

from utility.core.paths import API_CACHE_DIR as CACHE_DIR
DEFAULT_TTL_SECONDS = 24 * 60 * 60  # Pixabay requires 24 hours

_LOCK = threading.Lock()


def _key_for(url: str, params: Optional[Dict[str, Any]]) -> str:
    """Build a stable cache filename for a URL plus its query parameters."""
    safe_params = {}
    for key, value in (params or {}).items():
        # Never write API keys to disk in clear text; hash them instead.
        if key.lower() in ("key", "api_key", "apikey", "authorization"):
            safe_params[key] = hashlib.sha256(str(value).encode()).hexdigest()[:12]
        else:
            safe_params[key] = value
    blob = url + "|" + json.dumps(safe_params, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest() + ".json"


class ApiCache:
    """Simple JSON disk cache with a time-to-live."""

    def __init__(self, ttl_seconds: int = DEFAULT_TTL_SECONDS, directory: str = CACHE_DIR):
        self.ttl = ttl_seconds
        self.directory = directory
        os.makedirs(self.directory, exist_ok=True)

    def get(self, url: str, params: Optional[Dict[str, Any]] = None) -> Optional[Any]:
        """Return the cached payload when it is still fresh, else None."""
        path = os.path.join(self.directory, _key_for(url, params))
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as handle:
                entry = json.load(handle)
            if time.time() - entry.get("stored_at", 0) > self.ttl:
                return None
            return entry.get("payload")
        except (OSError, json.JSONDecodeError):
            return None

    def set(self, url: str, params: Optional[Dict[str, Any]], payload: Any) -> None:
        """Store a payload for this URL and parameter combination."""
        path = os.path.join(self.directory, _key_for(url, params))
        entry = {"stored_at": time.time(), "url": url, "payload": payload}
        with _LOCK:
            try:
                with open(path, "w", encoding="utf-8") as handle:
                    json.dump(entry, handle)
            except OSError as exc:
                LOGGER.info("Could not write the API cache entry: %s", exc)

    def stats(self) -> Dict[str, Any]:
        """Return cache size information for the UI."""
        files = [f for f in os.listdir(self.directory) if f.endswith(".json")] if os.path.isdir(self.directory) else []
        total_bytes = sum(os.path.getsize(os.path.join(self.directory, f)) for f in files)
        fresh = 0
        for name in files:
            try:
                with open(os.path.join(self.directory, name), "r", encoding="utf-8") as handle:
                    if time.time() - json.load(handle).get("stored_at", 0) <= self.ttl:
                        fresh += 1
            except (OSError, json.JSONDecodeError):
                continue
        return {
            "entries": len(files),
            "fresh_entries": fresh,
            "size_kb": round(total_bytes / 1024, 1),
            "ttl_hours": round(self.ttl / 3600, 1),
        }

    def clear(self) -> int:
        """Delete every cached response and return how many were removed."""
        removed = 0
        if not os.path.isdir(self.directory):
            return 0
        for name in os.listdir(self.directory):
            if name.endswith(".json"):
                try:
                    os.remove(os.path.join(self.directory, name))
                    removed += 1
                except OSError:
                    continue
        LOGGER.info("Cleared %d cached API responses.", removed)
        return removed


class RateLimitTracker:
    """Records the rate limit headers returned by the media APIs."""

    def __init__(self) -> None:
        self._state: Dict[str, Dict[str, Any]] = {}

    def record(self, service: str, headers: Dict[str, str]) -> None:
        """Store the latest rate limit values for a service."""
        lowered = {k.lower(): v for k, v in headers.items()}
        limit = lowered.get("x-ratelimit-limit")
        remaining = lowered.get("x-ratelimit-remaining")
        reset = lowered.get("x-ratelimit-reset")
        if limit is None and remaining is None:
            return
        self._state[service] = {
            "limit": _as_int(limit),
            "remaining": _as_int(remaining),
            "reset_seconds": _as_int(reset),
            "updated_at": time.time(),
        }

    def get(self, service: str) -> Optional[Dict[str, Any]]:
        """Return the last known rate limit state for a service."""
        return self._state.get(service)

    def all(self) -> Dict[str, Dict[str, Any]]:
        """Return the rate limit state for every service seen so far."""
        return dict(self._state)

    def is_exhausted(self, service: str, threshold: int = 2) -> bool:
        """True when very few requests remain in the current window."""
        state = self._state.get(service)
        if not state or state.get("remaining") is None:
            return False
        return state["remaining"] <= threshold


def _as_int(value: Optional[str]) -> Optional[int]:
    """Parse an integer header value, tolerating missing or malformed data."""
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


_CACHE: Optional[ApiCache] = None
_TRACKER: Optional[RateLimitTracker] = None


def get_cache() -> ApiCache:
    """Return the process-wide API cache."""
    global _CACHE
    if _CACHE is None:
        _CACHE = ApiCache()
    return _CACHE


def get_rate_tracker() -> RateLimitTracker:
    """Return the process-wide rate limit tracker."""
    global _TRACKER
    if _TRACKER is None:
        _TRACKER = RateLimitTracker()
    return _TRACKER


def cached_get(
    session,
    service: str,
    url: str,
    params: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: int = 20,
    ttl_seconds: Optional[int] = None,
) -> Tuple[Optional[Any], Optional[int], bool]:
    """Perform a cached GET returning (json_payload, status_code, from_cache)."""
    cache = get_cache()
    if ttl_seconds is not None:
        cache = ApiCache(ttl_seconds)
    hit = cache.get(url, params)
    if hit is not None:
        return hit, 200, True

    response = session.get(url, params=params, headers=headers, timeout=timeout)
    get_rate_tracker().record(service, dict(response.headers))
    if not response.ok:
        return None, response.status_code, False
    try:
        payload = response.json()
    except ValueError:
        return None, response.status_code, False
    cache.set(url, params, payload)
    return payload, response.status_code, False
