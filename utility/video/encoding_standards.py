"""YouTube 2026 encoding specifications.

Every value here comes from YouTube's published upload recommendations:

    Container   MP4
    Video       H.264 High Profile, CABAC, closed GOP, progressive
    Audio       AAC-LC, 48 kHz, 384 kbps stereo
    Colour      BT.709 primaries / transfer / matrix, 4:2:0, 8-bit
    Bitrate     8 Mbps at 1080p30, 12 Mbps at 1080p60

Previously the renderer relied on the x264 preset default, which produced a
noticeably lower bitrate than YouTube asks for. Sending a stream that already
matches the target means YouTube's own re-encode has more information to work
with, so the published video looks sharper.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

# Target video bitrate in kbps, keyed by (height, is_high_frame_rate).
BITRATE_TABLE: Dict[Tuple[int, bool], int] = {
    (2160, False): 40_000,
    (2160, True): 60_000,
    (1440, False): 16_000,
    (1440, True): 24_000,
    (1080, False): 8_000,
    (1080, True): 12_000,
    (720, False): 5_000,
    (720, True): 7_500,
    (480, False): 2_500,
    (480, True): 4_000,
}

HIGH_FRAME_RATE_THRESHOLD = 48  # fps at or above this counts as high frame rate

AUDIO_BITRATE_KBPS = 384        # stereo, per YouTube's recommendation
AUDIO_SAMPLE_RATE = 48_000
AUDIO_CODEC = "aac"
VIDEO_CODEC = "libx264"

# Accepted frame rates. Anything else is snapped to the nearest of these.
VALID_FRAME_RATES: List[int] = [24, 25, 30, 48, 50, 60]

COLOR_SPEC = {
    "primaries": "bt709",
    "transfer": "bt709",
    "matrix": "bt709",
    "pixel_format": "yuv420p",   # 4:2:0, 8-bit
    "range": "tv",
}


def normalize_frame_rate(fps: int) -> int:
    """Snap a frame rate to the nearest value YouTube accepts."""
    return min(VALID_FRAME_RATES, key=lambda valid: abs(valid - int(fps)))


def quality_tier(width: int, height: int) -> int:
    """Return the 16:9-equivalent height used for bitrate lookup.

    YouTube's bitrate table is written for landscape. A 1080x1920 vertical Short is
    a 1080p-class stream, but its pixel *height* is 1920, so looking the bitrate up
    by height alone charged it 4K rates. Using the smaller dimension gives the
    correct quality tier for both orientations.
    """
    return min(int(width), int(height))


def target_bitrate_kbps(height: int, fps: int, width: Optional[int] = None) -> int:
    """Return the recommended video bitrate for a resolution and frame rate."""
    if width is not None:
        height = quality_tier(width, height)
    high_fps = int(fps) >= HIGH_FRAME_RATE_THRESHOLD
    if (height, high_fps) in BITRATE_TABLE:
        return BITRATE_TABLE[(height, high_fps)]
    # Unlisted height: scale from the closest known entry by pixel count.
    known_heights = sorted({h for h, _ in BITRATE_TABLE})
    closest = min(known_heights, key=lambda h: abs(h - height))
    base = BITRATE_TABLE[(closest, high_fps)]
    return max(1_000, int(base * (height / closest) ** 2))


def build_ffmpeg_params(
    width: int, height: int, fps: int, faststart: bool = True
) -> List[str]:
    """Return the ffmpeg arguments that make the output match YouTube's spec.

    Uses a constrained VBR (average bitrate with a bufsize cap), which is what
    YouTube expects, rather than the encoder's default quality-based rate control.
    """
    fps = normalize_frame_rate(fps)
    bitrate = target_bitrate_kbps(height, fps, width)

    params: List[str] = [
        # --- H.264 profile settings YouTube asks for ---
        "-profile:v", "high",
        "-level:v", "4.2",
        "-coder", "1",              # CABAC entropy coding
        "-pix_fmt", COLOR_SPEC["pixel_format"],
        # --- Constrained VBR at the recommended bitrate ---
        "-b:v", f"{bitrate}k",
        "-maxrate", f"{int(bitrate * 1.5)}k",
        "-bufsize", f"{bitrate * 2}k",
        # --- Closed GOP, keyframe every 2 seconds ---
        "-g", str(fps * 2),
        "-keyint_min", str(fps),
        "-sc_threshold", "0",
        "-flags", "+cgop",
        # --- Colour tagging so players and YouTube read BT.709 correctly ---
        "-colorspace", COLOR_SPEC["matrix"],
        "-color_primaries", COLOR_SPEC["primaries"],
        "-color_trc", COLOR_SPEC["transfer"],
        "-color_range", COLOR_SPEC["range"],
        # --- Audio ---
        "-b:a", f"{AUDIO_BITRATE_KBPS}k",
        "-ar", str(AUDIO_SAMPLE_RATE),
        "-ac", "2",
        # --- Muxing ---
        "-max_muxing_queue_size", "1024",
    ]
    if faststart:
        params += ["-movflags", "+faststart"]
    return params


def conform_to_spec(path: str, width: int, height: int, fps: int) -> bool:
    """Re-encode the finished file so it exactly matches the YouTube 2026 spec.

    MoviePy appends its own ``-pix_fmt`` after any custom arguments, which makes x264
    fall back to the Main profile. A single fast pass over the finished file fixes the
    profile, GOP structure and colour tagging without touching the audio.
    """
    import os
    import tempfile

    from utility.audio.audio_mixer import run_ffmpeg

    if not os.path.exists(path):
        return False
    fps = normalize_frame_rate(fps)
    bitrate = target_bitrate_kbps(height, fps, width)
    # Create the temp file beside the target. Using the system temp directory failed
    # with "Invalid cross-device link" whenever /tmp is a different filesystem, which
    # is common on Linux containers and on Windows when the project sits on D:.
    directory = os.path.dirname(os.path.abspath(path)) or "."
    handle = tempfile.NamedTemporaryFile(
        delete=False, suffix=".mp4", prefix=".conform_", dir=directory
    )
    handle.close()
    temp_output = handle.name
    try:
        run_ffmpeg([
            "-i", path,
            "-c:v", VIDEO_CODEC,
            "-profile:v", "high",
            "-level:v", "4.2",
            "-preset", "veryfast",
            "-coder", "1",
            "-pix_fmt", COLOR_SPEC["pixel_format"],
            "-b:v", f"{bitrate}k",
            "-maxrate", f"{int(bitrate * 1.5)}k",
            "-bufsize", f"{bitrate * 2}k",
            "-g", str(fps * 2),
            "-keyint_min", str(fps),
            "-sc_threshold", "0",
            "-flags", "+cgop",
            "-colorspace", COLOR_SPEC["matrix"],
            "-color_primaries", COLOR_SPEC["primaries"],
            "-color_trc", COLOR_SPEC["transfer"],
            "-color_range", COLOR_SPEC["range"],
            "-c:a", "copy",
            "-movflags", "+faststart",
            temp_output,
        ])
        if os.path.getsize(temp_output) > 1024:
            try:
                os.replace(temp_output, path)
            except OSError:
                # Different filesystems: fall back to a copy.
                import shutil

                shutil.move(temp_output, path)
            return True
    except Exception as exc:  # noqa: BLE001 - the original file is still valid
        import logging

        logging.getLogger("encoding").warning(
            "Could not conform the output to the High profile: %s", str(exc)[:200]
        )
    finally:
        if os.path.exists(temp_output):
            try:
                os.remove(temp_output)
            except OSError:
                pass
    return False


def encoding_report(width: int, height: int, fps: int) -> Dict[str, Any]:
    """Human-readable summary of the encode, for the UI."""
    fps = normalize_frame_rate(fps)
    return {
        "resolution": f"{width}x{height}",
        "quality_tier": f"{quality_tier(width, height)}p-class",
        "frame_rate": fps,
        "video_codec": "H.264 High Profile (CABAC, closed GOP)",
        "video_bitrate_kbps": target_bitrate_kbps(height, fps, width),
        "audio_codec": "AAC-LC",
        "audio_bitrate_kbps": AUDIO_BITRATE_KBPS,
        "audio_sample_rate": AUDIO_SAMPLE_RATE,
        "color_space": "BT.709",
        "chroma_subsampling": "4:2:0 (8-bit)",
        "scan_type": "progressive",
        "faststart": True,
    }
