"""Tests that runtime paths resolve to the project root and land in the right folder.

Two rules are enforced here:

* nothing may be written inside a code package;
* scratch data goes to ``assets/`` and finished videos go to ``output/``.
"""

from __future__ import annotations

import os

from utility.core import paths

CODE_PACKAGES = ("ui", "utility", "tests")


def test_project_root_holds_a_root_marker() -> None:
    assert any(
        os.path.exists(os.path.join(paths.PROJECT_ROOT, marker))
        for marker in paths.ROOT_MARKERS
    )


def test_project_root_is_not_a_code_package() -> None:
    assert os.path.basename(paths.PROJECT_ROOT) not in CODE_PACKAGES


def test_every_runtime_path_lives_under_the_root() -> None:
    for value in (
        paths.CONFIG_FILE,
        paths.ASSETS_DIR,
        paths.FONT_CACHE_DIR,
        paths.API_CACHE_DIR,
        paths.MUSIC_DIR,
        paths.WORK_DIR,
        paths.OUTPUT_DIR,
        paths.GALLERY_VIDEOS,
        paths.GALLERY_THUMBS,
        paths.GALLERY_PACKAGES,
        paths.GALLERY_METADATA,
        paths.TREND_CACHE_FILE,
        paths.TREND_HISTORY_FILE,
    ):
        assert value.startswith(paths.PROJECT_ROOT)


def test_nothing_is_written_inside_a_code_package() -> None:
    """A path bug once put config.json inside core/. Never again."""
    writable = (
        paths.CONFIG_FILE, paths.ASSETS_DIR, paths.FONT_CACHE_DIR,
        paths.API_CACHE_DIR, paths.WORK_DIR, paths.OUTPUT_DIR,
        paths.GALLERY_VIDEOS, paths.TREND_CACHE_FILE,
    )
    for value in writable:
        relative = os.path.relpath(value, paths.PROJECT_ROOT)
        first = relative.split(os.sep)[0]
        assert first not in CODE_PACKAGES, f"{relative} would be written inside {first}/"


def test_scratch_goes_to_assets_and_videos_go_to_output() -> None:
    for value in (paths.FONT_CACHE_DIR, paths.API_CACHE_DIR,
                  paths.WORK_DIR, paths.MUSIC_DIR, paths.TREND_CACHE_FILE):
        assert value.startswith(paths.ASSETS_DIR), f"{value} should live in assets/"

    for value in (paths.GALLERY_VIDEOS, paths.GALLERY_THUMBS,
                  paths.GALLERY_PACKAGES, paths.GALLERY_METADATA):
        assert value.startswith(paths.OUTPUT_DIR), f"{value} should live in output/"


def test_assets_and_output_are_separate() -> None:
    assert not paths.OUTPUT_DIR.startswith(paths.ASSETS_DIR)
    assert not paths.ASSETS_DIR.startswith(paths.OUTPUT_DIR)


def test_ensure_runtime_dirs_creates_everything(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(paths, "ASSETS_DIR", str(tmp_path / "assets"))
    monkeypatch.setattr(paths, "FONT_CACHE_DIR", str(tmp_path / "assets" / "fonts"))
    monkeypatch.setattr(paths, "API_CACHE_DIR", str(tmp_path / "assets" / "api_cache"))
    monkeypatch.setattr(paths, "MUSIC_DIR", str(tmp_path / "assets" / "music"))
    monkeypatch.setattr(paths, "WORK_DIR", str(tmp_path / "assets" / "temp"))
    monkeypatch.setattr(paths, "OUTPUT_DIR", str(tmp_path / "output"))
    monkeypatch.setattr(paths, "GALLERY_VIDEOS", str(tmp_path / "output" / "videos"))
    monkeypatch.setattr(paths, "GALLERY_THUMBS", str(tmp_path / "output" / "thumbnails"))
    monkeypatch.setattr(paths, "GALLERY_PACKAGES", str(tmp_path / "output" / "packages"))

    paths.ensure_runtime_dirs()

    for directory in ("assets/fonts", "assets/api_cache", "assets/music",
                      "assets/temp", "output/videos", "output/thumbnails",
                      "output/packages"):
        assert (tmp_path / directory).is_dir(), f"{directory} was not created"
