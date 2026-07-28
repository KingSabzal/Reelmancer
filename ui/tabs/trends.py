"""Streamlit UI components for the Discover Trends tab."""

from __future__ import annotations

from typing import Any, Dict, List

import streamlit as st

from utility.trends.trend_cache_manager import TrendCacheManager
from ui.ui_helpers import inject_dropdown_css, set_select_value
from utility.trends.viral_title_generator import ViralTitleGenerator

SCAN_STEPS = [
    ("google_trends", "Scanning 15 countries on Google Trends..."),
    ("twitter", "Analyzing Twitter/X trending..."),
    ("reddit", "Checking Reddit popular..."),
    ("youtube", "Scanning YouTube trending..."),
    ("tiktok", "Reading TikTok trending hashtags..."),
    ("news", "Analyzing news sources..."),
    ("hackernews", "Reading Hacker News..."),
    ("producthunt", "Checking Product Hunt..."),
    ("wikipedia", "Reading Wikipedia In The News..."),
    ("generate", "Generating viral title suggestions..."),
]

CATEGORIES = [
    "All", "Technology", "Entertainment", "Science", "Politics", "Sports",
    "Health", "Business", "Culture", "Mystery", "Controversy",
]


def score_badge_html(badge: Dict[str, str], score: float) -> str:
    """Return the HTML for a colored viral score badge."""
    return (
        f"<span style='background:{badge['color']};color:#fff;padding:4px 12px;"
        f"border-radius:14px;font-weight:700;font-size:0.85rem'>"
        f"{badge['emoji']} {badge['label']} {score:.0f}/100</span>"
    )


def render_suggestion_card(suggestion: Dict[str, Any], index: int) -> None:
    """Render one viral title suggestion card with select and preview actions."""
    settings = suggestion["settings"]
    with st.container(border=True):
        st.markdown(score_badge_html(suggestion["badge"], suggestion["viral_score"]), unsafe_allow_html=True)
        st.markdown(f"### {suggestion['title']}")
        st.caption(
            f"Style: {settings['video_style']} | Duration: {settings['duration_seconds']}s | "
            f"Aspect: {settings['aspect_ratio']}"
        )
        st.caption(f"Voice: {settings['voice_description']}")
        st.caption(f"Music: {', '.join(settings['music_mood'][:3])}")
        st.caption(
            f"Category: {suggestion['category']} | Angle: {suggestion['angle']} | "
            f"Uniqueness: {suggestion['uniqueness']:.0f}%"
        )
        left, right = st.columns(2)
        if left.button("Select This", key=f"select_{index}", use_container_width=True, type="primary"):
            apply_suggestion(suggestion)
            st.success("Settings applied. Open the Create Video tab to generate.")
        if right.button("Preview", key=f"preview_{index}", use_container_width=True):
            st.json(
                {
                    "topic": suggestion["topic"],
                    "source_trend": suggestion["source_trend"],
                    "keywords": suggestion["keywords"],
                    "score_components": suggestion["score_components"],
                    "auto_applied_settings": settings,
                }
            )


def apply_suggestion(suggestion: Dict[str, Any]) -> None:
    """Push every auto-applied setting into the Create Video form state."""
    settings = suggestion["settings"]
    st.session_state["form_topic"] = suggestion["topic"] or suggestion["title"]
    st.session_state["form_title"] = suggestion["title"]
    st.session_state["form_style"] = settings["video_style"]
    set_select_value("style_picker", settings["video_style"])
    set_select_value("caption_picker", settings["caption_style"])
    st.session_state["form_duration"] = settings["duration_seconds"]
    st.session_state["form_voice"] = settings["voice_id"]
    st.session_state["form_caption_style"] = settings["caption_style"]
    st.session_state["form_emoji"] = settings["emoji_enabled"]
    st.session_state["form_pattern_interrupts"] = settings["pattern_interrupts"]
    st.session_state["form_target_platforms"] = settings["target_platforms"]
    st.session_state["selected_suggestion"] = suggestion


def render_discover_tab() -> None:
    """Render the whole Discover Trends tab."""
    inject_dropdown_css()
    cache = TrendCacheManager()
    st.markdown("## \U0001F525 VIRAL TREND DISCOVERY")
    st.markdown("---")

    top_left, top_mid, top_right = st.columns([2, 1, 1])
    discover = top_left.button(
        "\U0001F30D Discover Viral Trends", type="primary", use_container_width=True
    )
    refresh = top_mid.button("Refresh Trends", use_container_width=True)
    category = top_right.selectbox("Category", CATEGORIES, key="trend_category")

    st.caption(cache.last_updated_label())

    if discover or refresh:
        progress_bar = st.progress(0.0)
        status = st.empty()
        completed = {"count": 0}

        def report(key: str, message: str) -> None:
            """Update the scan progress indicators."""
            completed["count"] += 1
            label = dict(SCAN_STEPS).get(key, f"Fetching {key}...")
            status.info(f"{label} ({message})")
            progress_bar.progress(min(completed["count"] / (len(SCAN_STEPS) * 2), 0.95))

        try:
            generator = ViralTitleGenerator()
            for _key, label in SCAN_STEPS[:1]:
                status.info(label)
            result = generator.generate(
                count=12, force_refresh=refresh, category=category, progress=report
            )
            progress_bar.progress(1.0)
            status.success(
                f"{len(result['suggestions'])} unique suggestions generated "
                f"(average uniqueness {result['uniqueness_average']:.0f}%)."
            )
            st.session_state["trend_result"] = result
        except Exception as exc:  # noqa: BLE001
            progress_bar.empty()
            status.error(f"Trend discovery failed: {exc}")

    result = st.session_state.get("trend_result")
    if result:
        suggestions: List[Dict[str, Any]] = result["suggestions"]
        if category != "All":
            filtered = [s for s in suggestions if s["category"] == category]
            suggestions = filtered or suggestions
        suggestions = sorted(suggestions, key=lambda s: s["viral_score"], reverse=True)

        summary = result["trend_summary"]
        metrics = st.columns(4)
        metrics[0].metric("Trends analyzed", summary["total"])
        metrics[1].metric("Cross-platform", summary["cross_platform"])
        metrics[2].metric("Suggestions", len(suggestions))
        metrics[3].metric("Uniqueness", f"{result['uniqueness_average']:.0f}%")

        columns = st.columns(2)
        for index, suggestion in enumerate(suggestions):
            with columns[index % 2]:
                render_suggestion_card(suggestion, index)

    st.markdown("---")
    st.markdown("### \u270F\uFE0F Or enter your own custom topic below")
    custom = st.text_input("Custom topic", key="custom_trend_topic",
                           placeholder="Example: why deep sea mining just became legal")
    if st.button("Use this custom topic", use_container_width=True) and custom.strip():
        st.session_state["form_topic"] = custom.strip()
        st.session_state["form_title"] = ""
        st.session_state.pop("selected_suggestion", None)
        st.success("Custom topic applied. Open the Create Video tab to generate.")
