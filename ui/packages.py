"""SEO package panel: renders the YouTube / Instagram / TikTok upload packages."""

from __future__ import annotations

import json
from typing import Any, Dict

import streamlit as st

from utility.core.naming import output_stem


def render_packages(packages: Dict[str, Any]) -> None:
    """Render the three platform packages with copy and download options."""
    st.markdown("### SEO & Platform Packages")
    youtube_tab, instagram_tab, tiktok_tab = st.tabs(["YouTube", "Instagram", "TikTok"])

    with youtube_tab:
        data = packages["youtube"]
        st.text_area("Title", data["title"], height=68, key="yt_title")

        analysis = data.get("title_analysis")
        if analysis:
            score_cols = st.columns(4)
            score_cols[0].metric("Title score", f"{analysis['score']:.0f}/100")
            score_cols[1].metric("Characters", analysis["length"])
            score_cols[2].metric(
                "Hook fits mobile", "Yes" if analysis["hook_fits_mobile"] else "No"
            )
            score_cols[3].metric(
                "Keyword early", "Yes" if analysis["keyword_in_first_30"] else "No"
            )
            if analysis.get("patterns"):
                st.caption("Patterns used: " + ", ".join(analysis["patterns"]))
            for warning in analysis.get("warnings", []):
                st.warning(warning)

        if data.get("shorts_title"):
            st.text_input(
                "Shorts title (the Shorts feed truncates earlier)",
                data["shorts_title"], key="yt_shorts_title",
            )

        scored = data.get("title_alternatives_scored") or []
        if scored:
            with st.expander(f"All {len(scored)} title candidates, scored"):
                for item in scored:
                    st.markdown(
                        f"**{item['score']:.0f}/100** ({item['length']} chars) - {item['title']}"
                    )
                    if item.get("patterns"):
                        st.caption(", ".join(item["patterns"]))
        else:
            st.text_area(
                "Alternative titles", "\n".join(data.get("alt_titles", [])),
                height=90, key="yt_alts",
            )
        st.text_area("Description", data["description"], height=320, key="yt_desc")
        st.text_area(
            f"Tags ({len(data.get('tags', []))}, {data.get('tags_char_count', 0)}/500 chars)",
            ", ".join(data.get("tags", [])), height=110, key="yt_tags",
        )
        st.caption(
            "10-15 focused tags. YouTube tags lost most of their ranking weight after "
            "2019, so padding the list adds nothing."
        )

        desc_analysis = data.get("description_analysis")
        if desc_analysis:
            if not desc_analysis.get("keywords_early"):
                st.warning(
                    "No keyword appears in the first 125 characters of the description, "
                    "which is the part shown in search results."
                )
            st.caption(
                f"Description: {desc_analysis['length']} characters "
                f"(target {desc_analysis['target'][0]}-{desc_analysis['target'][1]})."
            )
        st.text_input("Thumbnail text", data.get("thumbnail_text", ""), key="yt_thumb")
        if data.get("thumbnail_warning"):
            st.warning(data["thumbnail_warning"])
        st.text_area("Pinned comment", data.get("pinned_comment", ""), height=90, key="yt_pin")
        st.json(data.get("community_post", {}))
        st.json({"settings": data.get("settings", {}), "end_screen": data.get("end_screen_elements", []),
                 "cards": data.get("cards", []), "posting_times": data.get("recommended_posting_times", [])})
        policy = data.get("policy_check") or {}
        risks = policy.get("risks", [])
        with st.expander(
            f"Policy check ({len(risks)} issue{'s' if len(risks) != 1 else ''})",
            expanded=bool(policy.get("blocking")),
        ):
            if not risks:
                st.success("No documented policy problems found in the title, thumbnail or description.")
            for risk in risks:
                line = f"**{risk['field']}** - {risk['message']}"
                if risk.get("matched"):
                    line += f"  (matched: `{risk['matched']}`)"
                if risk["severity"] == "blocking":
                    st.error(line)
                elif risk["severity"] == "high":
                    st.warning(line)
                else:
                    st.info(line)
            if policy.get("note"):
                st.caption(policy["note"])

        download_buttons("youtube", data)

    with instagram_tab:
        data = packages["instagram"]
        st.text_input("Hook line", data.get("hook_line", ""), key="ig_hook")
        st.text_area("Caption", data.get("caption", ""), height=320, key="ig_caption")
        st.text_area(
            f"Hashtags ({len(data.get('hashtags', []))} of 5 maximum)",
            " ".join(data.get("hashtags", [])), height=68, key="ig_tags",
        )
        st.caption(data.get("hashtag_policy", ""))
        rejected = data.get("hashtag_rejected") or []
        if rejected:
            with st.expander(f"{len(rejected)} hashtags were rejected"):
                for reason in rejected:
                    st.caption(f"- {reason}")

        caption_analysis = data.get("caption_analysis")
        if caption_analysis:
            cap_cols = st.columns(3)
            cap_cols[0].metric(
                "Keyword in first 125", "Yes" if caption_analysis.get("keywords_early") else "No"
            )
            cap_cols[1].metric(
                "Hook complete", "Yes" if caption_analysis.get("hook_complete") else "No"
            )
            cap_cols[2].metric("Keyword coverage", f"{caption_analysis.get('coverage', 0) * 100:.0f}%")
        if data.get("caption_warning"):
            st.warning(data["caption_warning"])
        st.text_input("Cover text", data.get("cover_text", ""), key="ig_cover")
        st.text_area("Alt text", data.get("alt_text", ""), height=68, key="ig_alt")
        st.json({"posting_times": data.get("recommended_posting_times", []),
                 "cross_promotion": data.get("cross_promotion", {}),
                 "hashtag_groups": data.get("hashtags_grouped", {})})
        download_buttons("instagram", data)

    with tiktok_tab:
        data = packages["tiktok"]
        st.text_input("Hook line", data.get("hook_line", ""), key="tt_hook")
        st.text_area("Caption", data.get("caption", ""), height=140, key="tt_caption")
        st.text_input(
            f"Hashtags ({len(data.get('hashtags', []))})",
            " ".join(data.get("hashtags", [])), key="tt_tags",
        )
        st.caption(data.get("hashtag_policy", ""))
        if data.get("caption_warning"):
            st.warning(data["caption_warning"])
        st.info(
            "TikTok requires the AI-content and commercial-content toggles. Undisclosed "
            "content leaves the For You feed within 24 hours."
        )
        st.text_input("Cover text", data.get("cover_text", ""), key="tt_cover")
        st.json({"sound": data.get("sound", {}), "settings": data.get("settings", {}),
                 "posting_times": data.get("recommended_posting_times", []),
                 "cross_promotion": data.get("cross_promotion", {})})
        download_buttons("tiktok", data)


