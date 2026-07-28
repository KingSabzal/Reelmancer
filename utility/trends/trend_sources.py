"""Trend source registry and async fetchers. All sources are free; most need no key."""

from __future__ import annotations

import asyncio
import html
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from xml.etree import ElementTree

import aiohttp

LOGGER = logging.getLogger("trend_sources")
if not LOGGER.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("[%(name)s] %(message)s"))
    LOGGER.addHandler(_h)
LOGGER.setLevel(logging.INFO)

SOURCE_TIMEOUT = 10
from utility.core.user_agents import (
    RETRYABLE_BLOCK_CODES,
    agents_for,
    default_agent,
    headers_for,
    remember_success,
    remember_total_failure,
)

USER_AGENT = default_agent()
HEADERS = headers_for(USER_AGENT)

# The 15 most populous countries, with their Google Trends geo codes.
GOOGLE_TRENDS_COUNTRIES: List[Dict[str, str]] = [
    {"name": "India", "geo": "IN", "hl": "en-IN"},
    {"name": "China", "geo": "HK", "hl": "en-US"},
    {"name": "United States", "geo": "US", "hl": "en-US"},
    {"name": "Indonesia", "geo": "ID", "hl": "en-US"},
    {"name": "Pakistan", "geo": "PK", "hl": "en-US"},
    {"name": "Nigeria", "geo": "NG", "hl": "en-US"},
    {"name": "Brazil", "geo": "BR", "hl": "en-US"},
    {"name": "Bangladesh", "geo": "BD", "hl": "en-US"},
    {"name": "Russia", "geo": "RU", "hl": "en-US"},
    {"name": "Ethiopia", "geo": "ET", "hl": "en-US"},
    {"name": "Mexico", "geo": "MX", "hl": "en-US"},
    {"name": "Japan", "geo": "JP", "hl": "en-US"},
    {"name": "Egypt", "geo": "EG", "hl": "en-US"},
    {"name": "Philippines", "geo": "PH", "hl": "en-US"},
    {"name": "Vietnam", "geo": "VN", "hl": "en-US"},
]

NEWS_RSS_FEEDS: List[Dict[str, str]] = [
    {"name": "BBC", "url": "https://feeds.bbci.co.uk/news/world/rss.xml"},
    {"name": "CNN", "url": "http://rss.cnn.com/rss/edition_world.rss"},
    {"name": "Reuters", "url": "https://news.google.com/rss/search?q=site:reuters.com&hl=en-US&gl=US&ceid=US:en"},
    {"name": "Al Jazeera", "url": "https://www.aljazeera.com/xml/rss/all.xml"},
    {"name": "The Guardian", "url": "https://www.theguardian.com/world/rss"},
    {"name": "Google News", "url": "https://news.google.com/rss?hl=en-US&gl=US&ceid=US:en"},
]

# Interface strings that appear in scraped pages and are not real trends.
UI_NOISE = [
    "try searching", "sign in", "subscribe", "watch later", "share", "settings",
    "your channel", "search with your voice", "keyboard shortcuts", "report history",
    "terms of service", "privacy policy", "how youtube works", "browse channels",
    "no results", "learn more", "accept all", "reject all", "cookie",
]

# Reddit's .json endpoints now reject anonymous clients, so the public RSS feeds
# are used first (no key, no login) with the JSON endpoints kept as a fallback.
REDDIT_RSS_ENDPOINTS = [
    "https://www.reddit.com/r/popular/.rss?limit=50",
    "https://www.reddit.com/r/todayilearned/.rss?limit=25",
    "https://www.reddit.com/r/technology/.rss?limit=25",
]

REDDIT_ENDPOINTS = [
    "https://www.reddit.com/r/popular.json?limit=50",
    "https://old.reddit.com/r/popular/.json?limit=50",
]

REDDIT_HEADERS = {
    "User-Agent": "windows:reelmancer:v1.0 (by /u/local-user)",
    "Accept": "application/json",
}

# Public Piped/Invidious mirrors expose YouTube trending without any API key.
YOUTUBE_MIRRORS = [
    "https://api.piped.private.coffee/trending?region=US",
    "https://pipedapi.adminforge.de/trending?region=US",
    "https://pipedapi.drgns.space/trending?region=US",
    "https://invidious.nerdvpn.de/api/v1/trending?region=US",
]

