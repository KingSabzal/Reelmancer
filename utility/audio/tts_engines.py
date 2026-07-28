"""Multi-engine text-to-speech with automatic fallback.

EdgeTTS is a single point of failure: Microsoft rejects requests with HTTP 403 when
the client version string is outdated, when the system clock is skewed, or when the
endpoint rate-limits an IP. This module wraps several free, keyless, online engines
and walks through them until one produces audio.

Fallback order:
    1. EdgeTTS with the requested voice (best quality, neural voices)
    2. EdgeTTS with neutral rate/pitch (some voices reject extreme prosody)
    3. EdgeTTS with alternative voices of the same gender and accent
    4. EdgeTTS with the library default voice
    5. Google Translate TTS (keyless, chunked and concatenated)

Every engine is free, online and lightweight. No paid service and no heavy local
model is used anywhere.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import tempfile
import urllib.parse
from dataclasses import dataclass
from typing import Callable, List, Optional, Tuple

import requests

LOGGER = logging.getLogger("tts_engines")
if not LOGGER.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("[%(name)s] %(message)s"))
    LOGGER.addHandler(_h)
LOGGER.setLevel(logging.INFO)

MIN_AUDIO_BYTES = 1024
HTTP_TIMEOUT = 30
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# Google Translate TTS rejects anything longer than roughly 200 characters.
GTTS_CHUNK_LIMIT = 190

# Accent -> Google Translate domain, so the fallback keeps a similar voice character.
GTTS_TLD_BY_ACCENT = {
    "American": "com",
    "British": "co.uk",
    "Irish": "co.uk",
    "European": "co.uk",
    "Australian": "com.au",
    "New Zealander": "com.au",
    "Canadian": "ca",
    "Indian": "co.in",
    "South Asia": "co.in",
    "Singaporean": "co.in",
    "Hong Kong": "co.in",
    "Filipino": "com",
    "South African": "co.uk",
    "Kenyan": "co.uk",
    "Nigerian": "co.uk",
    "Tanzanian": "co.uk",
    "Asian": "com",
    "Latin": "com",
}


@dataclass
class TTSResult:
    """Describes which engine and voice actually produced the audio."""

    path: str
    engine: str
    voice: str
    degraded: bool = False
    note: str = ""


class TTSFailure(Exception):
    """Raised when every engine failed to synthesize the text."""


def _valid_audio(path: str) -> bool:
    """True when the file exists and is large enough to be real audio."""
    return os.path.exists(path) and os.path.getsize(path) > MIN_AUDIO_BYTES


def _classify(error: Exception) -> str:
    """Turn a raw exception into a short, human-readable reason."""
    text = str(error)
    if "403" in text:
        return (
            "HTTP 403 from Microsoft. The Edge client version or the system clock was "
            "rejected."
        )
    if "429" in text:
        return "HTTP 429: the Edge endpoint is rate limiting this IP."
    if "NoAudioReceived" in type(error).__name__ or "No audio" in text:
        return "The service accepted the request but returned no audio."
    if "SkewAdjustment" in type(error).__name__:
        return "The system clock is too far off for the service token."
    return f"{type(error).__name__}: {text[:120]}"


# ----------------------------------------------------------------------
# Engine 1: EdgeTTS
# ----------------------------------------------------------------------
def refresh_edge_tts_drm() -> None:
    """Reset the EdgeTTS clock-skew correction so a stale token is regenerated.

    A wrong system clock is one of the two common causes of the 403 error. Newer
    edge-tts releases correct the skew from the server response, but the cached value
    can go stale inside a long-running session.
    """
    try:
        from edge_tts import drm

        drm.DRM.clock_skew_seconds = 0.0
    except Exception as exc:  # noqa: BLE001 - best effort only
        LOGGER.debug("Could not reset the EdgeTTS DRM state: %s", exc)


async def _edge_speak(text: str, output_path: str, voice: str, rate: str, pitch: str) -> None:
    """Single EdgeTTS synthesis attempt."""
    import edge_tts

    communicate = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch, volume="+0%")
    await communicate.save(output_path)


def _run_async(coroutine) -> None:
    """Run a coroutine whether or not an event loop is already running.

    asyncio.run() refuses to start when a loop is already active, which is the
    case inside Jupyter, Colab and any other notebook kernel. EdgeTTS then failed
    with "asyncio.run() cannot be called from a running event loop" and every
    voice fell through to the low quality fallback. nest_asyncio makes nesting
    legal; if it is unavailable the work is handed to a separate thread, which
    has no loop of its own.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(coroutine)      # the normal case: no loop, run directly
        return

    try:
        import nest_asyncio

        nest_asyncio.apply()
        asyncio.run(coroutine)
        return
    except ImportError:
        pass

    # Last resort: a thread with a clean event loop of its own.
    import concurrent.futures

    def _worker():
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(coroutine)
        finally:
            loop.close()

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(_worker).result()