def package_stem() -> str:
    """File name stem for package downloads, taken from the YouTube upload title."""
    payload = st.session_state.get("last_result") or {}
    entry = payload.get("entry") or {}
    if entry.get("file_stem"):
        return str(entry["file_stem"])
    packages = payload.get("packages") or {}
    title = packages.get("youtube", {}).get("title", "")
    topic = (payload.get("result") or {}).get("topic", "")
    return output_stem(title, topic)


def download_buttons(platform: str, data: Dict[str, Any]) -> None:
    """Offer per-component and full-package downloads."""
    stem = package_stem()
    columns = st.columns(3)
    columns[0].download_button(
        f"Download {platform} package (JSON)",
        json.dumps(data, indent=2, ensure_ascii=False),
        file_name=f"{stem}-{platform}.json",
        mime="application/json",
        key=f"dl_{platform}_json",
    )
    text_blocks = []
    for key, value in data.items():
        if isinstance(value, str):
            text_blocks.append(f"== {key.upper()} ==\n{value}\n")
        elif isinstance(value, list) and all(isinstance(v, str) for v in value):
            text_blocks.append(f"== {key.upper()} ==\n" + ", ".join(value) + "\n")
    columns[1].download_button(
        f"Download {platform} text",
        "\n".join(text_blocks),
        file_name=f"{stem}-{platform}.txt",
        key=f"dl_{platform}_txt",
    )
    columns[2].caption("Use the text areas above to copy individual fields.")


# ----------------------------------------------------------------------
