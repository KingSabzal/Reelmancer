"""End-to-end production pipeline.

The render path is: search queries per timed segment -> download clips ->
set_start/set_end trimming -> CompositeVideoClip stitching -> write_videofile.
Voice selection, mixing, captions, watermark and pattern interrupts are layered
on top of that.
"""

from __future__ import annotations

import logging
import os
import tempfile
from typing import Any, Callable, Dict, List, Optional, Tuple

from utility.core import compat  # noqa: F401  (restores Pillow constants for MoviePy)
from utility.publishing.algorithmic_standards import aspect_ratio_for_duration, compliance_report, resolution_for_aspect
from utility.audio.audio_mixer import AudioMixer, cut_silences, probe_duration
from utility.video.encoding_standards import (
    AUDIO_BITRATE_KBPS,
    AUDIO_SAMPLE_RATE,
    build_ffmpeg_params,
    conform_to_spec,
    encoding_report,
    normalize_frame_rate,
)
from utility.video.animated_watermark import AnimatedWatermark
from utility.video.caption_renderer import CaptionRenderer, generate_timed_captions, word_level_captions
from utility.video.caption_styles import caption_style_for_video_style
from utility.core.config_manager import get_config
from utility.audio.intelligent_voice_selector import IntelligentVoiceSelector
from utility.audio.key_moment_detector import KeyMomentDetector
from utility.media.media_manager import MediaSourceManager
from utility.video.pattern_interrupt_engine import PatternInterruptEngine
from utility.content.script_generator import ScriptGenerator
from utility.content.video_search_query_generator import (
    get_video_search_queries_timed,
    merge_empty_intervals as merge_intervals_original,
)
from utility.content.video_styles import get_style

LOGGER = logging.getLogger("pipeline")
if not LOGGER.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("[%(name)s] %(message)s"))
    LOGGER.addHandler(_h)
LOGGER.setLevel(logging.INFO)

from utility.core.paths import BIN_DIR, WORK_DIR

os.makedirs(WORK_DIR, exist_ok=True)


def register_ffmpeg() -> None:
    """Make the bundled ffmpeg binary discoverable on Windows (original behaviour)."""
    try:
        import imageio_ffmpeg

        exe = imageio_ffmpeg.get_ffmpeg_exe()
        bin_dir = BIN_DIR
        os.makedirs(bin_dir, exist_ok=True)
        target = os.path.join(bin_dir, "ffmpeg.exe" if os.name == "nt" else "ffmpeg")
        if not os.path.exists(target):
            import shutil

            shutil.copy2(exe, target)
        if bin_dir not in os.environ.get("PATH", ""):
            os.environ["PATH"] = bin_dir + os.pathsep + os.environ.get("PATH", "")
    except Exception as exc:  # noqa: BLE001
        LOGGER.info("ffmpeg registration skipped: %s", exc)


# ----------------------------------------------------------------------
# Text to speech
# ----------------------------------------------------------------------
def synthesize_voice(
    text: str,
    output_path: str,
    voice_id: str,
    tts_settings: Dict[str, str],
    accent: str = "American",
    on_status=None,
) -> Dict[str, Any]:
    """Synthesize the voiceover through the multi-engine fallback chain.

    Handles the Microsoft HTTP 403 failure by trying neutral prosody, sibling voices
    and finally a completely different free engine.
    """
    from utility.audio.tts_engines import synthesize as multi_engine_synthesize

    result = multi_engine_synthesize(
        text=text,
        output_path=output_path,
        voice_id=voice_id,
        rate=tts_settings.get("rate", "+0%"),
        pitch=tts_settings.get("pitch", "+0Hz"),
        accent=accent,
        on_status=on_status,
    )
    return {
        "path": result.path,
        "engine": result.engine,
        "voice": result.voice,
        "degraded": result.degraded,
        "note": result.note,
    }