TREND_SOURCES: Dict[str, Dict[str, Any]] = {
    "google_trends": {"name": "Google Trends (15 countries)", "needs_key": False, "weight": 1.0},
    "twitter": {"name": "Twitter/X Trending", "needs_key": False, "weight": 0.9},
    "reddit": {"name": "Reddit Popular", "needs_key": False, "weight": 0.9},
    "youtube": {"name": "YouTube Trending", "needs_key": False, "weight": 1.0},
    "tiktok": {"name": "TikTok Trending Hashtags", "needs_key": False, "weight": 0.85},
    "news": {"name": "News RSS (BBC, CNN, Reuters, Al Jazeera, Guardian)", "needs_key": False, "weight": 0.7},
    "hackernews": {"name": "Hacker News", "needs_key": False, "weight": 0.6},
    "producthunt": {"name": "Product Hunt", "needs_key": False, "weight": 0.5},
    "wikipedia": {"name": "Wikipedia In The News", "needs_key": False, "weight": 0.6},
}


def _now() -> str:
    """Current UTC timestamp as an ISO string."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _entry(title: str, source: str, category: str = "", extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Normalize one raw trend into the common shape."""
    item = {
        "title": html.unescape(str(title)).strip(),
        "source": source,
        "category": category,
        "fetched_at": _now(),
    }
    item.update(extra or {})
    return item


async def _get(
    session: aiohttp.ClientSession,
    url: str,
    headers: Optional[Dict[str, str]] = None,
    rotate: bool = True,
    **kwargs,
) -> Optional[str]:
    """GET a URL, rotating the User-Agent when the host refuses the current identity.

    Each agent gets a hard 10 second budget. A successful agent is remembered per host
    so later requests start with the one that works.
    """
    if headers is not None and not rotate:
        candidates = [headers.get("User-Agent", default_agent())]
        base_extra = {k: v for k, v in headers.items() if k.lower() != "user-agent"}
    else:
        candidates = agents_for(url)
        base_extra = {k: v for k, v in (headers or {}).items() if k.lower() != "user-agent"}

    last_status: Optional[int] = None
    for index, agent in enumerate(candidates):
        try:
            timeout = aiohttp.ClientTimeout(total=SOURCE_TIMEOUT)
            async with session.get(
                url, timeout=timeout, headers=headers_for(agent, base_extra), **kwargs
            ) as response:
                if response.status == 200:
                    text = await response.text()
                    if index > 0:
                        LOGGER.info(
                            "%s accepted User-Agent #%d after %d refusals.",
                            host_label(url), index + 1, index,
                        )
                    remember_success(url, agent)
                    return text

                last_status = response.status
                if response.status not in RETRYABLE_BLOCK_CODES:
                    return None
                # Refused this identity: fall through and try the next agent.
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            text = str(exc)
            if "Cannot connect to host" in text or "Connect call failed" in text:
                # Network-level block: no User-Agent can fix this, so stop immediately.
                LOGGER.info(
                    "%s is unreachable from this network (blocked or offline). "
                    "Skipping it; the other sources still provide trends.",
                    host_label(url),
                )
                return None
            last_status = None

    remember_total_failure(url)
    if last_status:
        LOGGER.info(
            "%s refused all %d User-Agents tried (last status %s).",
            host_label(url), len(candidates), last_status,
        )
    return None


def host_label(url: str) -> str:
    """Short hostname for log messages."""
    return url.split("/")[2] if "//" in url else url


# ----------------------------------------------------------------------
# Individual sources
# ----------------------------------------------------------------------
async def fetch_google_trends(session: aiohttp.ClientSession) -> List[Dict[str, Any]]:
    """Daily trending searches for the 15 most populous countries (RSS, no key)."""
    async def one(country: Dict[str, str]) -> List[Dict[str, Any]]:
        """Fetch a single source entry."""
        url = (
            "https://trends.google.com/trending/rss"
            f"?geo={country['geo']}"
        )
        text = await _get(session, url)
        if not text:
            return []
        results: List[Dict[str, Any]] = []
        try:
            root = ElementTree.fromstring(text)
            for item in root.iter("item"):
                title = (item.findtext("title") or "").strip()
                traffic = ""
                for child in item:
                    if child.tag.endswith("approx_traffic"):
                        traffic = (child.text or "").strip()
                if title:
                    results.append(
                        _entry(title, "google_trends", extra={
                            "country": country["name"],
                            "approx_traffic": traffic,
                        })
                    )
        except ElementTree.ParseError:
            return []
        return results[:20]

    batches = await asyncio.gather(*[one(country) for country in GOOGLE_TRENDS_COUNTRIES])
    return [item for batch in batches for item in batch]


