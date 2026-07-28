"""Application shell: page setup, global warnings and the tab bar.

Each tab is rendered by its own module under ``reelmancer.ui.tabs``; this file
only wires them together.
"""

from __future__ import annotations

import streamlit as st

from utility.core import compat  # noqa: F401  (restores Pillow constants for MoviePy)
from utility.llm.llm_providers import get_provider, missing_fields
from ui.state import CONFIG
from ui.tabs.create import render_create_tab
from ui.tabs.gallery import render_gallery_tab
from ui.tabs.settings import render_settings_tab
from ui.tabs.trends import render_discover_tab
from ui.tabs.url import render_url_tab
from ui.ui_helpers import inject_dropdown_css

PAGE_TITLE = "Reelmancer"
PAGE_ICON = "\U0001F3AC"


def configure_page() -> None:
    """Apply the Streamlit page configuration.

    Must run before any other Streamlit call, so it is invoked from the
    launcher rather than from ``main()``.
    """
    st.set_page_config(page_title=PAGE_TITLE, page_icon=PAGE_ICON, layout="wide")


def render_startup_warnings() -> None:
    """Warn about missing credentials before the user tries to generate anything."""
    provider_gaps = missing_fields(CONFIG.llm_provider(), CONFIG)
    if provider_gaps:
        st.warning(
            f"The selected LLM provider ({get_provider(CONFIG.llm_provider())['name']}) is missing: "
            f"{', '.join(provider_gaps)}. Add it in Settings, or switch provider there."
        )

    if not CONFIG.pexels_key() and not CONFIG.pixabay_key():
        st.warning(
            "No Pexels or Pixabay key yet. The free keyless sources (Mixkit, Coverr, "
            "SplitShire, Internet Archive) still work, but adding a key in Settings gives "
            "far better footage matching. Use 'Test all connections' there to verify."
        )


def main() -> None:
    """Draw the whole interface."""
    inject_dropdown_css()
    st.title(f"{PAGE_ICON} {PAGE_TITLE}")
    st.caption(
        "100% free sources, zero attribution, no AI video generation, no color grading. "
        "Built to YouTube 2026 algorithm standards."
    )

    render_startup_warnings()

    trends_tab, url_tab, create_tab, gallery_tab, settings_tab = st.tabs(
        ["\U0001F525 Discover Trends", "\U0001F517 From URL", "\U0001F3AC Create Video",
         "\U0001F4C1 Gallery", "\u2699\uFE0F Settings"]
    )
    with trends_tab:
        render_discover_tab()
    with url_tab:
        render_url_tab()
    with create_tab:
        render_create_tab()
    with gallery_tab:
        render_gallery_tab()
    with settings_tab:
        render_settings_tab()