def build_ssml(script: str, style_name: str, voice_id: str) -> str:
    """Build an SSML document with pauses and emphasis (used for reference/export)."""
    style = get_style(style_name)
    sentences = [s.strip() for s in script.replace("!", "!|").replace("?", "?|").replace(".", ".|").split("|") if s.strip()]
    break_ms = 260 if "slow" in style["pacing"] else 140
    rate_word = "slow" if "slow" in style["pacing"] else ("fast" if "fast" in style["pacing"] else "medium")
    body = []
    for sentence in sentences:
        emphasis = "strong" if sentence.endswith("!") else "moderate"
        body.append(f'<emphasis level="{emphasis}">{sentence}</emphasis><break time="{break_ms}ms"/>')
    return (
        '<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" xml:lang="en-US">'
        f'<voice name="{voice_id}"><prosody rate="{rate_word}">' + "".join(body) +
        "</prosody></voice></speak>"
    )


# ----------------------------------------------------------------------
# Consecutive shots overlap by this much so a one-frame rounding difference at a
# cut cannot expose the black background underneath. At 30 fps this is two frames.
JOIN_OVERLAP_SECONDS = 0.07

# Search query timing
# ----------------------------------------------------------------------
# ----------------------------------------------------------------------
# Pipeline
# ----------------------------------------------------------------------
class VideoPipeline:
    """Runs the full generation pipeline and reports progress."""

    STAGES = [
        "Script generation",
        "Voice synthesis",
        "Timed captions",
        "Sourcing footage",
        "Music and sound effects",
        "Audio mixing",
        "Rendering",
    ]

    def __init__(self, config=None, progress: Optional[Callable[[float, str], None]] = None):
        self.config = config or get_config()
        self.progress = progress or (lambda fraction, message: None)
        self.media = MediaSourceManager(self.config)
        register_ffmpeg()

    def _step(self, index: int, message: str) -> None:
        """Report progress for a numbered pipeline stage."""
        self.progress(index / len(self.STAGES), message)

    # ------------------------------------------------------------------
    def run(
        self,
        topic: str,
        style_name: str,
        duration_seconds: int,
        voice_id: Optional[str] = None,
        caption_style: Optional[str] = None,
        emoji_enabled: bool = False,
        pattern_interrupts: bool = True,
        watermark_settings: Optional[Dict[str, Any]] = None,
        output_path: Optional[str] = None,
        source_material: Optional[str] = None,
        source_site: str = "",
    ) -> Dict[str, Any]:
        """Produce a finished video and return the full result payload.

        When source_material is given (an extracted article) the script is grounded in
        it and no facts are invented.
        """
        style = get_style(style_name)
        aspect_ratio = aspect_ratio_for_duration(duration_seconds)
        width, height = resolution_for_aspect(aspect_ratio)
        landscape = aspect_ratio == "16:9"
        platform = "youtube_longform" if landscape else "youtube_shorts"
        caption_style = caption_style or caption_style_for_video_style(style_name, duration_seconds)
        output_path = output_path or os.path.join(WORK_DIR, "rendered_video.mp4")

        # 1. Script -------------------------------------------------------
        if source_material:
            self._step(0, "Writing a script from the article...")
            script_data = ScriptGenerator().generate_from_source(
                source_material, style_name, duration_seconds,
                site=source_site or "the source", fallback_topic=topic,
            )
        else:
            self._step(0, "Generating the 2026-standard script...")
            script_data = ScriptGenerator().generate(topic, style_name, duration_seconds)
        script = script_data["script"]

        # 2. Voice --------------------------------------------------------
        self._step(1, "Synthesizing the voiceover...")
        selector = IntelligentVoiceSelector()
        if not voice_id:
            voice_id = selector.select(style_name, topic)["voice_id"]
        tts_settings = selector.tts_settings(style_name, voice_id)
        voice_path = os.path.join(WORK_DIR, "audio_tts.mp3")
        from utility.audio.voice_profiles import get_voice

        voice_accent = get_voice(voice_id)["accent"]
        tts_info = synthesize_voice(
            script, voice_path, voice_id, tts_settings, accent=voice_accent,
            on_status=lambda message: self.progress(1 / len(self.STAGES), message),
        )
        if tts_info["degraded"]:
            LOGGER.warning("Voice engine fallback used: %s", tts_info["note"])
            voice_id = tts_info["voice"] if tts_info["voice"].startswith("en-") else voice_id
        # Remove pauses over 0.5s: 5-10% shorter runtime and noticeably better pace.
        tightened = os.path.join(WORK_DIR, "audio_tts_tight.mp3")
        voice_path = cut_silences(voice_path, tightened)
        audio_duration = probe_duration(voice_path)

        # 3. Captions -----------------------------------------------------
        self._step(2, "Transcribing word-level caption timings...")
        timed_captions = generate_timed_captions(voice_path)
        words = word_level_captions(voice_path)
        if not audio_duration and timed_captions:
            audio_duration = timed_captions[-1][0][1]

        # 4. Footage ------------------------------------------------------
        self._step(3, "Matching footage to each spoken sentence...")
        # Give the model the script plus the word-level timings so it returns concrete
        # visual keywords for every 2-4 second slice of narration.
        timed_searches = get_video_search_queries_timed(script, words or timed_captions)
        if not timed_searches:
            raise RuntimeError(
                "Could not build timed search queries. Check the LLM provider in Settings."
            )
        timed_urls = self.media.generate_video_url(
            timed_searches, orientation_landscape=landscape, style_name=style_name, topic=topic
        )
        timed_urls = merge_intervals_original(timed_urls) or []
        if not any(url for _interval, url in timed_urls):
            raise RuntimeError(
                "No stock footage could be found. Add a Pexels or Pixabay API key in Settings."
            )

        # 5. Music and SFX -------------------------------------------------
        self._step(4, "Selecting background music and sound effects...")
        music_url = self.media.find_music(style_name, topic)
        music_path = self.media.download_to_temp(music_url, ".mp3") if music_url else None

        detector = KeyMomentDetector(style["sfx_density"])
        moments = detector.detect(script, timed_captions, audio_duration)
        sfx_items: List[Dict[str, Any]] = []
        for moment in moments:
            url = self.media.find_sfx(moment["sfx_query"])
            path = self.media.download_to_temp(url, ".mp3") if url else None
            if path:
                sfx_items.append({"time": moment["time"], "path": path, "query": moment["sfx_query"]})

        # 6. Audio mix -----------------------------------------------------
        self._step(5, "Mixing audio to -14 LUFS with ducking...")
        mixer = AudioMixer(WORK_DIR)
        final_audio = mixer.mix(voice_path, music_path, sfx_items)

        # 7. Render --------------------------------------------------------
        self._step(6, "Rendering the final video...")
        render_stats = self._render(
            final_audio,
            timed_urls,
            timed_captions,
            words,
            caption_style,
            emoji_enabled,
            pattern_interrupts,
            watermark_settings or self.config.watermark(),
            (width, height),
            platform,
            output_path,
            landscape=landscape,
            style_name=style_name,
            topic=topic,
            timed_searches=timed_searches,
        )

        duration = probe_duration(output_path) or audio_duration
        self.progress(1.0, "Done.")
        return {
            "output_path": output_path,
            "script_data": script_data,
            "voice_id": voice_id,
            "tts_engine": tts_info["engine"],
            "tts_degraded": tts_info["degraded"],
            "tts_note": tts_info["note"],
            "tts_settings": tts_settings,
            "ssml": build_ssml(script, style_name, voice_id),
            "music_url": music_url,
            "sfx": sfx_items,
            "aspect_ratio": aspect_ratio,
            "resolution": f"{width}x{height}",
            "duration_seconds": duration,
            "caption_style": caption_style,
            "audio_mix": mixer.mix_report(),
            "compliance": compliance_report(duration, len(sfx_items), render_stats["visual_changes"]),
            "encoding": encoding_report(width, height, normalize_frame_rate(int(self.config.get("fps", 30)))),
            "clip_count": render_stats["clip_count"],
        }

    # ------------------------------------------------------------------
    def _find_replacement_clip(
        self,
        t1: float,
        t2: float,
        timed_searches: Optional[List[Any]],
        landscape: bool,
        style_name: str,
        topic: str,
        filename: str,
    ):
        """Source a different clip for a time slot whose original download failed.

        Uses the same keywords the LLM produced for this exact segment, so the
        replacement still matches the narration at that moment.
        """
        from moviepy.editor import VideoFileClip
        from utility.core.resilient_download import download_media

        keywords: List[str] = []
        for entry in timed_searches or []:
            try:
                (start, end), words = entry
            except (TypeError, ValueError):
                continue
            if abs(float(start) - t1) < 0.01 and abs(float(end) - t2) < 0.01:
                keywords = [str(w) for w in words]
                break
        if not keywords and topic:
            keywords = [topic]

        used: List[str] = []
        for keyword in keywords[:4]:
            url = self.media.find_video(keyword, landscape, used)
            if not url:
                continue
            used.append(url)
            path = download_media(url, filename, kind="video", max_attempts=3)
            if not path:
                continue
            try:
                LOGGER.info("Replaced the failed clip for %.1f-%.1fs using '%s'.", t1, t2, keyword)
                return VideoFileClip(filename)
            except Exception:  # noqa: BLE001
                continue
        return None

    def _render(
        self,
        audio_path: str,
        timed_urls: List[List[Any]],
        timed_captions: List[Tuple[Tuple[float, float], str]],
        word_timings: List[Tuple[Tuple[float, float], str]],
        caption_style: str,
        emoji_enabled: bool,
        pattern_interrupts: bool,
        watermark_settings: Dict[str, Any],
        frame_size: Tuple[int, int],
        platform: str,
        output_path: str,
        landscape: bool = False,
        style_name: str = "Cinematic",
        topic: str = "",
        timed_searches: Optional[List[Any]] = None,
    ) -> Dict[str, int]:
        """Download, trim and stitch clips, then overlay captions and the watermark."""
        from moviepy.editor import AudioFileClip, CompositeVideoClip, VideoFileClip

        width, height = frame_size
        visual_clips = []
        temp_files: List[str] = []
        interrupt_engine = PatternInterruptEngine(
            interval=float(self.config.get("pattern_interrupt_interval", 4.0)),
            enabled=pattern_interrupts,
        )

        clip_count = 0
        from utility.core.resilient_download import download_media, wait_for_internet

        failed_segments: List[Tuple[float, float]] = []
        for (t1, t2), video_url in timed_urls:
            if not video_url:
                continue
            filename = tempfile.NamedTemporaryFile(delete=False, suffix=".mp4").name
            temp_files.append(filename)

            clip = None
            # Download with retries. If the clip is genuinely unavailable, source a
            # replacement for this exact time slot instead of leaving a gap.
            candidate_urls = [video_url]
            for candidate in candidate_urls:
                path = download_media(candidate, filename, kind="video", max_attempts=5)
                if not path:
                    continue
                try:
                    clip = VideoFileClip(filename)
                    break
                except Exception as exc:  # noqa: BLE001
                    LOGGER.info("Downloaded file was not playable (%s).", exc)
                    clip = None

            if clip is None:
                # Everything failed. Check whether the connection itself is down before
                # deciding this clip is unusable.
                if not wait_for_internet(120):
                    LOGGER.warning(
                        "No internet connection while downloading segment %.1f-%.1fs.", t1, t2
                    )
                replacement = self._find_replacement_clip(
                    t1, t2, timed_searches, landscape, style_name, topic, filename,
                )
                if replacement is not None:
                    clip = replacement
                else:
                    # Leaving the slot empty would expose the black composite
                    # background for the whole segment, which reads as a flash at
                    # the cut. Extend the shot that is already on screen instead.
                    if visual_clips:
                        previous = visual_clips[-1]
                        visual_clips[-1] = previous.set_end(t2)
                        LOGGER.warning(
                            "Segment %.1f-%.1fs could not be filled; the previous "
                            "shot was extended over it.", t1, t2,
                        )
                    else:
                        LOGGER.warning(
                            "Segment %.1f-%.1fs could not be filled and there is no "
                            "earlier shot to extend.", t1, t2,
                        )
                    failed_segments.append((t1, t2))
                    continue

            # Place the clip on its timeline segment. The end is extended by a few
            # frames so consecutive shots overlap slightly: without the overlap a
            # rounding difference of one frame between the outgoing clip ending and
            # the incoming clip starting exposes the black composite background,
            # which reads as a flicker at the cut. The overlap is hidden underneath
            # the next clip, so nothing is visibly duplicated.
            clip = clip.set_start(t1).set_end(t2 + JOIN_OVERLAP_SECONDS)
            # Trim before scaling so only the needed seconds are ever decoded. A source
            # clip can be minutes long while the segment needs three seconds.
            # Fill the frame edge to edge, then centre-crop the overflow. Resizing to
            # "fit" would leave black bars, so we scale by the larger ratio and crop.
            # This is a geometric operation only: no color grading, LUT or filter.
            scale = max(width / clip.w, height / clip.h)
            clip = clip.resize(scale)
            if clip.w > width or clip.h > height:
                clip = clip.crop(
                    x_center=clip.w / 2, y_center=clip.h / 2,
                    width=min(width, clip.w), height=min(height, clip.h),
                )
            clip = clip.set_position("center")
            if pattern_interrupts:
                effect = interrupt_engine.random.choice(
                    ["zoom_in", "zoom_out", "pan_left", "pan_right", "cut"]
                )
                clip = interrupt_engine.apply_motion(clip, effect, frame_size)
                clip = clip.set_start(t1).set_end(t2 + JOIN_OVERLAP_SECONDS)
            visual_clips.append(clip)
            clip_count += 1

        audio_clip = AudioFileClip(audio_path)
        total_duration = audio_clip.duration

        schedule = interrupt_engine.plan(total_duration)

        renderer = CaptionRenderer(caption_style, frame_size, emoji_enabled, platform)
        visual_clips.extend(renderer.build_clips(timed_captions, word_timings))

        watermark_clip = AnimatedWatermark(watermark_settings, frame_size, platform).build_clip(
            total_duration
        )
        if watermark_clip is not None:
            visual_clips.append(watermark_clip)

        video = CompositeVideoClip(visual_clips, size=frame_size)
        video = video.set_duration(total_duration).set_audio(audio_clip)

        # Encode to YouTube's published 2026 specification: H.264 High Profile,
        # constrained VBR at the recommended bitrate, BT.709 colour tagging and
        # AAC-LC at 384 kbps. Relying on the encoder default produced a much lower
        # bitrate, which costs quality after YouTube re-encodes.
        fps = normalize_frame_rate(int(self.config.get("fps", 30)))
        ffmpeg_params = build_ffmpeg_params(width, height, fps)
        try:
            video.write_videofile(
                output_path,
                codec="libx264",
                audio_codec="aac",
                fps=fps,
                preset=str(self.config.get("video_preset", "veryfast")),
                threads=2,
                # MoviePy appends its own audio arguments after ffmpeg_params, so the
                # sample rate and bitrate must be set through its own parameters or
                # they are silently overridden.
                audio_fps=AUDIO_SAMPLE_RATE,
                audio_bitrate=f"{AUDIO_BITRATE_KBPS}k",
                audio_nbytes=2,
                # Streaming the frames through a temp audio file keeps peak memory low,
                # which matters on machines with limited RAM and for longer videos.
                temp_audiofile=os.path.join(WORK_DIR, "temp_audio.m4a"),
                remove_temp=True,
                ffmpeg_params=ffmpeg_params,
                logger=None,
            )
        finally:
            # Release every decoder so memory is returned before the next render.
            for item in visual_clips:
                try:
                    item.close()
                except Exception:  # noqa: BLE001
                    pass
            try:
                video.close()
                audio_clip.close()
            except Exception:  # noqa: BLE001
                pass

        # Conform the finished file to the exact YouTube profile. This runs after the
        # writer has released the file: MoviePy appends its own -pix_fmt after any
        # custom arguments, which makes x264 fall back to the Main profile.
        if conform_to_spec(output_path, width, height, fps):
            LOGGER.info("Output conformed to the YouTube 2026 encoding profile (High).")

        for path in temp_files:
            try:
                os.remove(path)
            except OSError:
                pass

        return {
            "clip_count": clip_count,
            "visual_changes": interrupt_engine.count_visual_changes(schedule, clip_count),
        }