def edge_tts_synthesize(
    text: str, output_path: str, voice: str, rate: str = "+0%", pitch: str = "+0Hz"
) -> bool:
    """Synthesize with EdgeTTS. Returns True on success."""
    refresh_edge_tts_drm()
    _run_async(_edge_speak(text, output_path, voice, rate, pitch))
    return _valid_audio(output_path)


def edge_tts_version_note() -> str:
    """Return an upgrade hint when the installed edge-tts is old enough to get 403s."""
    try:
        import importlib.metadata as metadata

        version = metadata.version("edge-tts")
    except Exception:  # noqa: BLE001
        return ""
    try:
        major, minor = (int(part) for part in version.split(".")[:2])
    except (ValueError, TypeError):
        return ""
    # Releases before 7.x send an outdated Sec-MS-GEC-Version that Microsoft now rejects.
    if major < 7:
        return (
            f"edge-tts {version} is outdated and sends an old client version string, "
            "which Microsoft answers with HTTP 403. Run: pip install -U edge-tts"
        )
    return ""


# ----------------------------------------------------------------------
# Engine 2: Google Translate TTS (keyless)
# ----------------------------------------------------------------------
def _split_for_gtts(text: str, limit: int = GTTS_CHUNK_LIMIT) -> List[str]:
    """Split text into chunks under the Google Translate length limit."""
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    chunks: List[str] = []
    current = ""
    for sentence in sentences:
        while len(sentence) > limit:
            cut = sentence.rfind(" ", 0, limit)
            cut = cut if cut > 0 else limit
            if current:
                chunks.append(current.strip())
                current = ""
            chunks.append(sentence[:cut].strip())
            sentence = sentence[cut:].strip()
        if len(current) + len(sentence) + 1 <= limit:
            current = f"{current} {sentence}".strip()
        else:
            if current:
                chunks.append(current.strip())
            current = sentence
    if current.strip():
        chunks.append(current.strip())
    return [chunk for chunk in chunks if chunk]


def google_tts_synthesize(text: str, output_path: str, accent: str = "American") -> bool:
    """Synthesize with the keyless Google Translate endpoint, chunked and joined."""
    tld = GTTS_TLD_BY_ACCENT.get(accent, "com")
    chunks = _split_for_gtts(text)
    if not chunks:
        return False

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Referer": f"https://translate.google.{tld}/"})
    workdir = tempfile.mkdtemp(prefix="gtts_")
    parts: List[str] = []
    try:
        for index, chunk in enumerate(chunks):
            url = f"https://translate.google.{tld}/translate_tts?" + urllib.parse.urlencode(
                {
                    "ie": "UTF-8",
                    "q": chunk,
                    "tl": "en",
                    "client": "tw-ob",
                    "total": len(chunks),
                    "idx": index,
                    "textlen": len(chunk),
                }
            )
            response = session.get(url, timeout=HTTP_TIMEOUT)
            if not response.ok or len(response.content) < 512:
                LOGGER.info("Google TTS chunk %d/%d failed.", index + 1, len(chunks))
                return False
            part_path = os.path.join(workdir, f"part_{index:04d}.mp3")
            with open(part_path, "wb") as handle:
                handle.write(response.content)
            parts.append(part_path)

        if len(parts) == 1:
            shutil.copy2(parts[0], output_path)
        else:
            _concat_audio(parts, output_path)
        return _valid_audio(output_path)
    except requests.RequestException as exc:
        LOGGER.info("Google TTS request failed: %s", exc)
        return False
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def _concat_audio(parts: List[str], output_path: str) -> None:
    """Concatenate MP3 chunks into one file using the bundled ffmpeg."""
    from utility.audio.audio_mixer import run_ffmpeg

    list_file = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8")
    try:
        for part in parts:
            list_file.write(f"file '{part}'\n")
        list_file.close()
        run_ffmpeg(["-f", "concat", "-safe", "0", "-i", list_file.name, "-c", "copy", output_path])
    finally:
        try:
            os.remove(list_file.name)
        except OSError:
            pass


