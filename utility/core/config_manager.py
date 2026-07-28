"""Configuration manager. All settings live in config.json (no .env file)."""

import json
import os
import threading
from typing import Any, Dict

from utility.core.paths import CONFIG_FILE

DEFAULT_CONFIG: Dict[str, Any] = {
    # ---- LLM provider selection ----------------------------------------
    # The user picks exactly one provider; models inside it fall back automatically.
    "llm_provider": "openrouter",
    "router9_url": "http://localhost:9000/v1",
    "router9_key": "",
    "openrouter_key": "",
    "nvidia_nim_key": "",
    "nvidia_nim_url": "https://integrate.api.nvidia.com/v1",
    "cloudflare_account_id": "",
    "cloudflare_api_token": "",
    # ---- Media providers ----------------------------------------------
    "pexels_api_key": "",
    "pixabay_api_key": "",
    # ---- Watermark -----------------------------------------------------
    "watermark": {
        "enabled": True,
        "type": "handle",              # text | handle | logo
        "content": "@YourChannel",
        "opacity": 0.25,               # 0.10 - 0.50
        "font_size_ratio": 0.03,       # 3% of frame height
        "font_family": "Arial Bold",
        "color": "#FFFFFF",
        "stroke_color": "#000000",
        "stroke_width": 1,
        "movement_pattern": "random_smooth",  # random_smooth|random_jump|circular|diagonal
        "movement_speed": "slow",             # slow|medium|fast
        "change_interval": 5,                 # seconds (3-10)
        "safe_zone_padding": 0.05,
    },
    # ---- Rendering ------------------------------------------------------
    "pattern_interrupts": True,
    "pattern_interrupt_interval": 4.0,
    "emoji_captions": False,
    "caption_style": "YouTube Shorts Style",
    "fps": 30,
    "video_preset": "veryfast",
    # ---- Gallery --------------------------------------------------------
    "gallery_max_videos": 1000,
    "gallery_cleanup_threshold": 500,
    "gallery_autocleanup": True,
    # ---- Trends ---------------------------------------------------------
    "trend_cache_ttl_minutes": 60,
    "trend_source_timeout": 10,
    "trend_history_size": 100,
}

_LOCK = threading.Lock()


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """Recursively merge override into a copy of base."""
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


class ConfigManager:
    """Thread-safe read/write access to config.json."""

    def __init__(self, path: str = CONFIG_FILE):
        self.path = path
        self._cache: Dict[str, Any] = {}
        self.load()

    def load(self) -> Dict[str, Any]:
        """Load config from disk, merged over defaults."""
        data = {}
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as handle:
                    data = json.load(handle)
            except (OSError, json.JSONDecodeError) as exc:
                print(f"[Config] Could not read config.json ({exc}); using defaults.")
                data = {}
        self._cache = _deep_merge(DEFAULT_CONFIG, data)
        return self._cache

    def save(self) -> None:
        """Persist the in-memory config to disk."""
        with _LOCK:
            with open(self.path, "w", encoding="utf-8") as handle:
                json.dump(self._cache, handle, indent=2, ensure_ascii=False)

    def all(self) -> Dict[str, Any]:
        """Return the whole configuration dictionary."""
        return self._cache

    def get(self, key: str, default: Any = None) -> Any:
        """Read a top-level key, with dotted-path support."""
        node: Any = self._cache
        for part in key.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def set(self, key: str, value: Any, autosave: bool = True) -> None:
        """Write a key (dotted paths create nested dictionaries)."""
        parts = key.split(".")
        node = self._cache
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value
        if autosave:
            self.save()

    def update(self, values: Dict[str, Any], autosave: bool = True) -> None:
        """Merge a dictionary of values into the configuration."""
        self._cache = _deep_merge(self._cache, values)
        if autosave:
            self.save()

    # -- Convenience accessors -------------------------------------------
    def pexels_key(self) -> str:
        """Return the stored Pexels API key."""
        return self.get("pexels_api_key", "") or ""

    def pixabay_key(self) -> str:
        """Return the stored Pixabay API key."""
        return self.get("pixabay_api_key", "") or ""

    def watermark(self) -> Dict[str, Any]:
        """Return the watermark configuration block."""
        return self.get("watermark", {})

    def llm_provider(self) -> str:
        """Return the id of the selected LLM provider."""
        return self.get("llm_provider", "openrouter") or "openrouter"


_CONFIG_SINGLETON: ConfigManager | None = None


def get_config() -> ConfigManager:
    """Return the process-wide ConfigManager singleton."""
    global _CONFIG_SINGLETON
    if _CONFIG_SINGLETON is None:
        _CONFIG_SINGLETON = ConfigManager()
    return _CONFIG_SINGLETON
