"""MediaSourceManager: search and download stock media across zero-attribution sources.

Clips are matched per timed segment: Pexels is searched first for an exact resolution
match, and the remaining zero-attribution sources are tried in order as a fallback
chain. Paid stock domains are hard-blocked even when a link to one appears on an
otherwise approved page.
"""

from __future__ import annotations

import logging
import os
import re
import tempfile
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote_plus

import requests

from utility.core.api_cache import cached_get
from utility.core.config_manager import get_config
from utility.media.media_sources import HEADERS, music_moods_for_style
from utility.content.video_styles import get_style

LOGGER = logging.getLogger("media_manager")
if not LOGGER.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("[%(name)s] %(message)s"))
    LOGGER.addHandler(_h)
LOGGER.setLevel(logging.INFO)

REQUEST_TIMEOUT = 20

# Smithsonian Open Access accepts the public data.gov demo key without registration.
SMITHSONIAN_API_KEY = "DEMO_KEY"

# Domains that host paid or attribution-required stock. A link from any of these is
# never accepted, even if it appears on an approved free site (embedded ads, widgets).
BLOCKED_DOMAINS = (
    "istockphoto.com", "shutterstock.com", "gettyimages.com", "adobe.com",
    "stock.adobe", "artgrid.io", "artlist.io", "storyblocks.com", "envato",
    "dissolve.com", "pond5.com", "alamy.com", "depositphotos.com", "vecteezy.com",
    "motionarray.com", "filmsupply.com", "musicbed.com", "epidemicsound.com",
)


# Asset name fragments that must never be used: AI-generated footage is forbidden by
# the project rules, and paywall/UI assets are not real stock clips.
BLOCKED_ASSET_MARKERS = (
    "ai-generation", "ai_generation", "ai-generated", "aigenerated", "midjourney",
    "runway", "sora-", "paywall", "subscription-modal", "placeholder", "watermark",
    "logo-animation", "promo-banner", "sample-preview",
)


def is_allowed_url(url: str) -> bool:
    """Reject paid sources, AI-generated footage and site UI assets."""
    lowered = (url or "").lower()
    if not lowered:
        return False
    if any(domain in lowered for domain in BLOCKED_DOMAINS):
        return False
    return not any(marker in lowered for marker in BLOCKED_ASSET_MARKERS)


def download_file(url: str, filename: str, kind: str = "video", max_attempts: int = 6) -> str:
    """Download a remote file, retrying and resuming through network problems.

    A transient failure never loses the clip: the download resumes with HTTP Range
    requests and retries with exponential backoff until the file is verified
    complete. Permanent failures are reported immediately instead of being retried.
    """
    from utility.core.resilient_download import download_with_retries

    result = download_with_retries(url, filename, kind=kind, max_attempts=max_attempts)
    if not result.ok:
        raise RuntimeError(
            f"Download did not complete after {result.attempts} attempts: {result.reason}"
        )
    return filename


# How far a clip may slide down the relevance order to gain a better duration.
# At 3.0 a clip can overtake at most three better-matching results, so duration
# still matters but can never override relevance outright.
RELEVANCE_DURATION_WEIGHT = 3.0


def merge_empty_intervals(segments):
    """Merge intervals that have no clip with the previous valid one.

    A skipped interval is not harmless. The renderer composites clips onto a black
    background, so any slot that reaches it without a clip shows as a black flash
    at the join between two shots. Every interval must leave this function owning
    a clip.

    The forward merge covers gaps that have a clip before them. A gap at the very
    start has nothing before it, so it is filled backwards from the first clip that
    does exist; otherwise the video opens on black.
    """
    if segments is None:
        LOGGER.warning("No background videos available to merge.")
        return None

    merged = []
    i = 0
    while i < len(segments):
        interval, url = segments[i]
        if url is None:
            j = i + 1
            while j < len(segments) and segments[j][1] is None:
                j += 1

            if i > 0 and merged:
                prev_interval, prev_url = merged[-1]
                if prev_url is not None and prev_interval[1] == interval[0]:
                    merged[-1] = [[prev_interval[0], segments[j - 1][0][1]], prev_url]
                else:
                    merged.append([interval, prev_url])
            else:
                # Leading gap. Keep the whole run as one interval so the backward
                # fill below covers it in a single piece: appending only the first
                # interval of the run used to drop the remainder of the timeline.
                merged.append([[interval[0], segments[j - 1][0][1]], None])
            i = j
        else:
            merged.append([interval, url])
            i += 1

    # Backward fill: borrow the first clip that exists for any leading gap.
    first_url = next((url for _interval, url in merged if url is not None), None)
    if first_url is not None:
        for index, (interval, url) in enumerate(merged):
            if url is not None:
                break
            merged[index] = [interval, first_url]

    return merged


