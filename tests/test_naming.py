"""Tests for output file naming.

These cover the cases that would otherwise only show up as a crash or an
overwritten video half way through a render.
"""

from __future__ import annotations

import os

import pytest

from utility.core.naming import MAX_SLUG_LENGTH, output_stem, slugify_title, unique_path


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Why Cats Purr: 5 Reasons You Never Knew!", "Why-Cats-Purr-5-Reasons-You-Never-Knew"),
        ("\U0001F525 10 Insane Facts \u2014 #3 Broke My Brain", "10-Insane-Facts-3-Broke-My-Brain"),
        ("Caf\u00e9 Culture & the Na\u00efve Traveller", "Cafe-Culture-the-Naive-Traveller"),
        ("  multiple   spaces\tand\nnewlines  ", "multiple-spaces-and-newlines"),
        ("ALL CAPS TITLE", "ALL-CAPS-TITLE"),
    ],
)
def test_slugify_common_titles(title: str, expected: str) -> None:
    assert slugify_title(title) == expected


def test_slugify_empty_and_punctuation_only_fall_back() -> None:
    assert slugify_title("") == "video"
    assert slugify_title("!!!???") == "video"
    assert slugify_title("   ") == "video"


def test_windows_reserved_names_are_made_safe() -> None:
    for reserved in ("CON", "PRN", "AUX", "NUL", "COM1", "LPT1"):
        assert slugify_title(reserved) != reserved


def test_long_titles_are_capped_and_not_cut_mid_word() -> None:
    title = "Supercalifragilistic " * 20
    slug = slugify_title(title)
    assert len(slug) <= MAX_SLUG_LENGTH
    # A clean cut means no truncated fragment at the end.
    assert all(part == "Supercalifragilistic" for part in slug.split("-"))


def test_unique_path_does_not_overwrite(tmp_path) -> None:
    first = unique_path(str(tmp_path), "Clip", ".mp4")
    open(first, "w").close()
    second = unique_path(str(tmp_path), "Clip", ".mp4")
    third_source = second
    open(second, "w").close()
    third = unique_path(str(tmp_path), "Clip", ".mp4")

    assert os.path.basename(first) == "Clip.mp4"
    assert os.path.basename(second) == "Clip-2.mp4"
    assert os.path.basename(third) == "Clip-3.mp4"
    assert first != third_source


def test_output_stem_fallback_order() -> None:
    assert output_stem("Real Title", "topic", "id-1") == "Real-Title"
    assert output_stem("", "my topic", "id-1") == "my-topic"
    assert output_stem("", "", "id-1") == "id-1"
    assert output_stem("", "", "") == "video"
