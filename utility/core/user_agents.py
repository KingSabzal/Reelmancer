"""User-Agent rotation with per-host learning.

Testing showed that a User-Agent helps for *some* blocks and not for others:

* Reddit RSS answers 429 for browser agents but 200 for feed-reader agents such as
  Feedly. Rotating agents therefore genuinely recovers this source.
* Videvo and the Pixabay audio pages return 403 for every agent, because they use a
  JavaScript bot challenge that no header can satisfy. Rotating there only wastes time.

So the rotation is adaptive: each host remembers which agent last worked, tries that
one first, and stops early when a host is clearly challenge-protected rather than
agent-filtered.
"""

from __future__ import annotations

import logging
import random
import threading
from typing import Dict, List, Optional

LOGGER = logging.getLogger("user_agents")
if not LOGGER.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("[%(name)s] %(message)s"))
    LOGGER.addHandler(_h)
LOGGER.setLevel(logging.INFO)


def _build_browser_agents() -> List[str]:
    """Generate realistic desktop browser agents across versions and platforms."""
    agents: List[str] = []

    windows = ["Windows NT 10.0; Win64; x64"]
    macos = ["Macintosh; Intel Mac OS X 10_15_7", "Macintosh; Intel Mac OS X 14_5"]
    linux = ["X11; Linux x86_64", "X11; Ubuntu; Linux x86_64"]

    # Chrome and the Chromium family (Edge, Opera, Brave share the base token).
    for major in range(124, 145):
        for platform in windows + macos + linux:
            agents.append(
                f"Mozilla/5.0 ({platform}) AppleWebKit/537.36 (KHTML, like Gecko) "
                f"Chrome/{major}.0.0.0 Safari/537.36"
            )
    for major in range(124, 145, 2):
        agents.append(
            f"Mozilla/5.0 ({windows[0]}) AppleWebKit/537.36 (KHTML, like Gecko) "
            f"Chrome/{major}.0.0.0 Safari/537.36 Edg/{major}.0.0.0"
        )

    # Firefox
    for major in range(121, 137):
        for platform, token in (
            (f"Windows NT 10.0; Win64; x64; rv:{major}.0", f"{major}.0"),
            (f"Macintosh; Intel Mac OS X 14.5; rv:{major}.0", f"{major}.0"),
            (f"X11; Linux x86_64; rv:{major}.0", f"{major}.0"),
        ):
            agents.append(f"Mozilla/5.0 ({platform}) Gecko/20100101 Firefox/{token}")

    # Safari
    for version in ("16.6", "17.0", "17.4", "17.6", "18.0", "18.2"):
        agents.append(
            f"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
            f"(KHTML, like Gecko) Version/{version} Safari/605.1.15"
        )

    # Mobile
    for version in ("16.6", "17.4", "17.6", "18.0"):
        agents.append(
            f"Mozilla/5.0 (iPhone; CPU iPhone OS {version.replace('.', '_')} like Mac OS X) "
            f"AppleWebKit/605.1.15 (KHTML, like Gecko) Version/{version} Mobile/15E148 Safari/604.1"
        )
    for android, chrome in (("13", 127), ("14", 131), ("15", 138)):
        agents.append(
            f"Mozilla/5.0 (Linux; Android {android}; Pixel 8) AppleWebKit/537.36 "
            f"(KHTML, like Gecko) Chrome/{chrome}.0.0.0 Mobile Safari/537.36"
        )

    # Deduplicate while preserving order.
    seen: set = set()
    unique: List[str] = []
    for agent in agents:
        if agent not in seen:
            seen.add(agent)
            unique.append(agent)
    return unique


# Feed readers are declared politely and are what Reddit and several news hosts accept.
FEED_READER_AGENTS: List[str] = [
    "Feedly/1.0 (+http://www.feedly.com/fetcher.html; like FeedFetcher-Google)",
    "Mozilla/5.0 (compatible; Feedbin feed-id:1 - 1 subscribers)",
    "Inoreader/1.0 (+http://www.inoreader.com/feed-fetcher; 5 subscribers)",
    "NewsBlur Feed Fetcher - 3 subscribers",
    "Mozilla/5.0 (compatible; theoldreader.com; 2 subscribers)",
    "SimplePie/1.5.6 (Feed Parser; http://simplepie.org)",
    "Liferea/1.13.5 (Linux; en_US; https://lzone.de/liferea/)",
    "Akregator/5.22.3; syndication",
    "Miniflux/2.1.0 (+https://miniflux.app)",
    "FreshRSS/1.24.0 (Linux; https://freshrss.org)",
    "Tiny Tiny RSS/21.11 (http://tt-rss.org/)",
    "rss-parser/3.13.0",
    "python-feedparser/6.0.11 +https://github.com/kurtmckee/feedparser/",
    "Mozilla/5.0 (compatible; NetNewsWire/6.1; +https://netnewswire.com/)",
    "Reeder/5.0 (+https://reederapp.com)",
]

# Declared API clients, accepted by hosts that dislike anonymous browser traffic.
API_CLIENT_AGENTS: List[str] = [
    "windows:reelmancer:v1.0 (by /u/local-user)",
    "web:reelmancer:v1.0 (open source video tool)",
    "python-requests/2.32.3",
    "aiohttp/3.10.5",
    "okhttp/4.12.0",
]