async def fetch_reddit(session: aiohttp.ClientSession) -> List[Dict[str, Any]]:
    """Top posts from Reddit via the public RSS feeds, with a JSON fallback."""
    results: List[Dict[str, Any]] = []

    for url in REDDIT_RSS_ENDPOINTS:
        text = await _get(session, url)
        if not text:
            continue
        try:
            root = ElementTree.fromstring(text)
        except ElementTree.ParseError:
            continue
        namespace = "{http://www.w3.org/2005/Atom}"
        for node in root.iter(f"{namespace}entry"):
            title = (node.findtext(f"{namespace}title") or "").strip()
            if not title:
                continue
            subreddit = ""
            category = node.find(f"{namespace}category")
            if category is not None:
                subreddit = category.attrib.get("label", "") or category.attrib.get("term", "")
            results.append(_entry(title, "reddit", extra={"subreddit": subreddit, "score": 0}))

    if results:
        return results[:80]

    for url in REDDIT_ENDPOINTS:
        text = await _get(session, url)
        if not text:
            continue
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            continue
        for child in payload.get("data", {}).get("children", []):
            data = child.get("data", {})
            title = data.get("title")
            if not title or data.get("over_18"):
                continue
            results.append(
                _entry(title, "reddit", extra={
                    "score": data.get("score", 0),
                    "comments": data.get("num_comments", 0),
                    "subreddit": data.get("subreddit", ""),
                })
            )
        if results:
            break
    return results


async def fetch_youtube_trending(session: aiohttp.ClientSession) -> List[Dict[str, Any]]:
    """YouTube trending titles via public Piped/Invidious mirrors, then direct scraping."""
    results: List[Dict[str, Any]] = []
    for url in YOUTUBE_MIRRORS:
        text = await _get(session, url)
        if not text:
            continue
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, list):
            continue
        for item in payload:
            title = (item.get("title") or "").strip()
            if len(title) < 8:
                continue
            results.append(
                _entry(title, "youtube", extra={
                    "views": item.get("views") or item.get("viewCount") or 0,
                    "channel": item.get("uploaderName") or item.get("author") or "",
                })
            )
        if results:
            return results[:50]

    text = await _get(session, "https://www.youtube.com/feed/trending?gl=US&hl=en")
    if not text:
        return results
    titles = re.findall(r'"title":\{"runs":\[\{"text":"(.*?)"\}\]', text)
    titles += re.findall(r'"title":\{"accessibility".*?"simpleText":"(.*?)"', text)
    seen: set[str] = set()
    for raw in titles:
        try:
            title = json.loads(f'"{raw}"')
        except json.JSONDecodeError:
            title = raw
        key = title.lower().strip()
        if len(title) < 12 or key in seen:
            continue
        if any(noise in key for noise in UI_NOISE):
            continue
        seen.add(key)
        results.append(_entry(title, "youtube"))
        if len(results) >= 50:
            break
    return results


async def fetch_twitter(session: aiohttp.ClientSession) -> List[Dict[str, Any]]:
    """Trending hashtags via free public mirrors of X/Twitter trends."""
    mirrors = [
        "https://trends24.in/",
        "https://getdaytrends.com/",
    ]
    results: List[Dict[str, Any]] = []
    for url in mirrors:
        text = await _get(session, url)
        if not text:
            continue
        candidates = re.findall(r'<a[^>]*>([#\w][^<]{2,60})</a>', text)
        for candidate in candidates:
            title = html.unescape(candidate).strip()
            if not title or title.lower().startswith(("http", "privacy", "cookie", "about")):
                continue
            if len(title) < 3 or len(title.split()) > 6:
                continue
            results.append(_entry(title, "twitter"))
        if results:
            break
    return results[:50]


async def fetch_tiktok(session: aiohttp.ClientSession) -> List[Dict[str, Any]]:
    """Trending TikTok hashtags scraped from public discovery pages."""
    urls = [
        "https://www.tiktok.com/discover",
        "https://ads.tiktok.com/business/creativecenter/hashtag/pk/pc/en",
    ]
    results: List[Dict[str, Any]] = []
    for url in urls:
        text = await _get(session, url)
        if not text:
            continue
        tags = re.findall(r'#([A-Za-z][A-Za-z0-9_]{3,28})', text)
        seen: set[str] = set()
        for tag in tags:
            key = tag.lower()
            if key in seen:
                continue
            seen.add(key)
            results.append(_entry("#" + tag, "tiktok"))
        if results:
            break
    return results[:40]


async def fetch_news(session: aiohttp.ClientSession) -> List[Dict[str, Any]]:
    """Headlines from free news RSS feeds."""
    async def one(feed: Dict[str, str]) -> List[Dict[str, Any]]:
        """Fetch a single source entry."""
        text = await _get(session, feed["url"])
        if not text:
            return []
        items: List[Dict[str, Any]] = []
        try:
            root = ElementTree.fromstring(text)
            for node in root.iter("item"):
                title = (node.findtext("title") or "").strip()
                if title:
                    items.append(
                        _entry(title, "news", extra={"outlet": feed["name"],
                                                     "published": node.findtext("pubDate") or ""})
                    )
        except ElementTree.ParseError:
            return []
        return items[:15]

    batches = await asyncio.gather(*[one(feed) for feed in NEWS_RSS_FEEDS])
    return [item for batch in batches for item in batch]


