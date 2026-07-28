"""Resilient downloading for slow, unstable or intermittent connections.

The problem this solves: a single network hiccup used to make a clip fail once and be
skipped, leaving a hole in the video.

The fix is *not* to retry forever. Testing shows two very different failure classes:

* **Transient** - timeout, connection reset, DNS blip, HTTP 429/500/502/503/504,
  or a truncated body. Retrying genuinely works, so we retry with exponential backoff
  and resume partial downloads with HTTP Range requests.
* **Permanent** - HTTP 404/410 (the file is gone) or 401/403 (bot challenge). These
  return the same error every single time. Retrying them forever would freeze the app,
  so we fail fast and let the caller pick a different clip instead.

A download is only accepted when the file is complete: the size is verified against
Content-Length and the container is checked, so a half-downloaded clip is never used.
"""

from __future__ import annotations

import logging
import os
import random
import shutil
import time
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

import requests

LOGGER = logging.getLogger("download")
if not LOGGER.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("[%(name)s] %(message)s"))
    LOGGER.addHandler(_h)
LOGGER.setLevel(logging.INFO)

# Retrying these is worthwhile: the server or the link is temporarily unhappy.
TRANSIENT_STATUS = {408, 409, 425, 429, 500, 502, 503, 504, 522, 524}
# Retrying these never helps: the resource is gone or the host refuses this client.
PERMANENT_STATUS = {400, 401, 402, 403, 404, 405, 410, 451}

# Purely informational: files above this are logged so a slow download is
# explained in the output, but nothing is rejected.
LARGE_FILE_NOTICE_BYTES = 80 * 1024 * 1024

CHUNK_SIZE = 256 * 1024
MIN_VIDEO_BYTES = 20 * 1024
MIN_AUDIO_BYTES = 8 * 1024


@dataclass
class DownloadResult:
    """Outcome of a download attempt."""

    path: Optional[str]
    ok: bool
    attempts: int
    bytes_downloaded: int
    reason: str = ""
    resumed: bool = False


class PermanentDownloadError(Exception):
    """The resource cannot be downloaded no matter how often we try."""


def _is_probably_complete(path: str, expected: Optional[int], minimum: int) -> bool:
    """Verify the file is fully downloaded, not truncated."""
    if not os.path.exists(path):
        return False
    size = os.path.getsize(path)
    if size < minimum:
        return False
    if expected and size < expected:
        return False
    return True


