"""Create Video tab: the form, the generation run and the result panel."""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

import streamlit as st

from utility.audio.intelligent_voice_selector import IntelligentVoiceSelector
from utility.audio.voice_profiles import VOICE_PROFILES
from utility.content.video_styles import VIDEO_STYLES, list_styles
from utility.llm.llm_router import InvalidAPIKeyError
from utility.publishing.algorithmic_standards import (
    TARGETS,
    UI_DISCLOSURE_REMINDER,
    aspect_ratio_for_duration,
    content_interrupt_target,
    duration_advice,
    hook_length_target,
    word_count_for_duration,
)
from utility.publishing.seo_package_generator import SEOPackageGenerator
from ui.packages import render_packages
from ui.state import CONFIG, caption_groups, gallery, style_groups, voice_groups
from ui.ui_helpers import grouped_select, set_select_value
from utility.video.caption_styles import (
    CATEGORIES as CAPTION_CATEGORIES,
    caption_style_for_video_style,
    list_caption_styles,
)
from utility.video.video_pipeline import VideoPipeline


def render_create_tab() -> None:
    """Render the video creation form and run the pipeline."""
    st.markdown("## \U0001F3AC Create Video")

    source_material = st.session_state.get("source_material")
    if source_material:
        source_cols = st.columns([5, 1])
        source_cols[0].success(
            f"Script will be written from: {st.session_state.get('source_url', 'the linked article')}"
        )
        if source_cols[1].button("Clear source", use_container_width=True):
            for key in ("source_material", "source_site", "source_url"):
                st.session_state.pop(key, None)
            st.rerun()

    suggestion = st.session_state.get("selected_suggestion")
    if suggestion:
        st.info(
            f"Using trend suggestion: **{suggestion['title']}** "
            f"({suggestion['badge']['label']} {suggestion['viral_score']:.0f}/100)"
        )

    topic = st.text_area(
        "Topic",
        value=st.session_state.get("form_topic", ""),
        height=90,
        placeholder="Describe the video you want to create.",
    )

    styles = list_styles()
    default_style = st.session_state.get("form_style", "Cinematic")
    if st.session_state.get("_apply_trend_style"):
        set_select_value("style_picker", default_style)
        st.session_state.pop("_apply_trend_style")

    left, right = st.columns([3, 2])
    with left:
        style_name = grouped_select(
            "Style",
            styles,
            groups=style_groups(),
            group_label="Style category",
            default=default_style,
            key="style_picker",
            help_text="Filter by category or type to search all 122 styles.",
        )
    duration = right.slider(
        "Duration (seconds)", 20, 600, int(st.session_state.get("form_duration", 60)), step=5
    )

    aspect_ratio = aspect_ratio_for_duration(duration)
    word_count = word_count_for_duration(duration)
    info = st.columns(5)
    info[0].metric("Word count", word_count)
    info[1].metric("Aspect ratio (auto)", aspect_ratio)
    info[2].metric("Format", "Shorts" if duration < 120 else "Long-form")
    info[3].metric(
        "Hook window", f"{hook_length_target(duration):.0f}s",
        help=(
            "Shorts are decided in the first frame, so the hook gets one second. "
            "Long-form hooks have ten seconds to validate the click, raise the "
            "stakes and open a curiosity loop."
        ),
    )
    info[4].metric(
        "Visual change", f"{TARGETS['visual_change_interval_seconds']:.0f}s",
        help=(
            f"A new clip every {TARGETS['visual_change_interval_seconds']:.0f}s. "
            f"Separately, what is being said changes every "
            f"{content_interrupt_target(duration):.0f}s."
        ),
    )
    st.caption("The aspect ratio is calculated automatically from the duration and cannot be changed manually.")

    advice = duration_advice(duration)
    if advice["verdict"] == "optimal":
        st.success(f"{advice['format']}: {advice['note']}")
    else:
        st.warning(f"{advice['format']}: {advice['note']}")

    style = VIDEO_STYLES[style_name]
    st.caption(
        f"Tone: {style['tone']} | Pacing: {style['pacing']} | "
        f"SFX density: {style['sfx_density']} | Music: {', '.join(style['music_mood'])}"
    )

    col_a, col_b, col_c = st.columns(3)
    emoji_enabled = col_a.toggle("Emoji in captions", value=bool(st.session_state.get("form_emoji", False)))
    pattern_interrupts = col_b.toggle(
        "Pattern interrupts", value=bool(st.session_state.get("form_pattern_interrupts", True))
    )
    auto_voice = IntelligentVoiceSelector().select(style_name, topic)
    voice_options = ["Auto (recommended)"] + [
        f"{v['name']} - {v['accent']} {v['gender']} ({v['voice_id']})" for v in VOICE_PROFILES.values()
    ]
    preselected = st.session_state.get("form_voice")
    default_voice = "Auto (recommended)"
    if preselected:
        for option in voice_options:
            if preselected in option:
                default_voice = option
                break
    with col_c:
        voice_choice = grouped_select(
            "Voice",
            voice_options,
            groups=voice_groups(voice_options),
            group_label="Accent",
            default=default_voice,
            key="voice_picker",
            help_text="Leave on Auto to let the system pick the best voice for the style.",
        )
    st.caption(f"Auto-selected voice: {auto_voice['name']} ({auto_voice['accent']}, {', '.join(auto_voice['tone'][:2])})")

    caption_styles = list_caption_styles()
    default_caption = st.session_state.get(
        "form_caption_style", caption_style_for_video_style(style_name, duration)
    )
    caption_style = grouped_select(
        "Caption style",
        caption_styles,
        groups=caption_groups(),
        group_label="Caption category",
        default=default_caption,
        key="caption_picker",
        help_text=f"{len(caption_styles)} styles across {len(CAPTION_CATEGORIES)} categories.",
    )

    with st.expander("Animated watermark"):
        watermark = dict(CONFIG.watermark())
        watermark["enabled"] = st.toggle("Enable watermark", value=watermark["enabled"])
        wm_cols = st.columns(2)
        watermark["type"] = wm_cols[0].selectbox(
            "Type", ["text", "handle", "logo"], index=["text", "handle", "logo"].index(watermark["type"])
        )
        watermark["content"] = wm_cols[1].text_input("Content", value=watermark["content"])
        watermark["opacity"] = st.slider("Opacity", 0.10, 0.50, float(watermark["opacity"]), 0.01)
        pattern_options = ["random_smooth", "random_jump", "circular", "diagonal"]
        watermark["movement_pattern"] = st.selectbox(
            "Movement pattern", pattern_options, index=pattern_options.index(watermark["movement_pattern"])
        )
        speed_options = ["slow", "medium", "fast"]
        watermark["movement_speed"] = st.select_slider(
            "Movement speed", speed_options, value=watermark["movement_speed"]
        )
        watermark["change_interval"] = st.slider(
            "Change interval (seconds)", 3, 10, int(watermark["change_interval"])
        )

    st.warning(UI_DISCLOSURE_REMINDER)

    if st.button("\U0001F680 Generate Video", type="primary", use_container_width=True):
        if not topic.strip():
            st.error("Enter a topic first.")
            return
        run_generation(
            topic.strip(), style_name, duration, voice_choice, caption_style,
            emoji_enabled, pattern_interrupts, watermark,
            source_material=st.session_state.get("source_material"),
            source_site=st.session_state.get("source_site", ""),
        )

    render_result()