async def fetch_hackernews(session: aiohttp.ClientSession) -> List[Dict[str, Any]]:
    """Top Hacker News stories (official free API)."""
    text = await _get(session, "https://hacker-news.firebaseio.com/v0/topstories.json")
    if not text:
        return []
    try:
        ids = json.loads(text)[:25]
    except json.JSONDecodeError:
        return []

    async def one(item_id: int) -> Optional[Dict[str, Any]]:
        """Fetch a single source entry."""
        body = await _get(session, f"https://hacker-news.firebaseio.com/v0/item/{item_id}.json")
        if not body:
            return None
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            return None
        if not data or not data.get("title"):
            return None
        return _entry(data["title"], "hackernews", category="Technology",
                      extra={"score": data.get("score", 0), "comments": data.get("descendants", 0)})

    items = await asyncio.gather(*[one(item_id) for item_id in ids])
    return [item for item in items if item]


async def fetch_producthunt(session: aiohttp.ClientSession) -> List[Dict[str, Any]]:
    """Today's Product Hunt launches via the public RSS feed."""
    text = await _get(session, "https://www.producthunt.com/feed")
    if not text:
        return []
    results: List[Dict[str, Any]] = []
    try:
        root = ElementTree.fromstring(text)
        for node in root.iter("{http://www.w3.org/2005/Atom}entry"):
            title = (node.findtext("{http://www.w3.org/2005/Atom}title") or "").strip()
            if title:
                results.append(_entry(title, "producthunt", category="Technology"))
    except ElementTree.ParseError:
        for title in re.findall(r"<title>(.*?)</title>", text)[1:25]:
            results.append(_entry(title, "producthunt", category="Technology"))
    return results[:25]


async def fetch_wikipedia(session: aiohttp.ClientSession) -> List[Dict[str, Any]]:
    """Wikipedia 'In the news' items from the main page API."""
    url = (
        "https://en.wikipedia.org/w/api.php?action=parse&page=Template:In_the_news"
        "&prop=text&format=json&formatversion=2"
    )
    text = await _get(session, url)
    if not text:
        return []
    try:
        html_text = json.loads(text)["parse"]["text"]
    except (json.JSONDecodeError, KeyError):
        return []
    items = re.findall(r"<li>(.*?)</li>", html_text, flags=re.DOTALL)
    results: List[Dict[str, Any]] = []
    for raw in items[:15]:
        clean = html.unescape(re.sub(r"<[^>]+>", "", raw)).strip()
        if 15 < len(clean) < 220:
            results.append(_entry(clean, "wikipedia"))
    return results


FETCHERS = {
    "google_trends": fetch_google_trends,
    "twitter": fetch_twitter,
    "reddit": fetch_reddit,
    "youtube": fetch_youtube_trending,
    "tiktok": fetch_tiktok,
    "news": fetch_news,
    "hackernews": fetch_hackernews,
    "producthunt": fetch_producthunt,
    "wikipedia": fetch_wikipedia,
}


async def fetch_all(progress=None) -> Dict[str, List[Dict[str, Any]]]:
    """Fetch every source in parallel with graceful degradation."""
    results: Dict[str, List[Dict[str, Any]]] = {}
    async with aiohttp.ClientSession() as session:
        async def run(key: str):
            """Fetch one source and record its result."""
            if progress:
                progress(key, "start")
            try:
                data = await FETCHERS[key](session)
            except Exception as exc:  # noqa: BLE001 - a broken source must not stop the scan
                LOGGER.warning("Source %s failed: %s", key, exc)
                data = []
            results[key] = data
            if progress:
                progress(key, f"{len(data)} items")

        await asyncio.gather(*[run(key) for key in FETCHERS])

    working = [k for k, v in results.items() if v]
    blocked = [k for k, v in results.items() if not v]
    LOGGER.info(
        "Trend scan finished: %d of %d sources responded (%s).%s",
        len(working), len(results), ", ".join(working) or "none",
        f" Unreachable from this network: {', '.join(blocked)}." if blocked else "",
    )
    return results


def fetch_all_sync(progress=None) -> Dict[str, List[Dict[str, Any]]]:
    """Blocking wrapper around fetch_all for Streamlit."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop and loop.is_running():
        import nest_asyncio  # type: ignore

        nest_asyncio.apply()
        return loop.run_until_complete(fetch_all(progress))
    return asyncio.run(fetch_all(progress))