def _looks_like_media(path: str, kind: str) -> bool:
    """Cheap container sanity check so a truncated or HTML error page is rejected."""
    try:
        with open(path, "rb") as handle:
            head = handle.read(16)
    except OSError:
        return False
    if not head:
        return False
    # An HTML error page saved as .mp4 is a common failure mode.
    lowered = head.lower()
    if lowered.startswith(b"<!doctype") or lowered.startswith(b"<html"):
        return False
    if kind == "video":
        # ISO base media files carry an 'ftyp' box near the start; webm starts with EBML.
        return b"ftyp" in head or head.startswith(b"\x1a\x45\xdf\xa3")
    if kind == "audio":
        return (
            head.startswith(b"ID3")
            or head[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2", b"\xff\xe3")
            or head.startswith(b"RIFF")
            or head.startswith(b"OggS")
            or b"ftyp" in head
        )
    return True


def download_with_retries(
    url: str,
    destination: str,
    kind: str = "video",
    max_attempts: int = 6,
    base_timeout: int = 45,
    headers: Optional[Dict[str, str]] = None,
    on_status: Optional[Callable[[str], None]] = None,
    session: Optional[requests.Session] = None,
    max_bytes: Optional[int] = None,
) -> DownloadResult:
    """Download a URL, resuming and retrying through transient network problems.

    Raises PermanentDownloadError when the resource is genuinely unavailable, so the
    caller can immediately try a different clip instead of waiting.
    """
    from utility.core.user_agents import agents_for, headers_for, remember_success

    report = on_status or (lambda message: None)
    owns_session = session is None
    session = session or requests.Session()
    agents = agents_for(url)
    # Tie the partial file to this exact URL. Reusing a .part left over from a different
    # clip made the server answer HTTP 416 (Range Not Satisfiable).
    import hashlib

    partial = f"{destination}.{hashlib.sha1(url.encode()).hexdigest()[:10]}.part"
    for stale in (destination + ".part",):
        if os.path.exists(stale):
            try:
                os.remove(stale)
            except OSError:
                pass
    attempts = 0
    resumed = False
    last_reason = "unknown"
    stalled_at = -1
    stall_count = 0

    try:
        for attempt in range(1, max_attempts + 1):
            attempts = attempt
            agent = agents[(attempt - 1) % len(agents)]
            request_headers = headers_for(agent, headers)

            # Resume support: ask only for the bytes we are missing.
            existing = os.path.getsize(partial) if os.path.exists(partial) else 0
            if existing > 0:
                request_headers["Range"] = f"bytes={existing}-"
                resumed = True

            # Give slow connections progressively more time.
            timeout = base_timeout + (attempt - 1) * 20

            try:
                response = session.get(
                    url, headers=request_headers, timeout=(15, timeout), stream=True
                )
            except (requests.Timeout, requests.ConnectionError) as exc:
                last_reason = f"{type(exc).__name__}"
                delay = min(2 ** (attempt - 1) + random.uniform(0, 1), 20)
                LOGGER.info(
                    "Attempt %d/%d for %s failed (%s). Retrying in %.1fs...",
                    attempt, max_attempts, url.split("/")[-1][:40], last_reason, delay,
                )
                report(f"Connection problem, retrying ({attempt}/{max_attempts})...")
                time.sleep(delay)
                continue

            if response.status_code == 416:
                # Our partial file is longer than the remote resource: start over.
                LOGGER.info("Server rejected the resume range; restarting the download.")
                try:
                    os.remove(partial)
                except OSError:
                    pass
                continue

            if response.status_code in PERMANENT_STATUS:
                # 403 can sometimes be an agent problem, so let the rotation try a few.
                if response.status_code == 403 and attempt < min(3, max_attempts):
                    last_reason = "HTTP 403"
                    continue
                raise PermanentDownloadError(
                    f"HTTP {response.status_code} for {url[:80]}"
                )

            if response.status_code in TRANSIENT_STATUS:
                last_reason = f"HTTP {response.status_code}"
                delay = min(2 ** (attempt - 1) + random.uniform(0, 1), 20)
                LOGGER.info(
                    "Attempt %d/%d: %s returned %s. Retrying in %.1fs...",
                    attempt, max_attempts, url.split("/")[2], response.status_code, delay,
                )
                time.sleep(delay)
                continue

            if response.status_code not in (200, 206):
                last_reason = f"HTTP {response.status_code}"
                continue

            # A 200 to a Range request means the server ignored it: start over.
            mode = "ab"
            if existing > 0 and response.status_code == 200:
                existing = 0
                resumed = False
                mode = "wb"

            expected = None
            length = response.headers.get("Content-Length")
            if length and length.isdigit():
                expected = int(length) + existing

            # No size limit: a large source clip is downloaded in full rather than
            # rejected, because rejecting it costs the segment its best match.
            if expected and expected > LARGE_FILE_NOTICE_BYTES:
                LOGGER.info(
                    "%s is %.0f MB; downloading in full.",
                    url.split("/")[-1][:40], expected / (1024 * 1024),
                )

            oversized = False
            try:
                written = existing
                with open(partial, mode) as handle:
                    for chunk in response.iter_content(chunk_size=CHUNK_SIZE):
                        if not chunk:
                            continue
                        handle.write(chunk)
                        written += len(chunk)
                        # Only an explicit max_bytes stops a download now. Left as an
                        # opt-in guard for callers that genuinely need a ceiling.
                        if max_bytes is not None and written > max_bytes:
                            oversized = True
                            break
            except (requests.Timeout, requests.ConnectionError, OSError) as exc:
                last_reason = f"stream interrupted ({type(exc).__name__})"
                got = os.path.getsize(partial) if os.path.exists(partial) else 0
                LOGGER.info(
                    "Download interrupted at %.1f MB (%s). Will resume.",
                    got / (1024 * 1024), last_reason,
                )
                report("Download interrupted, resuming...")
                if got == stalled_at:
                    stall_count += 1
                    if stall_count >= 2:
                        LOGGER.info(
                            "The source stopped sending data at %.1f MB twice; "
                            "using a different clip instead.", got / (1024 * 1024),
                        )
                        break
                else:
                    stalled_at = got
                    stall_count = 0
                time.sleep(min(2 ** (attempt - 1), 15))
                continue

            if oversized:
                LOGGER.info(
                    "Aborted %s: exceeded the caller's %.0f MB limit.",
                    url.split("/")[-1][:40], (max_bytes or 0) / (1024 * 1024),
                )
                try:
                    os.remove(partial)
                except OSError:
                    pass
                raise PermanentDownloadError("File exceeds the size limit for a clip")

            minimum = MIN_VIDEO_BYTES if kind == "video" else MIN_AUDIO_BYTES
            if not _is_probably_complete(partial, expected, minimum):
                got = os.path.getsize(partial) if os.path.exists(partial) else 0
                last_reason = f"incomplete ({got} of {expected or '?'} bytes)"
                LOGGER.info("File incomplete (%s). Resuming...", last_reason)
                time.sleep(1.0)
                continue

            if not _looks_like_media(partial, kind):
                last_reason = "not a valid media file"
                try:
                    os.remove(partial)
                except OSError:
                    pass
                raise PermanentDownloadError(f"{url[:70]} did not return real {kind} data")

            shutil.move(partial, destination)
            size = os.path.getsize(destination)
            remember_success(url, agent)
            if attempt > 1:
                LOGGER.info(
                    "Downloaded %s on attempt %d (%.1f KB%s).",
                    os.path.basename(destination), attempt, size / 1024,
                    ", resumed" if resumed else "",
                )
            return DownloadResult(destination, True, attempt, size, resumed=resumed)

        return DownloadResult(None, False, attempts, 0, reason=last_reason, resumed=resumed)
    finally:
        if owns_session:
            session.close()
        # A partial file is only useful while retrying this same URL.
        if os.path.exists(partial) and os.path.exists(destination):
            try:
                os.remove(partial)
            except OSError:
                pass


def download_media(
    url: str,
    destination: str,
    kind: str = "video",
    max_attempts: int = 6,
    on_status: Optional[Callable[[str], None]] = None,
    session: Optional[requests.Session] = None,
    max_bytes: Optional[int] = None,
) -> Optional[str]:
    """Convenience wrapper returning the path on success and None on failure.

    max_bytes stays None unless a caller asks for a ceiling, so clip size never
    decides whether a segment gets its footage.
    """
    try:
        result = download_with_retries(
            url, destination, kind=kind, max_attempts=max_attempts,
            on_status=on_status, session=session, max_bytes=max_bytes,
        )
    except PermanentDownloadError as exc:
        LOGGER.info("Permanent failure, will use a different source: %s", exc)
        return None
    if result.ok:
        return result.path
    LOGGER.info(
        "Gave up on %s after %d attempts (%s).",
        url.split("/")[-1][:40], result.attempts, result.reason,
    )
    return None


def download_first_working(
    urls: List[str],
    destination: str,
    kind: str = "video",
    attempts_per_url: int = 4,
    on_status: Optional[Callable[[str], None]] = None,
) -> Optional[str]:
    """Try several candidate URLs, retrying each, until one downloads completely."""
    for index, url in enumerate(urls):
        if not url:
            continue
        if index > 0 and on_status:
            on_status(f"Trying alternative source {index + 1} of {len(urls)}...")
        path = download_media(
            url, destination, kind=kind, max_attempts=attempts_per_url, on_status=on_status
        )
        if path:
            return path
    return None


def internet_available(timeout: int = 6) -> bool:
    """Quick check for a usable internet connection."""
    for url in ("https://www.google.com/generate_204", "https://1.1.1.1"):
        try:
            response = requests.head(url, timeout=timeout)
            if response.status_code < 500:
                return True
        except requests.RequestException:
            continue
    return False


def wait_for_internet(
    max_wait_seconds: int = 180, on_status: Optional[Callable[[str], None]] = None
) -> bool:
    """Pause while the connection is down, up to a bounded time.

    This is the one place where waiting is correct: if the whole connection dropped,
    every source will fail, so it is better to wait for the link to return than to burn
    through every fallback. The wait is bounded so the app can never hang forever.
    """
    report = on_status or (lambda message: None)
    waited = 0
    delay = 5
    while waited < max_wait_seconds:
        if internet_available():
            if waited:
                LOGGER.info("Connection restored after %d seconds.", waited)
                report("Connection restored, continuing...")
            return True
        LOGGER.info("No internet connection. Waiting %ds (%ds/%ds)...", delay, waited, max_wait_seconds)
        report(f"Waiting for the internet connection ({waited}s of {max_wait_seconds}s)...")
        time.sleep(delay)
        waited += delay
        delay = min(delay * 2, 30)
    return internet_available()