def run_generation(
    topic: str,
    style_name: str,
    duration: int,
    voice_choice: str,
    caption_style: str,
    emoji_enabled: bool,
    pattern_interrupts: bool,
    watermark: Dict[str, Any],
    source_material: Optional[str] = None,
    source_site: str = "",
) -> None:
    """Execute the pipeline with a live progress bar."""
    voice_id = None
    if voice_choice != "Auto (recommended)":
        voice_id = voice_choice.split("(")[-1].rstrip(")")

    progress_bar = st.progress(0.0)
    status = st.empty()

    def report(fraction: float, message: str) -> None:
        """Forward pipeline progress to the Streamlit widgets."""
        progress_bar.progress(min(max(fraction, 0.0), 1.0))
        status.info(message)

    manager = gallery()
    entry = manager.create_entry(title=topic[:80], topic=topic, style=style_name)

    try:
        pipeline = VideoPipeline(CONFIG, report)
        result = pipeline.run(
            topic=topic,
            style_name=style_name,
            duration_seconds=duration,
            voice_id=voice_id,
            caption_style=caption_style,
            emoji_enabled=emoji_enabled,
            pattern_interrupts=pattern_interrupts,
            watermark_settings=watermark,
            source_material=source_material,
            source_site=source_site,
        )

        status.info("Generating the SEO packages...")
        packages = SEOPackageGenerator().generate(
            topic=topic,
            title=result["script_data"]["title"],
            script=result["script_data"]["script"],
            style=style_name,
            duration_seconds=result["duration_seconds"],
            keywords=result["script_data"]["keywords"],
            channel_handle=watermark.get("content", "@YourChannel"),
            niche=st.session_state.get("detected_niche", style_name),
        )

        final_entry = manager.finalize_entry(
            video_id=entry["video_id"],
            source_path=result["output_path"],
            duration_seconds=result["duration_seconds"],
            aspect_ratio=result["aspect_ratio"],
            resolution=result["resolution"],
            voice_used=result["voice_id"],
            music_used=result.get("music_url") or "",
            tags=result["script_data"]["keywords"],
            seo_packages=packages,
            upload_title=packages.get("youtube", {}).get("title", ""),
        )
        progress_bar.progress(1.0)
        status.success("Video generated successfully.")
        st.session_state["last_result"] = {
            "result": result,
            "packages": packages,
            "entry": final_entry,
        }
    except InvalidAPIKeyError as exc:
        manager.mark_failed(entry["video_id"], str(exc))
        progress_bar.empty()
        st.error(f"Invalid API key: {exc} The process was stopped. Fix the key in the Settings tab.")
    except Exception as exc:  # noqa: BLE001
        manager.mark_failed(entry["video_id"], str(exc))
        progress_bar.empty()
        st.error(f"Generation failed: {exc}")


