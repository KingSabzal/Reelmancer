"""Registry of approved media sources. Zero-attribution licences only.

Every source listed here is CC0, Public Domain, Pexels, Pixabay or Mixkit licensed
and requires no credit in the finished video. Paid services and AI video generators
are intentionally absent.
"""

from __future__ import annotations

from typing import Any, Dict, List

VIDEO_SOURCES: List[Dict[str, Any]] = [
    {"priority": 1, "id": "pexels", "name": "Pexels", "kind": "api",
     "endpoint": "https://api.pexels.com/videos/search", "license": "Pexels License",
     "needs_key": True, "key_field": "pexels_api_key",
     "signup": "https://www.pexels.com/api/new/"},
    {"priority": 2, "id": "pixabay", "name": "Pixabay", "kind": "api",
     "endpoint": "https://pixabay.com/api/videos/", "license": "Pixabay License",
     "needs_key": True, "key_field": "pixabay_api_key",
     "signup": "https://pixabay.com/api/key/"},
    {"priority": 3, "id": "mixkit", "name": "Mixkit", "kind": "scrape",
     "endpoint": "https://mixkit.co/free-stock-video/{query}/", "license": "Mixkit License",
     "needs_key": False},
    {"priority": 4, "id": "coverr", "name": "Coverr", "kind": "scrape",
     "endpoint": "https://coverr.co/s?q={query}", "license": "Coverr (CC0-like)",
     "needs_key": False},
    {"priority": 5, "id": "dareful", "name": "Dareful", "kind": "scrape",
     "endpoint": "http://dareful.com/?s={query}", "license": "CC0", "needs_key": False},
    {"priority": 6, "id": "lifeofvids", "name": "Life of Vids", "kind": "scrape",
     "endpoint": "https://www.lifeofvids.com/?s={query}", "license": "Public Domain",
     "needs_key": False},
    {"priority": 7, "id": "splitshire", "name": "SplitShire", "kind": "scrape",
     "endpoint": "https://www.splitshire.com/?s={query}", "license": "CC0", "needs_key": False},
    {"priority": 8, "id": "videvo", "name": "Videvo (free only)", "kind": "scrape",
     "endpoint": "https://www.videvo.net/search/{query}/?filter=free", "license": "Videvo free / CC0",
     "needs_key": False},
    {"priority": 18, "id": "archive", "name": "Internet Archive / Prelinger", "kind": "api",
     "endpoint": "https://archive.org/advancedsearch.php", "license": "Public Domain",
     "needs_key": False},
]

IMAGE_SOURCES: List[Dict[str, Any]] = [
    {"priority": 1, "id": "pexels_img", "name": "Pexels Photos", "kind": "api",
     "endpoint": "https://api.pexels.com/v1/search", "license": "Pexels License",
     "needs_key": True, "key_field": "pexels_api_key"},
    {"priority": 2, "id": "pixabay_img", "name": "Pixabay Photos", "kind": "api",
     "endpoint": "https://pixabay.com/api/", "license": "Pixabay License",
     "needs_key": True, "key_field": "pixabay_api_key"},
    {"priority": 9, "id": "nasa", "name": "NASA Images", "kind": "api",
     "endpoint": "https://images-api.nasa.gov/search", "license": "Public Domain",
     "needs_key": False},
    {"priority": 10, "id": "smithsonian", "name": "Smithsonian Open Access", "kind": "api",
     "endpoint": "https://api.si.edu/openaccess/api/v1.0/search", "license": "CC0",
     "needs_key": False},
    {"priority": 11, "id": "met", "name": "Met Museum", "kind": "api",
     "endpoint": "https://collectionapi.metmuseum.org/public/collection/v1/search",
     "license": "CC0 (Open Access)", "needs_key": False},
    {"priority": 12, "id": "rijksmuseum", "name": "Rijksmuseum", "kind": "api",
     "endpoint": "https://www.rijksmuseum.nl/api/en/collection", "license": "Public Domain",
     "needs_key": False},
    {"priority": 13, "id": "nypl", "name": "NYPL Digital Collections", "kind": "api",
     "endpoint": "https://api.nypl.org/api/v1/items/search", "license": "Public Domain",
     "needs_key": False},
    {"priority": 15, "id": "noaa", "name": "NOAA Photo Library", "kind": "scrape",
     "endpoint": "https://www.noaa.gov/search?query={query}", "license": "Public Domain",
     "needs_key": False},
    {"priority": 16, "id": "usgs", "name": "USGS Multimedia", "kind": "scrape",
     "endpoint": "https://www.usgs.gov/search?keywords={query}", "license": "Public Domain",
     "needs_key": False},
    {"priority": 17, "id": "nps", "name": "National Park Service", "kind": "scrape",
     "endpoint": "https://www.nps.gov/search/?query={query}", "license": "Public Domain",
     "needs_key": False},
]

