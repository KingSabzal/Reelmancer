"""Streamlit UI for building a video from any article, paper or video link."""

from __future__ import annotations

from typing import Any, Dict

import streamlit as st

from utility.articles.article_analyzer import analyze, build_analyzed_brief
from utility.articles.article_extractor import Article, ExtractionError, extract, suggest_settings
from ui.ui_helpers import grouped_select, inject_dropdown_css, set_select_value
from utility.content.video_styles import list_styles

EXAMPLES = [
    "https://en.wikipedia.org/wiki/Bioluminescence",
    "https://arxiv.org/abs/1706.03762",
    "https://www.bbc.com/news",
]


def render_url_tab() -> None:
    """Render the From URL tab: paste a link, review the extract, apply the settings."""
    inject_dropdown_css()
    st.markdown("## \U0001F517 Create From a Link")
    st.caption(
        "Paste a news article, blog post, research paper or YouTube link. The page is "
        "read automatically and the script is written from its actual content, so no "
        "facts are invented. Visuals still come from real stock footage."
    )

    url = st.text_input(
        "Article or video URL",
        value=st.session_state.get("url_input", ""),
        placeholder="https://www.example.com/news/article",
        key="url_input_field",
    )
    st.caption("Examples: " + "  |  ".join(EXAMPLES))

    if st.button("\U0001F4C4 Read this page", type="primary", use_container_width=True):
        if not url.strip():
            st.error("Paste a link first.")
        else:
            with st.spinner("Downloading and reading the page..."):
                try:
                    article = extract(url.strip())
                    analysis = analyze(article)
                    st.session_state["extracted_article"] = article
                    st.session_state["article_analysis"] = analysis
                    st.session_state["url_input"] = url.strip()
                    if article.was_enriched:
                        st.success(
                            f"Read {article.word_count} words from {article.site}. The page "
                            f"was thin, so {len(article.supporting_sources)} linked page(s) "
                            f"that name this subject were added: "
                            f"{article.total_word_count} words and "
                            f"{len(analysis.key_facts)} key facts in total."
                        )
                    else:
                        st.success(
                            f"Read and analysed {article.word_count} words from "
                            f"{article.site}: {len(analysis.key_facts)} key facts found."
                        )
                except ExtractionError as exc:
                    st.session_state.pop("extracted_article", None)
                    st.error(str(exc))
                except Exception as exc:  # noqa: BLE001
                    st.session_state.pop("extracted_article", None)
                    st.error(f"Could not read that page: {exc}")

    article: Article | None = st.session_state.get("extracted_article")
    analysis = st.session_state.get("article_analysis")
    if not article or analysis is None:
        return

    st.markdown("---")
    st.markdown("### What the analysis found")

    metrics = st.columns(5)
    metrics[0].metric(
        "Words read",
        article.total_word_count,
        delta=(f"+{len(article.supporting_text.split())} supporting"
               if article.was_enriched else None),
    )
    metrics[1].metric("Key facts", len(analysis.key_facts))
    metrics[2].metric("Numbers", len(analysis.numbers))
    metrics[3].metric("Angle", analysis.emotion.title())
    metrics[4].metric("Substance", f"{analysis.substance_score:.0f}")

    if analysis.sufficiency == "insufficient":
        st.error(f"\u26A0\uFE0F Not much to work with. {analysis.sufficiency_note}")
    elif analysis.sufficiency == "thin":
        st.warning(f"\u26A0\uFE0F Limited source material. {analysis.sufficiency_note}")

    if article.was_enriched:
        with st.expander(
            f"Supporting pages used ({len(article.supporting_sources)})", expanded=False
        ):
            st.caption(
                "The main page was too short to fill a video on its own. Only linked "
                "pages that actually name this subject were used; broad background "
                "pages were rejected so the video does not drift off topic."
            )
            for source in article.supporting_sources:
                st.markdown(
                    f"- **{source['title']}** - names the subject "
                    f"{source['mentions']}x, added {source['words']} words  \n"
                    f"  `{source['url']}`"
                )

    st.text_input(
        "Title (cleaned automatically, edit if you want)",
        value=analysis.clean_title, key="url_title",
    )

    with st.expander(f"Top facts the script will be built on ({len(analysis.key_facts)})",
                     expanded=True):
        for index, fact in enumerate(analysis.key_facts[:8], 1):
            label = f"  _{', '.join(fact.kinds)}_" if fact.kinds else ""
            st.markdown(f"**{index}.** {fact.text}{label}")

    detail_left, detail_right = st.columns(2)
    with detail_left:
        if analysis.numbers:
            st.caption("**Key numbers:** " + ", ".join(analysis.numbers[:8]))
        if analysis.entities:
            st.caption("**Key names:** " + ", ".join(analysis.entities[:6]))
    with detail_right:
        if analysis.quotes:
            st.caption("**Quotes found:** " + str(len(analysis.quotes)))
            st.caption(f'"{analysis.quotes[0][:120]}..."')

    with st.expander("Full extracted text"):
        st.write(article.text[:4000] + ("..." if article.word_count > 700 else ""))
        if article.supporting_text:
            st.markdown("**Supporting material from linked pages**")
            st.write(article.supporting_text[:3000])
        if article.author:
            st.caption(f"Author: {article.author}")
        st.caption(f"Source: {article.url}")

    st.markdown("### Video settings")
    if analysis.sufficiency == "ok":
        st.caption(
            f"Recommended duration: **{analysis.recommended_duration} seconds**, "
            f"calculated from {len(analysis.key_facts)} usable facts. A short news brief "
            "and a long deep dive no longer produce the same length."
        )
    else:
        st.caption(
            f"Recommended duration: **{analysis.recommended_duration} seconds**. This is "
            f"capped by how much the source actually says: about {analysis.source_words} "
            f"words of usable material supports roughly "
            f"{analysis.max_supported_duration}s. Going longer means inventing content."
        )
    duration = st.slider(
        "Duration (seconds)",
        20, 600,
        int(st.session_state.get("url_duration", analysis.recommended_duration)),
        step=5, key="url_duration_slider",
    )
    suggestion: Dict[str, Any] = suggest_settings(article, duration, analysis)

    left, right = st.columns([3, 2])
    with left:
        style_name = grouped_select(
            "Style",
            list_styles(),
            default=suggestion["video_style"],
            key="url_style_picker",
            help_text=f"Auto-detected subject: {suggestion['category']}",
        )
    right.metric("Detected category", suggestion["category"])
    right.metric("Aspect ratio (auto)", suggestion["aspect_ratio"])
    right.metric("Reading time", f"{analysis.reading_time_seconds}s")

    st.caption(
        f"Suggested voice: {suggestion['voice_name']} | "
        f"Captions: {suggestion['caption_style']} | "
        f"Music: {', '.join(suggestion['music_mood'][:3])}"
    )

    st.info(
        "The whole article is analysed, not just the headline. The ranked facts, numbers, "
        "names and quotes above are handed to the script writer, and the script is "
        "written strictly from them. Nothing is invented."
    )

    if st.button("\u2713 Use this article in the Create Video tab", type="primary",
                 use_container_width=True):
        title = st.session_state.get("url_title") or analysis.clean_title
        st.session_state["form_topic"] = title
        st.session_state["form_title"] = title
        st.session_state["form_style"] = style_name
        st.session_state["form_duration"] = duration
        st.session_state["form_voice"] = suggestion["voice_id"]
        st.session_state["form_caption_style"] = suggestion["caption_style"]
        st.session_state["source_material"] = build_analyzed_brief(article, analysis)
        st.session_state["source_site"] = article.site
        st.session_state["detected_niche"] = suggestion["category"]
        st.session_state["source_url"] = article.url
        st.session_state.pop("selected_suggestion", None)
        set_select_value("style_picker", style_name)
        set_select_value("caption_picker", suggestion["caption_style"])
        st.success(
            "Applied. Open the Create Video tab and press Generate Video. "
            "The script will be based on this article."
        )