def render_result() -> None:
    """Show the finished video, compliance report and SEO packages."""
    payload = st.session_state.get("last_result")
    if not payload:
        return
    result = payload["result"]
    entry = payload["entry"] or {}
    path = entry.get("file_path") or result["output_path"]

    st.markdown("---")
    st.markdown("### Result")
    if path and os.path.exists(path):
        st.video(path)
        with open(path, "rb") as handle:
            st.download_button("Download video", handle.read(), file_name=os.path.basename(path))

    if result.get("tts_degraded"):
        st.warning(
            f"Voice engine fallback: {result.get('tts_note', '')} "
            "The narration still rendered, but for the best neural voices run "
            "`pip install -U edge-tts` and check that your system clock is correct."
        )

    script_data = result.get("script_data", {})
    if script_data.get("script_format_label"):
        bits = [f"**Narrative format:** {script_data['script_format_label']}"]
        if script_data.get("loop_line"):
            bits.append(f"**Loop line:** {script_data['loop_line']}")
        if script_data.get("next_hook"):
            bits.append(f"**Leads into:** {script_data['next_hook']}")
        st.caption("  |  ".join(bits))
        st.caption(
            "The format is rotated between videos so consecutive uploads do not look "
            "template-made, which is what YouTube's inauthentic content policy targets."
        )

    compliance = result["compliance"]
    metrics = st.columns(5)
    metrics[0].metric("Duration", f"{compliance['duration_seconds']:.1f}s")
    metrics[1].metric("Aspect", compliance["aspect_ratio"])
    metrics[2].metric("SFX", f"{compliance['sfx_count']}/{compliance['sfx_target']}")
    metrics[3].metric("Visual changes", f"{compliance['visual_changes']}/{compliance['visual_changes_target']}")
    metrics[4].metric("Loudness", f"{compliance['loudness_lufs']} LUFS")

    encoding = result.get("encoding")
    if encoding:
        with st.expander("Encoding (YouTube 2026 specification)"):
            enc_cols = st.columns(4)
            enc_cols[0].metric("Video bitrate", f"{encoding['video_bitrate_kbps'] / 1000:.0f} Mbps")
            enc_cols[1].metric("Audio", f"{encoding['audio_bitrate_kbps']} kbps")
            enc_cols[2].metric("Colour", encoding["color_space"])
            enc_cols[3].metric("Frame rate", encoding["frame_rate"])
            st.json(encoding)

    with st.expander("Script and audio details"):
        st.write(result["script_data"]["script"])
        st.json(
            {
                "hook": result["script_data"]["hook"],
                "open_loop": result["script_data"]["open_loop"],
                "payoff": result["script_data"]["payoff"],
                "cta": result["script_data"]["cta"],
                "voice": result["voice_id"],
                "tts_engine": result.get("tts_engine", ""),
                "tts_settings": result["tts_settings"],
                "audio_mix": result["audio_mix"],
                "retention_trap": result["script_data"].get("retention_trap", ""),
                "screen_text": result["script_data"].get("screen_text", []),
                "avg_sentence_words": result["script_data"].get("avg_sentence_words", 0),
                "model_used": result["script_data"]["model_used"],
            }
        )

    st.warning(UI_DISCLOSURE_REMINDER)
    render_packages(payload["packages"])