BROWSER_AGENTS: List[str] = _build_browser_agents()
ALL_AGENTS: List[str] = BROWSER_AGENTS + FEED_READER_AGENTS + API_CLIENT_AGENTS

# Hosts where a feed-reader identity works far better than a browser identity.
FEED_FIRST_HOSTS = (
    "reddit.com", "old.reddit.com", "feeds.bbci.co.uk", "theguardian.com",
    "aljazeera.com", "producthunt.com", "news.google.com", "rss.cnn.com",
    "trends.google.com",
)

# Status codes that mean "this identity was refused, another one may work".
RETRYABLE_BLOCK_CODES = (401, 403, 405, 406, 418, 429, 503)

_LOCK = threading.Lock()
_HOST_MEMORY: Dict[str, str] = {}          # host -> agent that last succeeded
_HOST_HOPELESS: Dict[str, int] = {}        # host -> consecutive full-rotation failures

# After this many complete failures a host is treated as challenge-protected and only
# one attempt is made, so scans stay fast instead of retrying 200 agents every time.
HOPELESS_THRESHOLD = 2


def host_of(url: str) -> str:
    """Extract the hostname from a URL."""
    if "//" not in url:
        return url
    return url.split("/")[2].lower()


def default_agent() -> str:
    """A stable, modern desktop agent for ordinary requests."""
    return (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36"
    )


def random_agent() -> str:
    """Return a random agent from the full pool."""
    return random.choice(ALL_AGENTS)


def agents_for(url: str, limit: int = 8) -> List[str]:
    """Return the agents to try for a URL, best candidate first.

    The list is deliberately short: testing showed that when a host filters by agent,
    a working agent appears within the first few tries, and when it uses a JavaScript
    challenge no agent ever works.
    """
    host = host_of(url)

    if _HOST_HOPELESS.get(host, 0) >= HOPELESS_THRESHOLD:
        # Challenge-protected: one polite attempt only.
        remembered = _HOST_MEMORY.get(host)
        return [remembered or default_agent()]

    ordered: List[str] = []

    remembered = _HOST_MEMORY.get(host)
    if remembered:
        ordered.append(remembered)

    if any(host.endswith(h) or h in host for h in FEED_FIRST_HOSTS):
        ordered.extend(FEED_READER_AGENTS[:6])
        ordered.extend(API_CLIENT_AGENTS[:2])
        ordered.extend(random.sample(BROWSER_AGENTS, k=min(4, len(BROWSER_AGENTS))))
    else:
        ordered.append(default_agent())
        ordered.extend(random.sample(BROWSER_AGENTS, k=min(6, len(BROWSER_AGENTS))))
        ordered.extend(FEED_READER_AGENTS[:2])

    seen: set = set()
    unique: List[str] = []
    for agent in ordered:
        if agent and agent not in seen:
            seen.add(agent)
            unique.append(agent)
    return unique[:limit]


def remember_success(url: str, agent: str) -> None:
    """Record the agent that worked so the next request starts with it."""
    host = host_of(url)
    with _LOCK:
        _HOST_MEMORY[host] = agent
        _HOST_HOPELESS.pop(host, None)


def remember_total_failure(url: str) -> None:
    """Record that every agent failed for this host."""
    host = host_of(url)
    with _LOCK:
        _HOST_HOPELESS[host] = _HOST_HOPELESS.get(host, 0) + 1
        if _HOST_HOPELESS[host] == HOPELESS_THRESHOLD:
            LOGGER.info(
                "%s refused every User-Agent, so it is protected by a JavaScript "
                "challenge rather than agent filtering. Future scans will not retry it.",
                host,
            )


def headers_for(agent: str, extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """Build a complete, believable header set for an agent."""
    headers = {
        "User-Agent": agent,
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,"
            "application/rss+xml;q=0.9,image/avif,image/webp,*/*;q=0.8"
        ),
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    }
    if "Chrome/" in agent and "Mobile" not in agent:
        version = agent.split("Chrome/")[1].split(".")[0]
        headers["sec-ch-ua"] = (
            f'"Chromium";v="{version}", "Not(A:Brand";v="24", "Google Chrome";v="{version}"'
        )
        headers["sec-ch-ua-mobile"] = "?0"
        headers["sec-ch-ua-platform"] = '"Windows"' if "Windows" in agent else '"macOS"'
        headers["Sec-Fetch-Dest"] = "document"
        headers["Sec-Fetch-Mode"] = "navigate"
        headers["Sec-Fetch-Site"] = "none"
    if extra:
        headers.update(extra)
    return headers


def stats() -> Dict[str, object]:
    """Return rotation statistics for the connection status dashboard."""
    return {
        "total_agents": len(ALL_AGENTS),
        "browser_agents": len(BROWSER_AGENTS),
        "feed_reader_agents": len(FEED_READER_AGENTS),
        "api_client_agents": len(API_CLIENT_AGENTS),
        "learned_hosts": dict(_HOST_MEMORY),
        "challenge_protected_hosts": sorted(
            host for host, count in _HOST_HOPELESS.items() if count >= HOPELESS_THRESHOLD
        ),
    }


def reset_memory() -> None:
    """Forget every learned agent and block record."""
    with _LOCK:
        _HOST_MEMORY.clear()
        _HOST_HOPELESS.clear()