# ----------------------------------------------------------------------
# Voice alternatives
# ----------------------------------------------------------------------
def alternative_voices(voice_id: str, limit: int = 4) -> List[str]:
    """Return other voices with the same gender and accent, then any safe default."""
    try:
        from utility.audio.voice_profiles import DEFAULT_VOICE, VOICE_PROFILES, get_voice
    except Exception:  # noqa: BLE001
        return []

    original = get_voice(voice_id)
    same_accent: List[str] = []
    same_gender: List[str] = []
    for candidate_id, profile in VOICE_PROFILES.items():
        if candidate_id == voice_id:
            continue
        if profile["gender"] == original["gender"]:
            if profile["accent"] == original["accent"]:
                same_accent.append(candidate_id)
            else:
                same_gender.append(candidate_id)

    ordered = same_accent + same_gender
    if DEFAULT_VOICE not in ordered and DEFAULT_VOICE != voice_id:
        ordered.append(DEFAULT_VOICE)
    return ordered[:limit]


# ----------------------------------------------------------------------
# Orchestrator
# ----------------------------------------------------------------------
def synthesize(
    text: str,
    output_path: str,
    voice_id: str,
    rate: str = "+0%",
    pitch: str = "+0Hz",
    accent: str = "American",
    on_status: Optional[Callable[[str], None]] = None,
) -> TTSResult:
    """Synthesize speech, walking through every engine until one succeeds."""
    report = on_status or (lambda message: None)
    errors: List[str] = []

    version_note = edge_tts_version_note()
    if version_note:
        LOGGER.warning(version_note)
        report(version_note)

    attempts: List[Tuple[str, str, Callable[[], bool]]] = [
        (
            "EdgeTTS",
            voice_id,
            lambda: edge_tts_synthesize(text, output_path, voice_id, rate, pitch),
        ),
        (
            "EdgeTTS (neutral prosody)",
            voice_id,
            lambda: edge_tts_synthesize(text, output_path, voice_id, "+0%", "+0Hz"),
        ),
    ]

    for alternative in alternative_voices(voice_id):
        attempts.append(
            (
                "EdgeTTS (alternative voice)",
                alternative,
                lambda v=alternative: edge_tts_synthesize(text, output_path, v, "+0%", "+0Hz"),
            )
        )

    attempts.append(
        (
            "Google Translate TTS",
            f"Google TTS ({GTTS_TLD_BY_ACCENT.get(accent, 'com')})",
            lambda: google_tts_synthesize(text, output_path, accent),
        )
    )

    for index, (engine, voice, run) in enumerate(attempts):
        try:
            if os.path.exists(output_path):
                os.remove(output_path)
        except OSError:
            pass
        try:
            if run():
                degraded = index > 0
                note = ""
                if degraded:
                    note = f"Fell back to {engine} with voice {voice}."
                    LOGGER.warning("Voice synthesis succeeded on fallback: %s (%s).", engine, voice)
                    report(note)
                else:
                    LOGGER.info("Voice synthesis succeeded with %s (%s).", engine, voice)
                return TTSResult(output_path, engine, voice, degraded, note)
            raise RuntimeError("No audio produced")
        except Exception as exc:  # noqa: BLE001 - continue down the chain
            reason = _classify(exc)
            errors.append(f"{engine}/{voice}: {reason}")
            next_engine = attempts[index + 1][0] if index + 1 < len(attempts) else "no further engines"
            LOGGER.warning(
                "%s failed with voice %s (reason: %s), falling back to %s...",
                engine, voice, reason, next_engine,
            )
            report(f"{engine} failed ({reason}). Trying {next_engine}...")

    hint = version_note or (
        "All voice engines failed. If you keep seeing HTTP 403, run "
        "'pip install -U edge-tts' and check that your system clock is correct."
    )
    raise TTSFailure(f"{hint} Details: " + " | ".join(errors[-4:]))


def engine_status() -> List[dict]:
    """Probe every voice engine, for the connection status dashboard."""
    results = []

    note = edge_tts_version_note()
    try:
        probe = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
        probe.close()
        ok = edge_tts_synthesize("Connection test.", probe.name, "en-US-AndrewNeural")
        results.append({
            "engine": "EdgeTTS",
            "ok": ok,
            "message": "Working." if ok else "Returned no audio.",
            "note": note,
        })
        os.remove(probe.name)
    except Exception as exc:  # noqa: BLE001
        results.append({
            "engine": "EdgeTTS",
            "ok": False,
            "message": _classify(exc),
            "note": note,
        })

    try:
        probe = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
        probe.close()
        ok = google_tts_synthesize("Connection test.", probe.name)
        results.append({
            "engine": "Google Translate TTS (fallback)",
            "ok": ok,
            "message": "Working." if ok else "Unavailable.",
            "note": "",
        })
        os.remove(probe.name)
    except Exception as exc:  # noqa: BLE001
        results.append({
            "engine": "Google Translate TTS (fallback)",
            "ok": False,
            "message": _classify(exc),
            "note": "",
        })

    return results
