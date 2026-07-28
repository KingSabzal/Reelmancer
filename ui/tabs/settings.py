"""Settings tab: API keys, defaults and the live diagnostics panel."""

from __future__ import annotations

from typing import Any, Dict

import streamlit as st

from utility.core.api_cache import get_cache
from utility.diagnostics.connection_status import ConnectionStatusChecker, mask_key
from utility.llm.llm_providers import PROVIDERS, get_provider, missing_fields, provider_labels
from utility.llm.llm_router import reset_router
from utility.trends.trend_cache_manager import TrendCacheManager
from ui.diagnostics_panel import render_connection_status
from ui.state import CONFIG


def render_settings_tab() -> None:
    """Render the API key, watermark and cache settings."""
    st.markdown("## \u2699\uFE0F Settings")
    st.caption("Every setting is stored in config.json. There is no .env file.")

    render_connection_status()
    st.markdown("---")

    st.markdown("### LLM provider")
    st.caption(
        "Choose one provider. There is no fallback between providers: inside the provider "
        "you pick, every available model is tried automatically until one answers."
    )

    labels = provider_labels()
    provider_ids = list(labels.keys())
    current_provider = CONFIG.llm_provider()
    if current_provider not in provider_ids:
        current_provider = provider_ids[0]

    chosen = st.radio(
        "Active provider",
        provider_ids,
        index=provider_ids.index(current_provider),
        format_func=lambda pid: labels[pid],
        horizontal=True,
        key="provider_radio",
    )
    provider = get_provider(chosen)
    st.info(f"**{provider['name']}** - {provider['description']}")
    if provider.get("notes"):
        st.caption(provider["notes"])
    if provider.get("signup"):
        st.link_button(f"Get {provider['name']} credentials", provider["signup"])

    with st.form("provider_form"):
        values: Dict[str, Any] = {}
        for field_def in provider["fields"]:
            values[field_def["key"]] = st.text_input(
                field_def["label"] + ("" if field_def.get("required") else " (optional)"),
                value=CONFIG.get(field_def["key"], "") or "",
                type="password" if field_def["type"] == "password" else "default",
                placeholder=field_def.get("placeholder", ""),
                key=f"provider_field_{field_def['key']}",
            )
        save_col, test_col = st.columns(2)
        saved = save_col.form_submit_button("Save provider", type="primary")
        tested = test_col.form_submit_button("Save and test")

        if saved or tested:
            values["llm_provider"] = chosen
            CONFIG.update(values)
            reset_router()
            st.session_state.pop("provider_models", None)
            st.success(f"{provider['name']} saved as the active provider.")
            gaps = missing_fields(chosen, CONFIG)
            if gaps:
                st.warning(f"Still missing: {', '.join(gaps)}.")
            elif tested:
                with st.spinner(f"Discovering the models available on {provider['name']}..."):
                    result = ConnectionStatusChecker(CONFIG).check_selected_provider()
                line = f"{result.icon} **{result.service}** - {result.message}"
                if result.status == "ok":
                    st.success(line)
                    st.session_state["provider_models"] = result.details.get("models_preview", [])
                else:
                    st.error(line)

    if st.session_state.get("provider_models"):
        with st.expander("Models discovered for automatic fallback"):
            st.write(st.session_state["provider_models"])
            st.caption(
                "These are tried in order. A model that fails three times in a row is "
                "dropped for the rest of the session (circuit breaker)."
            )

    with st.expander("Credentials stored for the other providers"):
        for pid, cfg in PROVIDERS.items():
            if pid == chosen:
                continue
            stored = ", ".join(
                f"{f['label']}: {mask_key(CONFIG.get(f['key'], ''))}" for f in cfg["fields"]
            )
            st.caption(f"**{cfg['name']}** - {stored}")
        st.caption("Switching provider above keeps these saved, so you can swap back instantly.")

    st.markdown("### Media API keys (free)")
    st.caption(
        f"Currently stored - Pexels: `{mask_key(CONFIG.pexels_key())}` | "
        f"Pixabay: `{mask_key(CONFIG.pixabay_key())}`"
    )
    key_cols = st.columns(2)
    key_cols[0].link_button("Get a free Pexels key", "https://www.pexels.com/api/new/")
    key_cols[1].link_button("Get a free Pixabay key", "https://pixabay.com/api/key/")
    with st.form("media_form"):
        pexels = st.text_input("Pexels API key", value=CONFIG.pexels_key(), type="password")
        pixabay = st.text_input("Pixabay API key", value=CONFIG.pixabay_key(), type="password")
        save_media, test_media = st.columns(2)
        saved = save_media.form_submit_button("Save media keys", type="primary")
        tested = test_media.form_submit_button("Save and test")
        if saved or tested:
            CONFIG.update({"pexels_api_key": pexels, "pixabay_api_key": pixabay})
            st.success("Media keys saved to config.json.")
            if tested:
                checker = ConnectionStatusChecker(CONFIG)
                for check in (checker.check_pexels, checker.check_pixabay):
                    result = check()
                    line = f"{result.icon} **{result.service}** - {result.message}"
                    if result.status == "ok":
                        st.success(line)
                        if result.details.get("access_tier") == "standard":
                            st.caption(
                                "Standard Pixabay access: images are capped at 1280 px "
                                "(largeImageURL). Videos are still available up to 1920x1080, "
                                "which is what the renderer uses."
                            )
                    elif result.status == "missing":
                        st.info(line)
                    else:
                        st.error(line)

    st.markdown("### Advanced watermark defaults")
    with st.form("watermark_form"):
        watermark = dict(CONFIG.watermark())
        enabled = st.toggle("Enabled by default", value=watermark["enabled"])
        content = st.text_input("Default content", value=watermark["content"])
        opacity = st.slider("Default opacity", 0.10, 0.50, float(watermark["opacity"]), 0.01)
        font_ratio = st.slider("Font size (fraction of frame height)", 0.02, 0.06, float(watermark["font_size_ratio"]), 0.005)
        padding = st.slider("Safe zone padding", 0.02, 0.15, float(watermark["safe_zone_padding"]), 0.01)
        color = st.color_picker("Color", watermark["color"])
        stroke = st.color_picker("Stroke color", watermark["stroke_color"])
        if st.form_submit_button("Save watermark defaults", type="primary"):
            watermark.update(
                {
                    "enabled": enabled, "content": content, "opacity": opacity,
                    "font_size_ratio": font_ratio, "safe_zone_padding": padding,
                    "color": color, "stroke_color": stroke,
                }
            )
            CONFIG.update({"watermark": watermark})
            st.success("Watermark defaults saved.")

    st.markdown("### Rendering")
    with st.form("render_form"):
        fps = st.select_slider("FPS", [24, 25, 30, 60], value=int(CONFIG.get("fps", 30)))
        preset_options = ["ultrafast", "superfast", "veryfast", "faster", "fast", "medium"]
        preset = st.selectbox(
            "Encoder preset", preset_options,
            index=preset_options.index(str(CONFIG.get("video_preset", "veryfast"))),
        )
        interval = st.slider(
            "Pattern interrupt interval (seconds)", 3.0, 5.0,
            float(CONFIG.get("pattern_interrupt_interval", 4.0)), 0.5,
        )
        if st.form_submit_button("Save rendering settings", type="primary"):
            CONFIG.update({"fps": fps, "video_preset": preset, "pattern_interrupt_interval": interval})
            st.success("Rendering settings saved.")

    st.markdown("### Trend cache")
    cache = TrendCacheManager()
    st.caption(cache.last_updated_label())
    api_stats = get_cache().stats()
    st.caption(
        f"API response cache: {api_stats['entries']} entries ({api_stats['size_kb']} KB), "
        f"{api_stats['ttl_hours']} h TTL."
    )
    if st.button("Clear API response cache", use_container_width=True):
        removed = get_cache().clear()
        st.success(f"Removed {removed} cached API responses.")

    cache_cols = st.columns(3)
    if cache_cols[0].button("Clear trend cache", use_container_width=True):
        cache.clear_cache()
        st.success("Trend cache cleared.")
    if cache_cols[1].button("Clear suggestion history", use_container_width=True):
        cache.clear_history()
        st.success("Suggestion history cleared.")
    if cache_cols[2].toggle("View history"):
        st.json(cache.history())

    with st.form("cache_form"):
        ttl = st.slider("Trend cache TTL (minutes)", 10, 240, int(CONFIG.get("trend_cache_ttl_minutes", 60)), 10)
        history_size = st.slider("History size", 20, 300, int(CONFIG.get("trend_history_size", 100)), 10)
        threshold = st.slider("Gallery cleanup threshold", 50, 1000, int(CONFIG.get("gallery_cleanup_threshold", 500)), 50)
        autoclean = st.toggle("Enable gallery auto-cleanup", value=bool(CONFIG.get("gallery_autocleanup", True)))
        if st.form_submit_button("Save cache and gallery settings", type="primary"):
            CONFIG.update(
                {
                    "trend_cache_ttl_minutes": ttl,
                    "trend_history_size": history_size,
                    "gallery_cleanup_threshold": threshold,
                    "gallery_autocleanup": autoclean,
                }
            )
            st.success("Cache and gallery settings saved.")
