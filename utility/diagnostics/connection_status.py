"""Connection and API key diagnostics.

Answers one question for the user: which keys and sites am I actually connected to?
Every check performs a real, minimal request and reports a clear status:

    ok        - connected and working
    invalid   - the service rejected the key
    missing   - no key configured
    limited   - connected but the rate limit is exhausted
    degraded  - reachable but returned something unexpected
    offline   - the host could not be reached
"""

from __future__ import annotations

import concurrent.futures
import logging
import re
import socket
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, List, Optional

import requests

from utility.core.api_cache import get_rate_tracker
from utility.core.config_manager import get_config
from utility.llm.llm_providers import get_provider, missing_fields
from utility.media.media_sources import HEADERS

LOGGER = logging.getLogger("connection_status")
if not LOGGER.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("[%(name)s] %(message)s"))
    LOGGER.addHandler(_h)
LOGGER.setLevel(logging.INFO)

CHECK_TIMEOUT = 12

# Smithsonian Open Access accepts the public data.gov demo key without registration.
SMITHSONIAN_DEMO_KEY = "DEMO_KEY"

STATUS_ICONS = {
    "ok": "\u2705",
    "invalid": "\u274C",
    "missing": "\u26AA",
    "limited": "\u23F3",
    "degraded": "\u26A0\uFE0F",
    "offline": "\U0001F534",
}

STATUS_COLORS = {
    "ok": "#16A34A",
    "invalid": "#DC2626",
    "missing": "#9CA3AF",
    "limited": "#EAB308",
    "degraded": "#F59E0B",
    "offline": "#DC2626",
}


@dataclass
class CheckResult:
    """The outcome of a single connectivity check."""

    service: str
    category: str
    status: str
    message: str
    latency_ms: Optional[int] = None
    requires_key: bool = False
    details: Dict[str, Any] = field(default_factory=dict)

    @property
    def icon(self) -> str:
        """Status icon for the UI."""
        return STATUS_ICONS.get(self.status, "\u2753")

    @property
    def color(self) -> str:
        """Status color for the UI."""
        return STATUS_COLORS.get(self.status, "#6B7280")

    def as_dict(self) -> Dict[str, Any]:
        """Serializable form of the result."""
        data = asdict(self)
        data["icon"] = self.icon
        data["color"] = self.color
        return data


def mask_key(value: str) -> str:
    """Mask a secret so it can be shown safely in the UI."""
    value = (value or "").strip()
    if not value:
        return "(not set)"
    if len(value) <= 8:
        return value[0] + "*" * (len(value) - 1)
    return f"{value[:4]}{'*' * 8}{value[-4:]}"


def _timed(function: Callable[[], CheckResult]) -> CheckResult:
    """Run a check and attach its latency."""
    started = time.perf_counter()
    try:
        result = function()
    except requests.Timeout:
        return CheckResult("unknown", "unknown", "offline", "The request timed out.")
    except (requests.ConnectionError, socket.gaierror):
        return CheckResult("unknown", "unknown", "offline", "The host could not be reached.")
    except Exception as exc:  # noqa: BLE001 - a diagnostic must never raise
        return CheckResult("unknown", "unknown", "degraded", f"Unexpected error: {exc}")
    result.latency_ms = int((time.perf_counter() - started) * 1000)
    return result


