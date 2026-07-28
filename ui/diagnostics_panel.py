"""Streamlit rendering for the connection diagnostics dashboard.

The checks themselves live in ``reelmancer.diagnostics`` and have no UI
dependency; this module only draws their results.
"""

from __future__ import annotations

import streamlit as st

from utility.core.api_cache import get_cache
from utility.diagnostics.connection_status import CATEGORY_LABELS, ConnectionStatusChecker
from ui.state import CONFIG


def status_pill(icon: str, label: str, color: str) -> str:
    """Return HTML for a small colored status pill."""
    return (
        f"<span style='background:{color};color:#fff;padding:3px 10px;border-radius:12px;"
        f"font-size:0.78rem;font-weight:600;white-space:nowrap'>{icon} {label}</span>"
    )


def render_connection_status(compact: bool = False) -> None:
    """Show which API keys and sites are actually connected and working."""
    st.markdown("### \U0001F50C Connection Status")
    st.caption(
        "Every check below performs a real request, so you can see exactly which keys "
        "are valid and which sources are reachable right now."
    )

    columns = st.columns([1, 1, 2])
    run = columns[0].button("Test all connections", type="primary", use_container_width=True)
    deep = columns[1].toggle("Include slow checks", value=True,
                             help="Also verifies the EdgeTTS voice catalogue and the caption model cache.")
    if run:
        with st.spinner("Testing every key, API and source..."):
            st.session_state["connection_report"] = ConnectionStatusChecker(CONFIG).run_all(
                include_slow=deep
            )

    report = st.session_state.get("connection_report")
    if not report:
        st.info("Press 'Test all connections' to check your keys and every media and trend source.")
        return

    ready = report["ready"]
    counts = report["counts"]
    st.caption(f"Last checked: {report['checked_at']}")

    ready_cols = st.columns(4)
    for column, (label, key, hint) in zip(
        ready_cols,
        [
            ("Footage", "footage", "At least one video source is working"),
            ("Text (LLM)", "llm", "At least one LLM provider is connected"),
            ("Voice", "voice", "EdgeTTS is reachable"),
            ("Rendering", "render", "FFmpeg is available"),
        ],
    ):
        column.metric(label, "Ready" if ready.get(key) else "Not ready", help=hint)

    summary = " ".join(
        f"{status}: {count}" for status, count in sorted(counts.items(), key=lambda i: -i[1])
    )
    st.caption(f"{report['total']} checks - {summary}")

    if not ready.get("llm"):
        st.error(
            "No LLM provider is connected, so scripts, SEO packages and viral titles cannot "
            "be generated. Add a 9Router, OpenRouter or NVIDIA NIM key below."
        )
    if not ready.get("footage"):
        st.error("No working video source. Add a Pexels or Pixabay key below.")

    order = ["media_api", "llm", "engine", "media_free", "trend"]
    for category in order:
        items = report["groups"].get(category)
        if not items:
            continue
        healthy = sum(1 for i in items if i["status"] == "ok")
        with st.expander(f"{CATEGORY_LABELS.get(category, category)} - {healthy}/{len(items)} working",
                         expanded=(category in ("media_api", "llm") and not compact)):
            for item in sorted(items, key=lambda i: (i["status"] != "ok", i["service"])):
                left, right = st.columns([3, 5])
                latency = f"{item['latency_ms']} ms" if item.get("latency_ms") is not None else ""
                left.markdown(
                    status_pill(item["icon"], item["status"].upper(), item["color"])
                    + f" **{item['service']}**",
                    unsafe_allow_html=True,
                )
                right.caption(f"{item['message']} {latency}")
                if item.get("details"):
                    with right.popover("Details"):
                        st.json(item["details"])

    limits = report.get("rate_limits") or {}
    if limits:
        st.markdown("**Rate limits observed**")
        for service, state in limits.items():
            remaining = state.get("remaining")
            limit = state.get("limit")
            if remaining is None or not limit:
                continue
            st.progress(
                max(0.0, min(remaining / limit, 1.0)),
                text=f"{service}: {remaining}/{limit} requests left "
                     f"(resets in {state.get('reset_seconds', '?')} s)",
            )

    cache_stats = get_cache().stats()
    st.caption(
        f"API response cache: {cache_stats['fresh_entries']} fresh of {cache_stats['entries']} "
        f"entries, {cache_stats['size_kb']} KB, {cache_stats['ttl_hours']} h TTL "
        "(Pixabay's terms require 24 hour caching)."
    )


# ----------------------------------------------------------------------
