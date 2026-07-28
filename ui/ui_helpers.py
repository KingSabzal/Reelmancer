"""Shared Streamlit UI helpers.

The main purpose is fixing long dropdowns. Streamlit renders its select boxes with a
virtualized listbox whose max-height shows only about 7-8 rows, which makes a flat list
of 122 video styles or 114 caption styles very hard to browse. Two things fix that:

1. inject_dropdown_css() raises the popover height so roughly 18 rows are visible and
   the list scrolls smoothly;
2. grouped_select() adds a category filter and a live text search above the select box,
   so the user narrows 122 options down to a handful before opening it.
"""

from __future__ import annotations

from typing import Callable, Dict, List, Optional, Sequence

import streamlit as st

DROPDOWN_CSS = """
<style>
/* Streamlit renders the option list inside a virtualized container whose inner
   div[role="listbox"] carries a hardcoded max-height of 300px (about 7-8 rows).
   Both the outer virtual dropdown and that inner listbox must be raised, otherwise
   the inner one keeps clipping the list. */
div[data-testid="stSelectboxVirtualDropdown"],
div[data-testid="stVirtualDropdown"],
div[data-baseweb="popover"] [class*="MenuList"] {
    max-height: 65vh !important;
}
div[data-testid="stSelectboxVirtualDropdown"] > div,
div[data-testid="stVirtualDropdown"] > div {
    max-height: 65vh !important;
}
/* The inner scrolling listbox: this is the element that actually clipped to 300px. */
div[data-baseweb="popover"] div[role="listbox"],
div[data-baseweb="popover"] ul[role="listbox"],
div[data-testid="stSelectboxVirtualDropdown"] div[role="listbox"] {
    max-height: 62vh !important;
    overflow-y: auto !important;
}
/* Its virtualized child sets an inline height as well. */
div[data-baseweb="popover"] div[role="listbox"] > div {
    max-height: none !important;
}
/* Compact rows so more options fit on screen. */
div[data-baseweb="popover"] li[role="option"],
div[data-baseweb="popover"] div[role="option"] {
    padding-top: 6px !important;
    padding-bottom: 6px !important;
    min-height: 32px !important;
}
/* Multiselect popovers get the same treatment. */
div[data-baseweb="popover"] div[data-baseweb="menu"],
div[data-baseweb="popover"] div[data-baseweb="menu"] > ul {
    max-height: 62vh !important;
}
</style>
"""


def inject_dropdown_css() -> None:
    """Inject the CSS that makes long dropdowns scrollable and much taller."""
    if not st.session_state.get("_dropdown_css_done"):
        st.markdown(DROPDOWN_CSS, unsafe_allow_html=True)
        st.session_state["_dropdown_css_done"] = True


def grouped_select(
    label: str,
    options: Sequence[str],
    *,
    groups: Optional[Dict[str, List[str]]] = None,
    group_label: str = "Category",
    default: Optional[str] = None,
    key: str,
    help_text: Optional[str] = None,
    format_func: Optional[Callable[[str], str]] = None,
    search_placeholder: str = "Type and press Enter...",
) -> str:
    """Render a searchable, optionally grouped select box for very long option lists.

    Returns the selected option. The current value is always kept in the list, even
    when the filter would exclude it, so a selection is never silently lost.
    """
    inject_dropdown_css()
    options = list(options)
    if not options:
        st.warning(f"No options available for {label}.")
        return ""

    current = st.session_state.get(f"{key}__value") or default or options[0]
    if current not in options:
        current = options[0]

    filter_columns = st.columns([1, 1]) if groups else [st]
    chosen_group = "All"
    if groups:
        group_names = ["All"] + sorted(groups.keys())
        stored_group = st.session_state.get(f"{key}__group", "All")
        if stored_group not in group_names:
            stored_group = "All"
        chosen_group = filter_columns[0].selectbox(
            group_label, group_names, index=group_names.index(stored_group), key=f"{key}__group"
        )
        search_host = filter_columns[1]
    else:
        search_host = filter_columns[0]

    query = search_host.text_input(
        f"Search {label.lower()}",
        value="",
        key=f"{key}__query",
        placeholder=search_placeholder,
        help="Type a word and press Enter to filter the list.",
    ).strip().lower()

    pool = list(options)
    if groups and chosen_group != "All":
        pool = [option for option in options if option in set(groups.get(chosen_group, []))]
    if query:
        pool = [option for option in pool if query in option.lower()]
    if not pool:
        st.caption(f"No {label.lower()} matches '{query}'. Showing every option instead.")
        pool = list(options)
    if current not in pool:
        pool = [current] + pool

    index = pool.index(current)
    selection = st.selectbox(
        f"{label} ({len(pool)} shown of {len(options)})",
        pool,
        index=index,
        key=f"{key}__select",
        help=help_text,
        format_func=format_func or (lambda value: value),
    )
    st.session_state[f"{key}__value"] = selection
    return selection


def set_select_value(key: str, value: str) -> None:
    """Preset the value of a grouped_select from outside (used by trend auto-apply)."""
    st.session_state[f"{key}__value"] = value
    st.session_state.pop(f"{key}__select", None)
    st.session_state.pop(f"{key}__query", None)


def status_pill(icon: str, label: str, color: str) -> str:
    """Return HTML for a small colored status pill."""
    return (
        f"<span style='background:{color};color:#fff;padding:3px 10px;border-radius:12px;"
        f"font-size:0.78rem;font-weight:600;white-space:nowrap'>{icon} {label}</span>"
    )
