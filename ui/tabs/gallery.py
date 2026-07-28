"""Gallery tab: browse, filter, play, download and delete finished videos."""

from __future__ import annotations

import os
from datetime import date

import streamlit as st

from utility.content.video_styles import list_styles
from ui.state import gallery, style_groups
from ui.ui_helpers import grouped_select


def render_gallery_tab() -> None:
    """Render the gallery with statistics, filters and per-video actions."""
    st.markdown("## \U0001F4C1 Gallery")
    manager = gallery()
    stats = manager.statistics()

    metrics = st.columns(4)
    metrics[0].metric("Total videos", stats["total_videos"])
    metrics[1].metric("Total duration", f"{stats['total_duration_seconds'] / 60:.1f} min")
    metrics[2].metric("Total size", f"{stats['total_size_mb']:.1f} MB")
    metrics[3].metric("Styles used", stats["style_count"])

    with st.expander("Style distribution"):
        st.json(stats["style_distribution"])

    filters = st.columns(4)
    query = filters[0].text_input("Search title, topic or tags")
    with filters[1]:
        style_filter = grouped_select(
            "Style", ["All"] + list_styles(), groups=style_groups(),
            group_label="Category", default="All", key="gallery_style_filter",
        )
    aspect_filter = filters[2].selectbox("Aspect ratio", ["All", "9:16", "16:9"])
    sort_by = filters[3].selectbox("Sort by", ["date", "duration", "size", "title"])

    date_cols = st.columns(2)
    use_dates = date_cols[0].toggle("Filter by date range")
    date_from = date_to = None
    if use_dates:
        start, end = date_cols[1].date_input(
            "Range", value=(date.today(), date.today()), key="gallery_dates"
        ) or (None, None)
        date_from = start.isoformat() if start else None
        date_to = end.isoformat() if end else None

    videos = manager.search(
        query=query,
        style="" if style_filter == "All" else style_filter,
        aspect_ratio="" if aspect_filter == "All" else aspect_filter,
        date_from=date_from,
        date_to=date_to,
        sort_by=sort_by,
    )

    if not videos:
        st.info("No videos yet. Create one in the Create Video tab.")
        return

    columns = st.columns(3)
    for index, video in enumerate(videos):
        with columns[index % 3]:
            with st.container(border=True):
                if video.get("thumbnail_path") and os.path.exists(video["thumbnail_path"]):
                    st.image(video["thumbnail_path"], use_container_width=True)
                st.markdown(f"**{video['title'][:70]}**")
                st.caption(
                    f"{video['style']} | {video['duration_seconds']:.0f}s | "
                    f"{video['aspect_ratio']} | {video['file_size_mb']:.1f} MB"
                )
                st.caption(f"Created: {video['created_at']} | Status: {video['status']}")

                path = video.get("file_path", "")
                if path and os.path.exists(path):
                    if st.toggle("Play", key=f"play_{video['video_id']}"):
                        st.video(path)
                    with open(path, "rb") as handle:
                        st.download_button(
                            "Download", handle.read(),
                            file_name=os.path.basename(path),
                            key=f"dl_{video['video_id']}", use_container_width=True,
                        )
                packages = manager.load_packages(video["video_id"])
                if packages and st.toggle("View SEO packages", key=f"seo_{video['video_id']}"):
                    st.json(packages)
                if st.button("Delete", key=f"del_{video['video_id']}", use_container_width=True):
                    manager.delete(video["video_id"])
                    st.rerun()