class ConnectionStatusChecker:
    """Runs every connectivity and credential check in parallel."""

    def __init__(self, config=None):
        self.config = config or get_config()
        self.session = requests.Session()
        self.session.headers.update(HEADERS)

    # ------------------------------------------------------------------
    # Media APIs (key based)
    # ------------------------------------------------------------------
    def check_pexels(self) -> CheckResult:
        """Validate the Pexels key with a 1-result search."""
        key = self.config.pexels_key()
        if not key:
            return CheckResult(
                "Pexels", "media_api", "missing",
                "No API key. Get a free one at pexels.com/api/new/.", requires_key=True,
                details={"signup": "https://www.pexels.com/api/new/"},
            )
        response = self.session.get(
            "https://api.pexels.com/videos/search",
            headers={"Authorization": key},
            params={"query": "ocean", "per_page": 1},
            timeout=CHECK_TIMEOUT,
        )
        get_rate_tracker().record("pexels", dict(response.headers))
        if response.status_code in (401, 403):
            return CheckResult(
                "Pexels", "media_api", "invalid",
                "The key was rejected. Check it in Settings.", requires_key=True,
                details={"key": mask_key(key), "http": response.status_code},
            )
        if response.status_code == 429:
            return CheckResult(
                "Pexels", "media_api", "limited",
                "Rate limit reached. Try again later.", requires_key=True,
                details={"key": mask_key(key)},
            )
        if not response.ok:
            return CheckResult(
                "Pexels", "media_api", "degraded",
                f"Unexpected response (HTTP {response.status_code}).", requires_key=True,
            )
        payload = response.json()
        headers = {k.lower(): v for k, v in response.headers.items()}
        return CheckResult(
            "Pexels", "media_api", "ok",
            f"Connected. {payload.get('total_results', 0):,} clips match a test query.",
            requires_key=True,
            details={
                "key": mask_key(key),
                "monthly_limit": headers.get("x-ratelimit-limit", "unknown"),
                "remaining": headers.get("x-ratelimit-remaining", "unknown"),
            },
        )

    def check_pixabay(self) -> CheckResult:
        """Validate the Pixabay key and detect the access tier."""
        key = self.config.pixabay_key()
        if not key:
            return CheckResult(
                "Pixabay", "media_api", "missing",
                "No API key. Get a free one at pixabay.com/api/key/.", requires_key=True,
                details={"signup": "https://pixabay.com/api/key/"},
            )
        response = self.session.get(
            "https://pixabay.com/api/",
            params={"key": key, "q": "ocean", "image_type": "photo", "per_page": 3},
            timeout=CHECK_TIMEOUT,
        )
        get_rate_tracker().record("pixabay", dict(response.headers))

        # Pixabay signals a bad key with HTTP 400, not 401.
        if response.status_code == 400 and "key" in response.text.lower():
            return CheckResult(
                "Pixabay", "media_api", "invalid",
                "Invalid or missing API key (Pixabay returns HTTP 400 for this).",
                requires_key=True, details={"key": mask_key(key), "body": response.text[:120]},
            )
        if response.status_code == 429:
            return CheckResult(
                "Pixabay", "media_api", "limited",
                "Rate limit exceeded (100 requests / 60 s). Responses are cached for 24 h.",
                requires_key=True, details={"key": mask_key(key)},
            )
        if not response.ok:
            return CheckResult(
                "Pixabay", "media_api", "degraded",
                f"Unexpected response (HTTP {response.status_code}).", requires_key=True,
            )

        payload = response.json()
        hits = payload.get("hits", [])
        full_access = bool(hits and ("fullHDURL" in hits[0] or "imageURL" in hits[0]))
        headers = {k.lower(): v for k, v in response.headers.items()}

        # Confirm the video endpoint too, since the pipeline depends on it.
        video_ok = False
        video_response = self.session.get(
            "https://pixabay.com/api/videos/",
            params={"key": key, "q": "ocean", "per_page": 3, "safesearch": "true"},
            timeout=CHECK_TIMEOUT,
        )
        get_rate_tracker().record("pixabay", dict(video_response.headers))
        if video_response.ok:
            video_ok = bool(video_response.json().get("hits"))

        tier = "Full API access (original resolution)" if full_access else "Standard access (images up to 1280 px)"
        return CheckResult(
            "Pixabay", "media_api", "ok",
            f"Connected. {tier}. Video endpoint: {'working' if video_ok else 'no results'}.",
            requires_key=True,
            details={
                "key": mask_key(key),
                "access_tier": "full" if full_access else "standard",
                "rate_limit": headers.get("x-ratelimit-limit", "100"),
                "remaining": headers.get("x-ratelimit-remaining", "unknown"),
                "reset_seconds": headers.get("x-ratelimit-reset", "unknown"),
                "videos_1080p": video_ok,
                "images_total": payload.get("total", 0),
            },
        )

    # ------------------------------------------------------------------
    # LLM providers
    # ------------------------------------------------------------------
    def check_9router(self) -> CheckResult:
        """Check the local 9Router endpoint and count its models."""
        url = (self.config.get("router9_url") or "").rstrip("/")
        key = self.config.get("router9_key") or ""
        if not url:
            return CheckResult("9Router (Layer 1)", "llm", "missing", "No base URL configured.", requires_key=True)
        response = self.session.get(
            f"{url}/models",
            headers={"Authorization": f"Bearer {key}"} if key else {},
            timeout=CHECK_TIMEOUT,
        )
        if response.status_code == 401:
            return CheckResult(
                "9Router (Layer 1)", "llm", "invalid", "The API key was rejected (401).",
                requires_key=True, details={"key": mask_key(key), "url": url},
            )
        if not response.ok:
            return CheckResult(
                "9Router (Layer 1)", "llm", "degraded",
                f"Reachable but returned HTTP {response.status_code}.", requires_key=True,
            )
        models = response.json().get("data", [])
        return CheckResult(
            "9Router (Layer 1)", "llm", "ok",
            f"Connected. {len(models)} models available.",
            requires_key=True,
            details={"url": url, "key": mask_key(key),
                     "sample_models": [m.get("id") for m in models[:5]]},
        )

    def check_openrouter(self) -> CheckResult:
        """Check OpenRouter model discovery and count the free models."""
        key = self.config.get("openrouter_key") or ""
        response = self.session.get("https://openrouter.ai/api/v1/models", timeout=CHECK_TIMEOUT)
        if not response.ok:
            return CheckResult(
                "OpenRouter (Layer 2)", "llm", "degraded",
                f"Model list unavailable (HTTP {response.status_code}).",
            )
        models = response.json().get("data", [])
        free = [
            m for m in models
            if float((m.get("pricing") or {}).get("prompt", 1) or 0) == 0
        ]
        if not key:
            return CheckResult(
                "OpenRouter (Layer 2)", "llm", "missing",
                f"Reachable ({len(models)} models, {len(free)} free) but no API key is set.",
                requires_key=True, details={"total_models": len(models), "free_models": len(free)},
            )
        probe = self.session.get(
            "https://openrouter.ai/api/v1/key",
            headers={"Authorization": f"Bearer {key}"},
            timeout=CHECK_TIMEOUT,
        )
        if probe.status_code == 401:
            return CheckResult(
                "OpenRouter (Layer 2)", "llm", "invalid", "The API key was rejected (401).",
                requires_key=True, details={"key": mask_key(key)},
            )
        info = probe.json().get("data", {}) if probe.ok else {}
        return CheckResult(
            "OpenRouter (Layer 2)", "llm", "ok",
            f"Connected. {len(models)} models, {len(free)} free.",
            requires_key=True,
            details={
                "key": mask_key(key), "total_models": len(models), "free_models": len(free),
                "usage": info.get("usage"), "credit_limit": info.get("limit"),
            },
        )

    def check_nvidia(self) -> CheckResult:
        """Check the NVIDIA NIM endpoint."""
        key = self.config.get("nvidia_nim_key") or ""
        url = (self.config.get("nvidia_nim_url") or "").rstrip("/")
        if not key or not url:
            return CheckResult(
                "NVIDIA NIM (Layer 3)", "llm", "missing", "No API key or base URL configured.",
                requires_key=True,
            )
        response = self.session.get(
            f"{url}/models", headers={"Authorization": f"Bearer {key}"}, timeout=CHECK_TIMEOUT
        )
        if response.status_code in (401, 403):
            return CheckResult(
                "NVIDIA NIM (Layer 3)", "llm", "invalid", "The API key was rejected.",
                requires_key=True, details={"key": mask_key(key)},
            )
        if not response.ok:
            return CheckResult(
                "NVIDIA NIM (Layer 3)", "llm", "degraded",
                f"Returned HTTP {response.status_code}.", requires_key=True,
            )
        models = response.json().get("data", [])
        return CheckResult(
            "NVIDIA NIM (Layer 3)", "llm", "ok", f"Connected. {len(models)} models available.",
            requires_key=True, details={"key": mask_key(key), "models": len(models)},
        )

    def check_cloudflare(self) -> CheckResult:
        """Validate the Cloudflare Workers AI credentials and count the text models."""
        account_id = (self.config.get("cloudflare_account_id") or "").strip()
        token = (self.config.get("cloudflare_api_token") or "").strip()
        if not account_id or not token:
            return CheckResult(
                "Cloudflare Workers AI", "llm", "missing",
                "Account ID or API token is not set.", requires_key=True,
                details={"signup": "https://dash.cloudflare.com/profile/api-tokens"},
            )
        response = self.session.get(
            f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/models/search",
            headers={"Authorization": f"Bearer {token}"},
            params={"task": "Text Generation", "per_page": 100},
            timeout=CHECK_TIMEOUT,
        )
        if response.status_code in (400, 401, 403):
            body = response.text.lower()
            hint = "Check both the Account ID and the API token."
            if "route" in body or "object identifier" in body:
                hint = "The Account ID looks wrong (Cloudflare could not route the request)."
            return CheckResult(
                "Cloudflare Workers AI", "llm", "invalid",
                f"Cloudflare rejected the credentials. {hint}", requires_key=True,
                details={"account_id": mask_key(account_id), "token": mask_key(token),
                         "http": response.status_code, "body": response.text[:140]},
            )
        if not response.ok:
            return CheckResult(
                "Cloudflare Workers AI", "llm", "degraded",
                f"Unexpected response (HTTP {response.status_code}).", requires_key=True,
            )
        payload = response.json()
        models = payload.get("result") or []
        info = payload.get("result_info") or {}
        names = [m.get("name") for m in models if m.get("name")]
        return CheckResult(
            "Cloudflare Workers AI", "llm", "ok",
            f"Connected. {info.get('total_count', len(names))} text models available for automatic fallback.",
            requires_key=True,
            details={
                "account_id": mask_key(account_id), "token": mask_key(token),
                "text_models": len(names), "sample_models": names[:6],
                "free_tier": "10,000 neurons per day",
            },
        )

    def check_selected_provider(self) -> CheckResult:
        """Check only the provider the user selected, and list its models."""
        provider_id = self.config.get("llm_provider", "openrouter")
        provider = get_provider(provider_id)
        gaps = missing_fields(provider_id, self.config)
        if gaps:
            return CheckResult(
                f"Selected provider: {provider['name']}", "llm", "missing",
                f"Missing required settings: {', '.join(gaps)}.", requires_key=True,
            )
        from utility.llm.llm_router import SmartLLMRouter

        try:
            router = SmartLLMRouter(self.config)
            models = router.available_models(refresh=True)
        except Exception as exc:  # noqa: BLE001 - report, never raise
            return CheckResult(
                f"Selected provider: {provider['name']}", "llm", "invalid", str(exc)[:160],
                requires_key=True,
            )
        return CheckResult(
            f"Selected provider: {provider['name']}", "llm", "ok",
            f"{len(models)} text models ready. They fall back to each other automatically.",
            requires_key=True, details={"models_preview": models[:8], "total_models": len(models)},
        )

    # ------------------------------------------------------------------
    # Keyless services
    # ------------------------------------------------------------------
    def _simple_check(
        self, name: str, category: str, url: str, must_contain: Optional[str] = None,
        params: Optional[Dict[str, Any]] = None, success_message: str = "Reachable.",
    ) -> CheckResult:
        """Generic reachability probe for a keyless source."""
        response = self.session.get(url, params=params, timeout=CHECK_TIMEOUT)
        if response.status_code == 429:
            return CheckResult(
                name, category, "limited",
                "Rate limited right now (HTTP 429). It will recover on its own; "
                "responses are cached and other sources cover this one.",
            )
        if response.status_code in (401, 403):
            return CheckResult(
                name, category, "degraded",
                f"Blocked by bot protection (HTTP {response.status_code}). "
                "This source is skipped automatically; the fallback chain covers it.",
            )
        if not response.ok:
            return CheckResult(name, category, "offline", f"HTTP {response.status_code}.")
        if must_contain and must_contain.lower() not in response.text.lower():
            return CheckResult(
                name, category, "degraded",
                "Reachable but the expected content was not found (the site layout may have changed).",
            )
        return CheckResult(name, category, "ok", success_message)

    def check_edge_tts(self) -> CheckResult:
        """Verify the voice engines and the local voice library."""
        import asyncio

        import edge_tts

        from utility.audio.tts_engines import edge_tts_version_note, engine_status
        from utility.audio.voice_profiles import VOICE_PROFILES

        version_note = edge_tts_version_note()
        engines = engine_status()
        working = [e for e in engines if e["ok"]]
        names = ", ".join(e["engine"] for e in working) or "none"

        if not working:
            return CheckResult(
                "Voice engines", "engine", "offline",
                "No voice engine is reachable. " + (version_note or ""),
                details={"engines": engines},
            )

        primary_ok = any(e["engine"] == "EdgeTTS" and e["ok"] for e in engines)
        if not primary_ok:
            return CheckResult(
                "Voice engines", "engine", "degraded",
                f"EdgeTTS is unavailable, but the fallback works ({names}). "
                + (version_note or "Videos will still render, with a simpler voice."),
                details={"engines": engines, "fix": version_note},
            )

        try:
            live = {v["ShortName"] for v in asyncio.run(edge_tts.list_voices())}
            missing = [v for v in VOICE_PROFILES if v not in live]
        except Exception:  # noqa: BLE001
            missing = []

        message = f"Connected. {len(working)} engines available ({names})."
        if missing:
            message = f"Connected, but {len(missing)} configured voices are no longer offered."
        return CheckResult(
            "Voice engines", "engine", "degraded" if missing else "ok", message,
            details={"engines": engines, "configured_voices": len(VOICE_PROFILES),
                     "missing_voices": missing[:8], "upgrade_hint": version_note},
        )

    def check_user_agents(self) -> CheckResult:
        """Report the User-Agent rotation pool and what it has learned."""
        from utility.core.user_agents import stats as ua_stats

        info = ua_stats()
        learned = info["learned_hosts"]
        blocked = info["challenge_protected_hosts"]
        message = (
            f"{info['total_agents']} agents in rotation "
            f"({info['browser_agents']} browser, {info['feed_reader_agents']} feed reader). "
            f"{len(learned)} hosts have a known-good agent."
        )
        if blocked:
            message += f" {len(blocked)} hosts use a JavaScript challenge that no agent can pass."
        return CheckResult(
            "User-Agent rotation", "engine", "ok", message,
            details={
                "total_agents": info["total_agents"],
                "learned_hosts": {h: a[:60] for h, a in learned.items()},
                "challenge_protected": blocked,
            },
        )

    def check_google_fonts(self) -> CheckResult:
        """Verify that caption fonts can be downloaded."""
        response = self.session.get(
            "https://fonts.googleapis.com/css2",
            params={"family": "Montserrat:wght@900", "display": "swap"},
            timeout=CHECK_TIMEOUT,
        )
        if not response.ok:
            return CheckResult("Google Fonts", "engine", "offline", f"HTTP {response.status_code}.")
        found = bool(re.search(r"url\(https://[^)]+\.(ttf|otf|woff2)\)", response.text))
        return CheckResult(
            "Google Fonts", "engine", "ok" if found else "degraded",
            "Connected. Caption fonts can be downloaded." if found else "Reachable but no font file was found.",
        )

    def check_ffmpeg(self) -> CheckResult:
        """Verify that the bundled ffmpeg binary works."""
        import subprocess

        from utility.audio.audio_mixer import ffmpeg_binary

        binary = ffmpeg_binary()
        result = subprocess.run([binary, "-version"], capture_output=True, text=True)
        if result.returncode != 0:
            return CheckResult("FFmpeg", "engine", "offline", "The ffmpeg binary could not be executed.")
        version = result.stdout.splitlines()[0] if result.stdout else "unknown"
        return CheckResult("FFmpeg", "engine", "ok", f"Available. {version[:60]}",
                           details={"path": binary})

    def check_whisper_model(self) -> CheckResult:
        """Report whether the faster-whisper base model is cached locally."""
        import os

        home = os.path.expanduser("~")
        candidates = [
            os.path.join(home, ".cache", "huggingface", "hub"),
            os.path.join(home, ".cache", "whisper"),
        ]
        for directory in candidates:
            if os.path.isdir(directory):
                for entry in os.listdir(directory):
                    if "faster-whisper" in entry.lower() or "whisper-base" in entry.lower():
                        return CheckResult(
                            "faster-whisper (captions)", "engine", "ok",
                            "The base model is cached locally; captions run offline.",
                            details={"path": os.path.join(directory, entry)},
                        )
        return CheckResult(
            "faster-whisper (captions)", "engine", "degraded",
            "The base model is not cached yet. It downloads once (about 145 MB) on the first render.",
        )

    # ------------------------------------------------------------------
    def keyless_media_checks(self) -> List[Callable[[], CheckResult]]:
        """Return probes for every keyless media source."""
        return [
            lambda: self._simple_check("Mixkit (video)", "media_free", "https://mixkit.co/free-stock-video/nature/", "mixkit.co"),
            lambda: self._simple_check("Coverr (video)", "media_free", "https://coverr.co/s?q=nature", "coverr"),
            lambda: self._simple_check("SplitShire (video)", "media_free", "https://www.splitshire.com/?s=city", "splitshire"),
            lambda: self._simple_check("Videvo (free video)", "media_free", "https://www.videvo.net/search/nature/?filter=free", "videvo"),
            lambda: self._simple_check("Internet Archive", "media_free", "https://archive.org/advancedsearch.php", params={"q": "mediatype:movies", "rows": 1, "output": "json"}, success_message="Public domain archive reachable."),
            lambda: self._simple_check("NASA Images", "media_free", "https://images-api.nasa.gov/search", params={"q": "mars", "media_type": "image"}, success_message="Public domain imagery reachable."),
            lambda: self._simple_check("Smithsonian Open Access", "media_free", "https://api.si.edu/openaccess/api/v1.0/search", params={"q": "sky", "rows": 1, "api_key": SMITHSONIAN_DEMO_KEY}),
            lambda: self._simple_check("Met Museum", "media_free", "https://collectionapi.metmuseum.org/public/collection/v1/search", params={"q": "sky", "hasImages": "true"}),
            lambda: self._simple_check("Mixkit (music & SFX)", "media_free", "https://mixkit.co/free-stock-music/ambient/", "mixkit"),
            lambda: self._simple_check("Mixkit SFX", "media_free", "https://mixkit.co/free-sound-effects/whoosh/", "mixkit"),
            self.check_freepd,
        ]

    def trend_checks(self) -> List[Callable[[], CheckResult]]:
        """Return probes for every trend source."""
        return [
            lambda: self._simple_check("Google Trends RSS", "trend", "https://trends.google.com/trending/rss?geo=US", "<item>", success_message="Trending searches reachable (15 countries configured)."),
            lambda: self._simple_check("Reddit RSS", "trend", "https://www.reddit.com/r/popular/.rss?limit=5", "<entry"),
            lambda: self._simple_check("Hacker News API", "trend", "https://hacker-news.firebaseio.com/v0/topstories.json"),
            lambda: self._simple_check("Wikipedia In The News", "trend", "https://en.wikipedia.org/w/api.php", params={"action": "parse", "page": "Template:In_the_news", "prop": "text", "format": "json", "formatversion": 2}),
            lambda: self._simple_check("BBC News RSS", "trend", "https://feeds.bbci.co.uk/news/world/rss.xml", "<item>"),
            lambda: self._simple_check("Google News RSS", "trend", "https://news.google.com/rss?hl=en-US&gl=US&ceid=US:en", "<item>"),
            lambda: self._simple_check("Product Hunt feed", "trend", "https://www.producthunt.com/feed"),
            self.check_youtube_trending,
            lambda: self._simple_check("X/Twitter trend mirror", "trend", "https://trends24.in/", "trend"),
        ]

    def check_freepd(self) -> CheckResult:
        """FreePD now renders its track list with JavaScript, so report it accurately."""
        response = self.session.get("https://freepd.com/", timeout=CHECK_TIMEOUT)
        if not response.ok:
            return CheckResult("FreePD (CC0 music)", "media_free", "offline",
                               f"HTTP {response.status_code}.")
        if ".mp3" not in response.text.lower():
            return CheckResult(
                "FreePD (CC0 music)", "media_free", "degraded",
                "Reachable, but the catalogue is rendered with JavaScript so no direct MP3 "
                "links can be scraped. Music comes from Pixabay and Mixkit instead.",
            )
        return CheckResult("FreePD (CC0 music)", "media_free", "ok", "Reachable with direct MP3 links.")

    def check_youtube_trending(self) -> CheckResult:
        """Check the public YouTube trending mirrors used by the trend engine."""
        mirrors = [
            "https://api.piped.private.coffee/trending?region=US",
            "https://pipedapi.adminforge.de/trending?region=US",
            "https://pipedapi.drgns.space/trending?region=US",
        ]
        for mirror in mirrors:
            try:
                response = self.session.get(mirror, timeout=CHECK_TIMEOUT)
                if response.ok and isinstance(response.json(), list) and response.json():
                    host = mirror.split("/")[2]
                    return CheckResult(
                        "YouTube Trending", "trend", "ok",
                        f"Connected through the public mirror {host}.",
                        details={"mirror": host, "items": len(response.json())},
                    )
            except (requests.RequestException, ValueError):
                continue
        return CheckResult(
            "YouTube Trending", "trend", "degraded",
            "All public mirrors are unavailable; the direct scrape fallback will be used.",
        )

    # ------------------------------------------------------------------
    def run_all(self, include_slow: bool = True) -> Dict[str, Any]:
        """Run every check in parallel and return a grouped report."""
        checks: List[Callable[[], CheckResult]] = [
            self.check_selected_provider,
            self.check_pexels,
            self.check_pixabay,
            self.check_9router,
            self.check_openrouter,
            self.check_nvidia,
            self.check_cloudflare,
            self.check_ffmpeg,
            self.check_google_fonts,
            self.check_user_agents,
        ]
        if include_slow:
            checks.append(self.check_edge_tts)
            checks.append(self.check_whisper_model)
        checks.extend(self.keyless_media_checks())
        checks.extend(self.trend_checks())

        results: List[CheckResult] = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
            for result in pool.map(lambda fn: _timed(fn), checks):
                results.append(result)

        grouped: Dict[str, List[Dict[str, Any]]] = {}
        for result in results:
            grouped.setdefault(result.category, []).append(result.as_dict())

        counts: Dict[str, int] = {}
        for result in results:
            counts[result.status] = counts.get(result.status, 0) + 1

        can_generate_video = any(
            r.service in ("Pexels", "Pixabay") and r.status == "ok" for r in results
        ) or any(r.category == "media_free" and r.status == "ok" for r in results)
        can_use_llm = any(
            r.service.startswith("Selected provider") and r.status == "ok" for r in results
        )

        return {
            "groups": grouped,
            "counts": counts,
            "total": len(results),
            "checked_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "ready": {
                "footage": can_generate_video,
                "llm": can_use_llm,
                "voice": any(
                r.service == "Voice engines" and r.status in ("ok", "degraded") for r in results
            ),
                "render": any(r.service == "FFmpeg" and r.status == "ok" for r in results),
            },
            "rate_limits": get_rate_tracker().all(),
        }


CATEGORY_LABELS = {
    "llm": "LLM providers (text generation)",
    "media_api": "Media APIs (your keys)",
    "media_free": "Free media sources (no key needed)",
    "trend": "Trend sources",
    "engine": "Local engines",
    "unknown": "Other",
}
