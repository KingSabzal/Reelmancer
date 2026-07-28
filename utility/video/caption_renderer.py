"""Caption rendering: faster-whisper word timings plus styled, animated text clips.

The generated voiceover is transcribed locally with faster-whisper, which emits
[[start, end], word] pairs. Those timings drive both the caption animation and the
footage matching, so the words on screen land exactly with the words being spoken.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any, List, Optional, Tuple

import requests

from utility.core import compat  # noqa: F401  (restores Pillow constants for MoviePy)
from utility.video.caption_styles import get_caption_style
from utility.video.safe_zone_manager import SafeZoneManager
from utility.video.text_renderer import make_text_clip

LOGGER = logging.getLogger("caption_renderer")
if not LOGGER.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("[%(name)s] %(message)s"))
    LOGGER.addHandler(_h)
LOGGER.setLevel(logging.INFO)

from utility.core.paths import FONT_CACHE_DIR
GOOGLE_FONTS_CSS = "https://fonts.googleapis.com/css2?family={family}&display=swap"
FONT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

EMOJI_PATTERN = re.compile(
    "[" "\U0001F300-\U0001FAFF" "\U00002600-\U000027BF" "\U0001F1E6-\U0001F1FF" "\u2190-\u21FF"
    "\u2B00-\u2BFF" "\uFE0F" "]+",
    flags=re.UNICODE,
)


# ----------------------------------------------------------------------
# Word-level timing
# ----------------------------------------------------------------------
def generate_timed_captions(
    audio_filename: str, model_size: str = "base", max_caption_size: int = 15
) -> List[Tuple[Tuple[float, float], str]]:
    """Transcribe the voiceover with faster-whisper and return timed caption pairs."""
    from faster_whisper import WhisperModel

    model = WhisperModel(model_size, device="cpu", compute_type="int8")
    segments, _info = model.transcribe(audio_filename, word_timestamps=True, vad_filter=False)

    word_pairs: List[Tuple[Tuple[float, float], str]] = []
    for segment in segments:
        for word in segment.words or []:
            token = word.word.strip()
            if token:
                word_pairs.append(((float(word.start), float(word.end)), token))

    if not word_pairs:
        LOGGER.warning("No words detected in the voiceover; captions will be empty.")
    return _group_words(word_pairs, max_caption_size)


def _group_words(
    word_pairs: List[Tuple[Tuple[float, float], str]], max_caption_size: int
) -> List[Tuple[Tuple[float, float], str]]:
    """Group word-level timings into short caption chunks (original behaviour)."""
    grouped: List[Tuple[Tuple[float, float], str]] = []
    buffer: List[str] = []
    start_time: Optional[float] = None
    end_time = 0.0
    for (start, end), word in word_pairs:
        if start_time is None:
            start_time = start
        candidate = " ".join(buffer + [word])
        if len(candidate) > max_caption_size and buffer:
            grouped.append(((start_time, end_time), " ".join(buffer)))
            buffer = [word]
            start_time = start
        else:
            buffer.append(word)
        end_time = end
    if buffer and start_time is not None:
        grouped.append(((start_time, end_time), " ".join(buffer)))
    return grouped


def word_level_captions(
    audio_filename: str, model_size: str = "base"
) -> List[Tuple[Tuple[float, float], str]]:
    """Return raw word-level timings (used for karaoke highlighting)."""
    from faster_whisper import WhisperModel

    model = WhisperModel(model_size, device="cpu", compute_type="int8")
    segments, _info = model.transcribe(audio_filename, word_timestamps=True, vad_filter=False)
    output: List[Tuple[Tuple[float, float], str]] = []
    for segment in segments:
        for word in segment.words or []:
            token = word.word.strip()
            if token:
                output.append(((float(word.start), float(word.end)), token))
    return output


# ----------------------------------------------------------------------
# Fonts
# ----------------------------------------------------------------------
def ensure_font(google_font: str) -> Optional[str]:
    """Download a Google Font (family:weight) into the local cache and return its path."""
    os.makedirs(FONT_CACHE_DIR, exist_ok=True)
    family, _, weight = google_font.partition(":")
    weight = weight or "400"
    safe_name = f"{family.replace(' ', '_')}_{weight}.ttf"
    local_path = os.path.join(FONT_CACHE_DIR, safe_name)
    if os.path.exists(local_path) and os.path.getsize(local_path) > 1024:
        return local_path

    query = family.replace(" ", "+") + f":wght@{weight}"
    try:
        css = requests.get(
            GOOGLE_FONTS_CSS.format(family=query),
            headers={"User-Agent": "Mozilla/5.0"},  # ask for TTF, not WOFF2
            timeout=20,
        )
        urls = re.findall(r"url\((https://[^)]+\.(?:ttf|otf))\)", css.text)
        if not urls:
            css = requests.get(
                f"https://fonts.googleapis.com/css?family={family.replace(' ', '+')}",
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=20,
            )
            urls = re.findall(r"url\((https://[^)]+\.(?:ttf|otf))\)", css.text)
        if not urls:
            LOGGER.info("No TTF found for font %s.", family)
            return None
        data = requests.get(urls[0], headers={"User-Agent": FONT_UA}, timeout=30).content
        with open(local_path, "wb") as handle:
            handle.write(data)
        return local_path
    except requests.RequestException as exc:
        LOGGER.info("Font download failed for %s: %s", family, exc)
        return None


# ----------------------------------------------------------------------
# Rendering
# ----------------------------------------------------------------------
class CaptionRenderer:
    """Builds MoviePy text clips for timed captions in a chosen caption style."""

    def __init__(
        self,
        style_name: str,
        frame_size: Tuple[int, int],
        emoji_enabled: bool = False,
        platform: str = "youtube_shorts",
    ):
        self.style = get_caption_style(style_name)
        self.width, self.height = frame_size
        self.emoji_enabled = emoji_enabled
        self.safe_zones = SafeZoneManager(platform, self.width, self.height)
        self.font_path = ensure_font(self.style["google_font"])

    # -- helpers --------------------------------------------------------
    def _clean(self, text: str) -> str:
        """Apply uppercase and emoji rules."""
        if not self.emoji_enabled:
            text = EMOJI_PATTERN.sub("", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text.upper() if self.style["uppercase"] else text

    def font_size(self) -> int:
        """Pixel font size derived from the style ratio and frame height."""
        return max(18, int(self.height * self.style["font_size_ratio"]))

    def chunk_captions(
        self, timed_captions: List[Tuple[Tuple[float, float], str]]
    ) -> List[Tuple[Tuple[float, float], str]]:
        """Re-chunk captions so each block respects the style's max_words."""
        max_words = self.style["max_words"]
        chunks: List[Tuple[Tuple[float, float], str]] = []
        for (start, end), text in timed_captions:
            words = text.split()
            if len(words) <= max_words:
                chunks.append(((start, end), text))
                continue
            span = (end - start) / max(len(words), 1)
            for index in range(0, len(words), max_words):
                group = words[index : index + max_words]
                chunk_start = start + index * span
                chunk_end = chunk_start + len(group) * span
                chunks.append(((chunk_start, chunk_end), " ".join(group)))
        return chunks

    # -- animation ------------------------------------------------------
    def _animate(self, clip, animation: str, start: float, duration: float, base_pos):
        """Attach a lightweight, GPU-free animation to a text clip."""
        x_base, y_base = base_pos

        def position(t):
            """Animated (x, y) for a given timestamp."""
            progress = min(max(t / max(duration, 0.01), 0.0), 1.0)
            x, y = x_base, y_base
            if animation == "bounce":
                y = y_base - int(18 * max(0.0, 1 - abs(progress * 4 - 1)))
            elif animation == "shake":
                x = x_base + int(6 * (1 if int(t * 20) % 2 else -1) * (1 - progress))
            elif animation == "fade_up":
                y = y_base + int(30 * (1 - min(progress * 3, 1)))
            elif animation == "slide_in":
                x = x_base - int(self.width * 0.3 * (1 - min(progress * 4, 1)))
            elif animation == "punch":
                y = y_base - int(10 * max(0.0, 1 - progress * 6))
            return (x, y)

        clip = clip.set_position(position)
        if animation in ("fade", "fade_up", "glow", "liquid", "gradient_sweep", "rainbow"):
            from moviepy.video.fx.fadein import fadein
            from moviepy.video.fx.fadeout import fadeout

            fade = min(0.12, duration / 3)
            if fade > 0.02:
                clip = fadein(clip, fade)
                clip = fadeout(clip, fade)
        if animation in ("zoom", "pop", "punch", "rotate_3d", "particle"):
            clip = clip.resize(lambda t: 1.0 + 0.10 * max(0.0, 1 - t / max(duration * 0.4, 0.01)))
        return clip

    def build_clips(
        self,
        timed_captions: List[Tuple[Tuple[float, float], str]],
        word_timings: Optional[List[Tuple[Tuple[float, float], str]]] = None,
    ) -> List[Any]:
        """Return a list of MoviePy clips for the whole caption track."""
        style = self.style
        size = self.font_size()
        clips: List[Any] = []
        animation = style["animation"]
        chunks = self.chunk_captions(timed_captions)

        for (start, end), text in chunks:
            content = self._clean(text)
            if not content:
                continue
            duration = max(end - start, 0.08)
            try:
                clip = make_text_clip(
                    content,
                    self.font_path,
                    size,
                    color=style["color"],
                    stroke_color=style["stroke_color"],
                    stroke_width=int(style["stroke_width"]),
                    max_width=int(self.width * 0.86),
                    background=style.get("background"),
                )
            except Exception as exc:  # noqa: BLE001
                LOGGER.info("Caption rasterization failed for '%s': %s", content[:30], exc)
                continue

            clip = clip.set_start(start).set_duration(duration)
            text_height = clip.h or size
            _, y = self.safe_zones.caption_position(style["position"], text_height)
            x = int((self.width - (clip.w or self.width)) / 2)
            clip = self._animate(clip, animation, start, duration, (x, y))
            clips.append(clip)

            if animation == "karaoke" and word_timings:
                clips.extend(self._karaoke_layer(word_timings, start, end, size, y))
        return clips

    def _karaoke_layer(
        self,
        word_timings: List[Tuple[Tuple[float, float], str]],
        start: float,
        end: float,
        size: int,
        y: int,
    ) -> List[Any]:
        """Overlay the currently spoken word in the highlight color."""
        overlays: List[Any] = []
        for (word_start, word_end), word in word_timings:
            if word_start < start or word_end > end + 0.01:
                continue
            content = self._clean(word)
            if not content:
                continue
            try:
                highlight = make_text_clip(
                    content,
                    self.font_path,
                    int(size * 1.06),
                    color=self.style["highlight_color"],
                    stroke_color=self.style["stroke_color"],
                    stroke_width=int(self.style["stroke_width"]),
                )
            except Exception:  # noqa: BLE001
                continue
            highlight = highlight.set_start(word_start).set_duration(
                max(word_end - word_start, 0.05)
            )
            highlight = highlight.set_position(
                (int((self.width - (highlight.w or 0)) / 2), max(y - int(size * 1.45), 0))
            )
            overlays.append(highlight)
        return overlays