class MediaSourceManager:
    """Searches every approved source in priority order until a clip is found."""

    def __init__(self, config=None):
        self.config = config or get_config()
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        # Not a licence requirement (all sources are zero-attribution), but Pixabay
        # asks API users to show where results came from, so we track the providers.
        self.sources_used: List[Dict[str, str]] = []

    def _note_attribution(self, provider: str, url: str) -> None:
        """Record which provider supplied media, for display in the UI."""
        if not any(entry["provider"] == provider for entry in self.sources_used):
            self.sources_used.append({"provider": provider, "url": url})

    def credits_line(self) -> str:
        """Return a short 'media from' line listing the providers actually used."""
        if not self.sources_used:
            return ""
        names = ", ".join(entry["provider"] for entry in self.sources_used)
        return f"Media sourced from {names}."

    # ------------------------------------------------------------------
    # Query building
    # ------------------------------------------------------------------
    @staticmethod
    def build_queries(topic: str, style_name: str, base_queries: Optional[List[str]] = None) -> List[str]:
        """Combine the topic with the style's visual keywords. Never generic filler."""
        style = get_style(style_name)
        topic_core = " ".join(re.findall(r"[A-Za-z0-9]+", topic or "")[:3]).strip()
        queries: List[str] = []
        for query in base_queries or []:
            if query and query.strip():
                queries.append(query.strip())
        for keyword in style["visual_keywords"]:
            if topic_core:
                queries.append(f"{topic_core} {keyword}")
        queries.extend(style["visual_keywords"])
        seen: set[str] = set()
        unique = []
        for query in queries:
            key = query.lower()
            if key not in seen:
                seen.add(key)
                unique.append(query)
        return unique

    # ------------------------------------------------------------------
    # Priority 1: Pexels (exact resolution match, closest to 15 s)
    # ------------------------------------------------------------------
    def search_videos_pexels(self, query_string: str, orientation_landscape: bool = True) -> Dict[str, Any]:
        """Original Pexels search request."""
        api_key = self.config.pexels_key()
        if not api_key:
            raise RuntimeError("Pexels API key is missing. Add it in the Settings tab.")
        url = "https://api.pexels.com/videos/search"
        headers = {"Authorization": api_key, **HEADERS}
        params = {
            "query": query_string,
            "orientation": "landscape" if orientation_landscape else "portrait",
            "per_page": 15,
        }
        response = self.session.get(url, headers=headers, params=params, timeout=REQUEST_TIMEOUT)
        json_data = response.json()
        if response.status_code != 200:
            raise RuntimeError(f"Pexels API error: {json_data.get('error', response.status_code)}")
        if "videos" not in json_data:
            raise RuntimeError("Pexels API returned an unexpected response (no 'videos' field).")
        return json_data

    def get_best_video_pexels(
        self, query_string: str, orientation_landscape: bool = True, used_vids: Optional[List[str]] = None
    ) -> Optional[str]:
        """Original best-clip selection: exact 16:9 / 9:16 files closest to 15 seconds."""
        used_vids = used_vids or []
        vids = self.search_videos_pexels(query_string, orientation_landscape)
        videos = vids["videos"]

        if orientation_landscape:
            filtered = [
                v for v in videos
                if v["width"] >= 1920 and v["height"] >= 1080 and v["width"] / v["height"] == 16 / 9
            ]
        else:
            filtered = [
                v for v in videos
                if v["width"] >= 1080 and v["height"] >= 1920 and v["height"] / v["width"] == 16 / 9
            ]

        # Pexels returns results in relevance order. Sorting purely by how close the
        # duration is to 15 seconds threw that away, so an unrelated 14 second clip
        # beat a well matched 30 second one. Rank on relevance first and use duration
        # only to separate clips of similar relevance. This costs no extra requests:
        # it reorders the results already in hand.
        ranked = []
        for position, video in enumerate(filtered):
            duration_penalty = min(abs(15 - int(video.get("duration", 15))), 30) / 30.0
            ranked.append((position + duration_penalty * RELEVANCE_DURATION_WEIGHT, video))
        sorted_videos = [video for _score, video in sorted(ranked, key=lambda item: item[0])]

        for video in sorted_videos:
            for video_file in video["video_files"]:
                if orientation_landscape:
                    if video_file["width"] == 1920 and video_file["height"] == 1080:
                        if video_file["link"].split(".hd")[0] not in used_vids:
                            return video_file["link"]
                else:
                    if video_file["width"] == 1080 and video_file["height"] == 1920:
                        if video_file["link"].split(".hd")[0] not in used_vids:
                            return video_file["link"]
        LOGGER.info("No Pexels links for query: %s", query_string)
        return None

    # ------------------------------------------------------------------
    # Priority 2: Pixabay
    # ------------------------------------------------------------------
    def get_best_video_pixabay(
        self, query: str, orientation_landscape: bool, used: List[str]
    ) -> Optional[str]:
        """Pixabay video search (free API key, Pixabay License, no attribution)."""
        key = self.config.pixabay_key()
        if not key:
            return None
        try:
            payload, status, from_cache = cached_get(
                self.session,
                "pixabay",
                "https://pixabay.com/api/videos/",
                params={"key": key, "q": query, "per_page": 20, "safesearch": "true"},
                timeout=REQUEST_TIMEOUT,
            )
            if payload is None:
                # Pixabay reports an invalid key with HTTP 400, not 401.
                if status == 400:
                    LOGGER.warning("Pixabay rejected the API key (HTTP 400). Check it in Settings.")
                elif status == 429:
                    LOGGER.warning("Pixabay rate limit reached (100 requests / 60 s).")
                return None
            if not from_cache:
                self._note_attribution("Pixabay", "https://pixabay.com/")
            hits = payload.get("hits", [])
        except requests.RequestException as exc:
            LOGGER.info("Pixabay request failed: %s", exc)
            return None

        # Same relevance-first ranking as Pexels: the API already returns hits in
        # relevance order, so position is the primary key and duration only breaks
        # ties between similarly relevant clips.
        candidates: List[Tuple[float, str]] = []
        for position, hit in enumerate(hits):
            # Highest quality first so the clip does not need upscaling.
            for quality in ("large", "medium", "small"):
                item = hit.get("videos", {}).get(quality)
                if not item or not item.get("url"):
                    continue
                width, height = item.get("width", 0), item.get("height", 0)
                if orientation_landscape and width < height:
                    continue
                if not orientation_landscape and height < width:
                    continue
                if item["url"] in used:
                    continue
                duration_penalty = min(abs(15 - int(hit.get("duration", 15))), 30) / 30.0
                candidates.append(
                    (position + duration_penalty * RELEVANCE_DURATION_WEIGHT, item["url"])
                )
                break
        candidates.sort(key=lambda item: item[0])
        return candidates[0][1] if candidates else None

    # ------------------------------------------------------------------
    # Priorities 3-8: lightweight scraping of zero-attribution stock sites
    # ------------------------------------------------------------------
    @staticmethod
    def _rank_by_resolution(links: List[str]) -> List[str]:
        """Order scraped links so the highest resolution rendition comes first."""
        def score(link: str) -> int:
            match = re.search(r"(\d{3,4})p", link)
            if match:
                return -int(match.group(1))
            match = re.search(r"-(\d{3,4})\.mp4", link)
            if match:
                return -int(match.group(1))
            if "1080" in link or "large" in link:
                return -1080
            if "720" in link or "medium" in link:
                return -720
            return 0

        return sorted(links, key=score)

    @staticmethod
    def _looks_relevant(link: str, query: str) -> bool:
        """True when a scraped asset name shares a meaningful word with the query.

        Some free sites (Coverr in particular) render results with JavaScript and serve
        the same generic clips for every search term, including nonsense ones. Matching
        the query words against the asset slug keeps the footage tied to the narration.
        """
        slug = re.sub(r"[^a-z0-9]+", " ", link.lower())
        words = [w for w in re.findall(r"[a-z]{4,}", (query or "").lower())]
        if not words:
            return True
        return any(word in slug for word in words)

    def _fetch_with_rotation(self, url: str) -> Optional[str]:
        """GET a page, trying alternative User-Agents when the host refuses one."""
        from utility.core.user_agents import (
            RETRYABLE_BLOCK_CODES,
            agents_for,
            headers_for,
            remember_success,
            remember_total_failure,
        )

        last_status = None
        candidates = agents_for(url)
        for index, agent in enumerate(candidates):
            try:
                response = self.session.get(
                    url, headers=headers_for(agent), timeout=REQUEST_TIMEOUT
                )
            except requests.RequestException as exc:
                LOGGER.info("Request to %s failed: %s", url.split("/")[2], str(exc)[:80])
                return None
            if response.ok:
                if index > 0:
                    LOGGER.info(
                        "%s accepted User-Agent #%d.", url.split("/")[2], index + 1
                    )
                remember_success(url, agent)
                return response.text
            last_status = response.status_code
            if response.status_code not in RETRYABLE_BLOCK_CODES:
                return None
        remember_total_failure(url)
        LOGGER.info(
            "%s refused all %d User-Agents (last status %s).",
            url.split("/")[2], len(candidates), last_status,
        )
        return None

    def _scrape_video_links(
        self,
        url: str,
        patterns: List[str],
        used: List[str],
        query: str = "",
        require_relevance: bool = False,
    ) -> Optional[str]:
        """Fetch a page and return the best matching, highest-resolution video link."""
        html = self._fetch_with_rotation(url)
        if not html:
            return None
        for pattern in patterns:
            found: List[str] = []
            for match in re.findall(pattern, html):
                link = match if isinstance(match, str) else match[0]
                link = link.replace("&amp;", "&")
                if link.startswith("//"):
                    link = "https:" + link
                if link.startswith("http") and link not in used and is_allowed_url(link):
                    found.append(link)
            if not found:
                continue
            ranked = self._rank_by_resolution(found)
            if require_relevance:
                relevant = [l for l in ranked if self._looks_relevant(l, query)]
                if not relevant:
                    LOGGER.info(
                        "%s returned results unrelated to '%s'; skipping this source.",
                        url.split("/")[2] if "//" in url else url, query,
                    )
                    return None
                return relevant[0]
            return ranked[0]
        return None

    def get_video_mixkit(self, query: str, used: List[str]) -> Optional[str]:
        """Mixkit free stock video (Mixkit License, no attribution)."""
        slug = quote_plus(query.replace(" ", "-"))
        return self._scrape_video_links(
            f"https://mixkit.co/free-stock-video/{slug}/",
            [r'https://assets\.mixkit\.co/videos/[^"\']+\.mp4'],
            used,
        )

    def get_video_coverr(self, query: str, used: List[str]) -> Optional[str]:
        """Coverr, restricted to Coverr's own CDN so no third-party stock leaks in."""
        return self._scrape_video_links(
            f"https://coverr.co/s?q={quote_plus(query)}",
            [
                r'https://cdn\.coverr\.co/videos/[^"\'?\s]+',
                r'https://storage\.coverr\.co/videos/[^"\'?\s]+',
            ],
            used,
            query=query,
            require_relevance=True,
        )

    def get_video_dareful(self, query: str, used: List[str]) -> Optional[str]:
        """Dareful CC0 footage, restricted to Dareful-hosted files."""
        return self._scrape_video_links(
            f"http://dareful.com/?s={quote_plus(query)}",
            [
                r'https?://[a-z0-9.-]*dareful\.com/[^"\'?\s]+\.mp4',
                r'https?://[a-z0-9.-]*amazonaws\.com/dareful[^"\'?\s]+\.mp4',
            ],
            used,
            query=query,
            require_relevance=True,
        )

    def get_video_lifeofvids(self, query: str, used: List[str]) -> Optional[str]:
        """Life of Vids public domain clips."""
        return self._scrape_video_links(
            f"https://www.lifeofvids.com/?s={quote_plus(query)}",
            [r'https?://[a-z0-9.-]*lifeofvids\.com/[^"\'?\s]+\.mp4'],
            used,
            query=query,
            require_relevance=True,
        )

    def get_video_splitshire(self, query: str, used: List[str]) -> Optional[str]:
        """SplitShire CC0 clips."""
        return self._scrape_video_links(
            f"https://www.splitshire.com/?s={quote_plus(query)}",
            [r'https?://[a-z0-9.-]*splitshire\.com/[^"\'?\s]+\.mp4'],
            used,
            query=query,
            require_relevance=True,
        )

    def get_video_videvo(self, query: str, used: List[str]) -> Optional[str]:
        """Videvo free-tier clips only."""
        slug = quote_plus(query.replace(" ", "-"))
        return self._scrape_video_links(
            f"https://www.videvo.net/search/{slug}/?filter=free",
            [r'https://[^"\']*videvo[^"\']*\.mp4'],
            used,
            query=query,
            require_relevance=True,
        )

    def get_video_archive(self, query: str, used: List[str]) -> Optional[str]:
        """Internet Archive / Prelinger public domain footage."""
        try:
            response = self.session.get(
                "https://archive.org/advancedsearch.php",
                params={
                    "q": f'{query} AND mediatype:(movies) AND licenseurl:(*publicdomain*)',
                    "fl[]": "identifier",
                    "rows": 5,
                    "output": "json",
                },
                timeout=REQUEST_TIMEOUT,
            )
            docs = response.json().get("response", {}).get("docs", [])
        except (requests.RequestException, ValueError) as exc:
            LOGGER.info("Archive.org search failed: %s", exc)
            return None
        for doc in docs:
            identifier = doc.get("identifier")
            if not identifier:
                continue
            try:
                meta = self.session.get(
                    f"https://archive.org/metadata/{identifier}", timeout=REQUEST_TIMEOUT
                ).json()
            except (requests.RequestException, ValueError):
                continue
            # Prefer small derivative files: full documentaries are hundreds of MB and
            # are useless as a 3 second background clip.
            candidates = []
            for file_entry in meta.get("files", []):
                name = file_entry.get("name", "")
                if not name.lower().endswith((".mp4", ".m4v")):
                    continue
                try:
                    size = int(file_entry.get("size", 0) or 0)
                except (TypeError, ValueError):
                    size = 0
                # Size no longer disqualifies a clip. Unknown-size entries are still
                # skipped because the sort below needs a number to rank on.
                if not size:
                    continue
                link = f"https://archive.org/download/{identifier}/{quote_plus(name)}"
                if link not in used:
                    candidates.append((size or 10**9, link))
            if candidates:
                candidates.sort(key=lambda item: item[0])
                return candidates[0][1]
        return None

    # ------------------------------------------------------------------
    # Fallback chain
    # ------------------------------------------------------------------
    def find_video(
        self, query: str, orientation_landscape: bool = True, used: Optional[List[str]] = None
    ) -> Optional[str]:
        """Try every video source in priority order for a single query."""
        used = used if used is not None else []
        providers = [
            ("Pexels", lambda: self.get_best_video_pexels(query, orientation_landscape, used)),
            ("Pixabay", lambda: self.get_best_video_pixabay(query, orientation_landscape, used)),
            ("Mixkit", lambda: self.get_video_mixkit(query, used)),
            ("Coverr", lambda: self.get_video_coverr(query, used)),
            ("Dareful", lambda: self.get_video_dareful(query, used)),
            ("Life of Vids", lambda: self.get_video_lifeofvids(query, used)),
            ("SplitShire", lambda: self.get_video_splitshire(query, used)),
            ("Videvo", lambda: self.get_video_videvo(query, used)),
            ("Internet Archive", lambda: self.get_video_archive(query, used)),
        ]
        for name, provider in providers:
            try:
                url = provider()
            except Exception as exc:  # noqa: BLE001 - never break the chain
                LOGGER.info("%s failed for '%s': %s", name, query, exc)
                continue
            if url and not is_allowed_url(url):
                LOGGER.info("Rejected a paid-stock link returned by %s.", name)
                continue
            if url:
                LOGGER.info("Found clip for '%s' on %s.", query, name)
                self._note_attribution(name, url.split("/")[2] if "//" in url else name)
                return url
        return None

    def generate_video_url(
        self,
        timed_video_searches,
        orientation_landscape: bool = True,
        style_name: str = "Cinematic",
        topic: str = "",
    ):
        """Map [[t1,t2], [keywords]] segments to clip URLs, one clip per segment.

        For each timed segment, that segment's own keywords are tried in order, the
        first clip found is taken, and used links are remembered so the same footage
        is never repeated.

        The keywords come from the narration of that specific segment, which is what
        keeps every shot matched to the sentence being spoken. Style keywords are only
        used as a last resort, and are combined with the segment's own words so the
        shot still relates to what is being said.
        """
        timed_video_urls = []
        used_links: List[str] = []
        topic_core = " ".join(re.findall(r"[A-Za-z0-9]+", topic or "")[:2]).strip()
        style = get_style(style_name)

        for (t1, t2), search_terms in timed_video_searches:
            url = None
            segment_terms = [str(term).strip() for term in search_terms if str(term).strip()]

            # 1) the segment's own visual keywords, exactly as the LLM produced them
            for query in segment_terms:
                url = self.find_video(query, orientation_landscape, used_links)
                if url:
                    break

            # 2) still nothing: widen the segment's own words with the video topic
            if not url and segment_terms and topic_core:
                for query in segment_terms[:2]:
                    url = self.find_video(
                        f"{query} {topic_core}", orientation_landscape, used_links
                    )
                    if url:
                        break

            # 3) last resort: the segment's leading word plus a style visual, so the
            #    shot still carries the meaning of the sentence instead of being generic
            if not url:
                lead = segment_terms[0].split()[0] if segment_terms else topic_core
                for keyword in style["visual_keywords"][:3]:
                    query = f"{lead} {keyword}".strip() if lead else keyword
                    url = self.find_video(query, orientation_landscape, used_links)
                    if url:
                        break

            if url:
                used_links.append(url.split(".hd")[0])
            else:
                LOGGER.info("No clip found for segment %.2f-%.2f (%s).", t1, t2, segment_terms[:2])
            timed_video_urls.append([[t1, t2], url])

        found = sum(1 for _i, u in timed_video_urls if u)
        LOGGER.info(
            "Sourced %d clips for %d timed segments.", found, len(timed_video_urls)
        )
        return timed_video_urls

    # ------------------------------------------------------------------
    # Public domain images (Ken Burns material)
    # ------------------------------------------------------------------
    def find_image(self, query: str) -> Optional[str]:
        """Find a public domain / zero-attribution still image."""
        finders = [
            ("Pexels", self._image_pexels),
            ("Pixabay", self._image_pixabay),
            ("NASA", self._image_nasa),
            ("Smithsonian", self._image_smithsonian),
            ("Met Museum", self._image_met),
            ("Rijksmuseum", self._image_rijksmuseum),
        ]
        for name, finder in finders:
            try:
                url = finder(query)
            except Exception as exc:  # noqa: BLE001
                LOGGER.info("Image source %s failed: %s", name, exc)
                continue
            if url:
                return url
        return None

    def _image_pexels(self, query: str) -> Optional[str]:
        """Pexels photo search."""
        key = self.config.pexels_key()
        if not key:
            return None
        response = self.session.get(
            "https://api.pexels.com/v1/search",
            headers={"Authorization": key},
            params={"query": query, "per_page": 5},
            timeout=REQUEST_TIMEOUT,
        )
        photos = response.json().get("photos", []) if response.ok else []
        return photos[0]["src"]["large2x"] if photos else None

    def _image_pixabay(self, query: str) -> Optional[str]:
        """Pixabay photo search."""
        key = self.config.pixabay_key()
        if not key:
            return None
        payload, _status, from_cache = cached_get(
            self.session,
            "pixabay",
            "https://pixabay.com/api/",
            params={"key": key, "q": query, "per_page": 5, "image_type": "photo"},
            timeout=REQUEST_TIMEOUT,
        )
        hits = (payload or {}).get("hits", [])
        if hits and not from_cache:
            self._note_attribution("Pixabay", "https://pixabay.com/")
        # fullHDURL / imageURL need full API access; largeImageURL (1280 px) is always present.
        if not hits:
            return None
        first = hits[0]
        return first.get("fullHDURL") or first.get("largeImageURL") or first.get("webformatURL")

    def _image_nasa(self, query: str) -> Optional[str]:
        """NASA public domain imagery (no key required)."""
        response = self.session.get(
            "https://images-api.nasa.gov/search",
            params={"q": query, "media_type": "image"},
            timeout=REQUEST_TIMEOUT,
        )
        items = response.json().get("collection", {}).get("items", []) if response.ok else []
        for item in items:
            links = item.get("links", [])
            if links and links[0].get("href"):
                return links[0]["href"]
        return None

    def _image_smithsonian(self, query: str) -> Optional[str]:
        """Smithsonian Open Access CC0 imagery (public data.gov demo key)."""
        payload, _status, _cached = cached_get(
            self.session,
            "smithsonian",
            "https://api.si.edu/openaccess/api/v1.0/search",
            params={
                "q": f"{query} AND online_media_type:Images",
                "rows": 5,
                "api_key": SMITHSONIAN_API_KEY,
            },
            timeout=REQUEST_TIMEOUT,
        )
        if not payload:
            return None
        rows = payload.get("response", {}).get("rows", [])
        for row in rows:
            media = (
                row.get("content", {})
                .get("descriptiveNonRepeating", {})
                .get("online_media", {})
                .get("media", [])
            )
            for entry in media:
                if entry.get("content"):
                    return entry["content"]
        return None

    def _image_met(self, query: str) -> Optional[str]:
        """Met Museum public domain artworks."""
        search = self.session.get(
            "https://collectionapi.metmuseum.org/public/collection/v1/search",
            params={"q": query, "hasImages": "true", "isPublicDomain": "true"},
            timeout=REQUEST_TIMEOUT,
        )
        ids = search.json().get("objectIDs") or [] if search.ok else []
        for object_id in ids[:5]:
            detail = self.session.get(
                f"https://collectionapi.metmuseum.org/public/collection/v1/objects/{object_id}",
                timeout=REQUEST_TIMEOUT,
            )
            if detail.ok and detail.json().get("primaryImage"):
                return detail.json()["primaryImage"]
        return None

    def _image_rijksmuseum(self, query: str) -> Optional[str]:
        """Rijksmuseum public domain artworks."""
        response = self.session.get(
            "https://www.rijksmuseum.nl/api/en/collection",
            params={"q": query, "imgonly": "true", "ps": 5, "key": "0fiuZFh4"},
            timeout=REQUEST_TIMEOUT,
        )
        if not response.ok:
            return None
        for art in response.json().get("artObjects", []):
            image = (art.get("webImage") or {}).get("url")
            if image:
                return image
        return None

    # ------------------------------------------------------------------
    # Music and SFX
    # ------------------------------------------------------------------
    def find_music(self, style_name: str, topic: str = "") -> Optional[str]:
        """Find a background music track matching the style's music moods.

        Compound moods such as "ambient aquatic" are also tried word by word, because
        the free catalogues are indexed by single mood keywords.
        """
        moods = music_moods_for_style(style_name)
        queries: List[str] = []
        for mood in moods:
            queries.append(mood)
            if " " in mood:
                queries.extend(mood.split())
        queries.append("ambient")  # always-available neutral bed, still zero attribution

        seen: set[str] = set()
        for mood in queries:
            mood = mood.strip().lower()
            if not mood or mood in seen:
                continue
            seen.add(mood)
            for finder in (self._audio_pixabay, self._audio_mixkit_music, self._audio_freepd):
                try:
                    url = finder(f"{mood} music")
                except Exception as exc:  # noqa: BLE001
                    LOGGER.info("Music source failed: %s", exc)
                    continue
                if url and is_allowed_url(url):
                    LOGGER.info("Selected background music for mood '%s'.", mood)
                    return url
        return None

    def find_sfx(self, keyword: str) -> Optional[str]:
        """Find a single sound effect for a keyword across the approved SFX sources."""
        primary = keyword.strip()
        head = primary.split()[0] if primary.split() else primary
        for query in (primary, head):
            for finder in (self._sfx_pixabay, self._audio_mixkit_sfx, self._audio_freepd):
                try:
                    url = finder(query)
                except Exception as exc:  # noqa: BLE001
                    LOGGER.info("SFX source failed: %s", exc)
                    continue
                if url and is_allowed_url(url):
                    return url
        return None

    def _sfx_pixabay(self, query: str) -> Optional[str]:
        """Pixabay sound effects section (Pixabay License, no attribution)."""
        slug = quote_plus(query.replace(" ", "-"))
        for page in (
            f"https://pixabay.com/sound-effects/search/{slug}/",
            f"https://pixabay.com/sound-effects/search/{quote_plus(query)}/",
        ):
            try:
                response = self.session.get(page, timeout=REQUEST_TIMEOUT)
                if not response.ok:
                    continue
                matches = re.findall(
                    r'https://cdn\.pixabay\.com/(?:download/)?audio/[^"\']+?\.mp3', response.text
                )
                if matches:
                    return matches[0].replace("&amp;", "&")
            except requests.RequestException:
                continue
        return None

    def _audio_pixabay(self, query: str) -> Optional[str]:
        """Pixabay audio pages are scraped for direct CDN mp3 links (Pixabay License)."""
        page = f"https://pixabay.com/music/search/{quote_plus(query)}/"
        html = self._fetch_with_rotation(page)
        if not html:
            return None
        matches = re.findall(r'https://cdn\.pixabay\.com/(?:download/)?audio/[^"\']+?\.mp3', html)
        return matches[0].replace("&amp;", "&") if matches else None

    def _audio_mixkit_music(self, query: str) -> Optional[str]:
        """Mixkit free stock music (Mixkit License)."""
        slug = quote_plus(query.replace(" ", "-"))
        try:
            response = self.session.get(
                f"https://mixkit.co/free-stock-music/{slug}/", timeout=REQUEST_TIMEOUT
            )
            matches = re.findall(r'https://assets\.mixkit\.co/(?:music|active_storage)/[^"\']+\.mp3', response.text)
            return matches[0] if matches else None
        except requests.RequestException:
            return None

    def _audio_mixkit_sfx(self, query: str) -> Optional[str]:
        """Mixkit free sound effects (Mixkit License, no attribution)."""
        slug = quote_plus(query.replace(" ", "-"))
        pages = [
            f"https://mixkit.co/free-sound-effects/{slug}/",
            f"https://mixkit.co/free-sound-effects/search/{quote_plus(query)}/",
        ]
        for page in pages:
            try:
                response = self.session.get(page, timeout=REQUEST_TIMEOUT)
                if not response.ok:
                    continue
                matches = re.findall(
                    r'https://assets\.mixkit\.co/[^"\']+?\.(?:mp3|wav)', response.text
                )
                if matches:
                    return matches[0]
            except requests.RequestException:
                continue
        return None

    def _audio_freepd(self, query: str) -> Optional[str]:
        """FreePD hosts CC0 music; pick a track whose name matches the mood."""
        # FreePD renders its catalogue with JavaScript, so only a few static pages
        # still expose direct MP3 links. It is kept as a last-resort CC0 fallback.
        pages = [
            "https://freepd.com/",
            "https://freepd.com/music/",
            "https://freepd.com/epic.php",
            "https://freepd.com/scoring.php",
        ]
        links: List[str] = []
        for page in pages:
            try:
                response = self.session.get(page, timeout=REQUEST_TIMEOUT)
                if not response.ok:
                    continue
                links.extend(re.findall(r'href="([^"]+\.mp3)"', response.text))
                links.extend(re.findall(r'src="([^"]+\.mp3)"', response.text))
            except requests.RequestException:
                continue
            if links:
                break
        if not links:
            return None
        words = [w for w in re.findall(r"[a-z]+", query.lower()) if len(w) > 3]
        for link in links:
            if any(word in link.lower() for word in words):
                return link if link.startswith("http") else "https://freepd.com/" + link.lstrip("/")
        first = links[0]
        return first if first.startswith("http") else "https://freepd.com/" + first.lstrip("/")

    # ------------------------------------------------------------------
    def download_to_temp(
        self, url: str, suffix: str = ".mp4", kind: Optional[str] = None
    ) -> Optional[str]:
        """Download a URL into a temp file, retrying through transient failures."""
        from utility.core.resilient_download import download_media

        if kind is None:
            kind = "audio" if suffix.lower() in (".mp3", ".wav", ".m4a", ".ogg") else "video"
        handle = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
        handle.close()
        path = download_media(url, handle.name, kind=kind)
        if not path:
            try:
                os.remove(handle.name)
            except OSError:
                pass
            return None
        return path