MUSIC_SOURCES: List[Dict[str, Any]] = [
    {"priority": 1, "id": "yt_audio_library", "name": "YouTube Audio Library", "kind": "scrape",
     "endpoint": "https://studio.youtube.com/channel/UC/music", "license": "YouTube Audio Library (no attribution tracks only)",
     "needs_key": False},
    {"priority": 2, "id": "pixabay_music", "name": "Pixabay Music", "kind": "api",
     "endpoint": "https://pixabay.com/api/", "license": "Pixabay License",
     "needs_key": True, "key_field": "pixabay_api_key"},
    {"priority": 3, "id": "mixkit_music", "name": "Mixkit Music", "kind": "scrape",
     "endpoint": "https://mixkit.co/free-stock-music/{query}/", "license": "Mixkit License",
     "needs_key": False},
    {"priority": 4, "id": "freepd", "name": "FreePD", "kind": "scrape",
     "endpoint": "https://freepd.com/", "license": "CC0", "needs_key": False},
]

SFX_SOURCES: List[Dict[str, Any]] = [
    {"priority": 1, "id": "yt_audio_library_sfx", "name": "YouTube Audio Library SFX",
     "kind": "scrape", "endpoint": "https://studio.youtube.com/channel/UC/music",
     "license": "YouTube Audio Library", "needs_key": False},
    {"priority": 2, "id": "pixabay_sfx", "name": "Pixabay Sound Effects", "kind": "api",
     "endpoint": "https://pixabay.com/api/", "license": "Pixabay License",
     "needs_key": True, "key_field": "pixabay_api_key"},
    {"priority": 3, "id": "mixkit_sfx", "name": "Mixkit Sound Effects", "kind": "scrape",
     "endpoint": "https://mixkit.co/free-sound-effects/{query}/", "license": "Mixkit License",
     "needs_key": False},
    {"priority": 4, "id": "freepd_sfx", "name": "FreePD SFX", "kind": "scrape",
     "endpoint": "https://freepd.com/", "license": "CC0", "needs_key": False},
]

# Video style -> music mood keywords used for automatic track selection.
STYLE_MUSIC_MAPPING: Dict[str, List[str]] = {}


def _build_music_mapping() -> None:
    """Derive the style->music mapping from the video style library."""
    from utility.content.video_styles import VIDEO_STYLES  # local import to avoid a cycle

    manual = {
        "Cinematic": ["cinematic", "epic", "dramatic", "orchestral"],
        "Horror": ["dark", "suspense", "mysterious", "creepy"],
        "Comedy": ["comedy", "upbeat", "light", "playful"],
        "Gaming": ["electronic", "energetic", "trap", "intense"],
        "Meditation": ["ambient", "calm", "relaxing", "meditation"],
        "Documentary": ["ambient", "documentary", "inspiring", "calm"],
        "Tech Review": ["electronic", "modern", "tech", "corporate"],
        "Motivational": ["epic", "inspiring", "uplifting", "cinematic"],
    }
    for name, style in VIDEO_STYLES.items():
        STYLE_MUSIC_MAPPING[name] = manual.get(name, list(style["music_mood"]))


_build_music_mapping()

from utility.core.user_agents import default_agent, headers_for

USER_AGENT = default_agent()
HEADERS = headers_for(USER_AGENT)


def music_moods_for_style(style_name: str) -> List[str]:
    """Return music mood keywords for a style."""
    return STYLE_MUSIC_MAPPING.get(style_name, ["ambient", "cinematic"])


def all_sources() -> Dict[str, List[Dict[str, Any]]]:
    """Return every registered source grouped by media type."""
    return {
        "video": VIDEO_SOURCES,
        "image": IMAGE_SOURCES,
        "music": MUSIC_SOURCES,
        "sfx": SFX_SOURCES,
    }
