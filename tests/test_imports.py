"""Import the whole package.

A broken import inside a rarely used module would otherwise only surface when a
user clicks the tab that needs it, half way through a render.
"""

from __future__ import annotations

import importlib
import pkgutil

PACKAGES = ("ui", "utility")


def test_every_module_imports() -> None:
    failures = []
    for name in PACKAGES:
        package = importlib.import_module(name)
        for module in pkgutil.walk_packages(package.__path__, f"{name}."):
            try:
                importlib.import_module(module.name)
            except Exception as exc:  # noqa: BLE001 - report all of them at once
                failures.append(f"{module.name}: {type(exc).__name__}: {exc}")
    assert not failures, "Modules failed to import:\n" + "\n".join(failures)


def test_ui_tabs_expose_their_render_function() -> None:
    expected = {
        "ui.tabs.create": "render_create_tab",
        "ui.tabs.gallery": "render_gallery_tab",
        "ui.tabs.settings": "render_settings_tab",
        "ui.tabs.trends": "render_discover_tab",
        "ui.tabs.url": "render_url_tab",
    }
    for module_name, function_name in expected.items():
        module = importlib.import_module(module_name)
        assert callable(getattr(module, function_name)), f"{module_name}.{function_name}"
