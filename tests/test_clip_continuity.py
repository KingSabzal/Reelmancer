"""Tests that the finished video never shows the black background.

Clips are composited onto a black canvas, so any moment of the timeline that no
clip covers appears as a black flash at a cut. These tests assert that every
interval leaves the merge owning a clip.
"""

from __future__ import annotations

import pytest

from utility.content.video_search_query_generator import (
    merge_empty_intervals as merge_queries,
)
from utility.media.media_manager import (
    RELEVANCE_DURATION_WEIGHT,
    merge_empty_intervals as merge_media,
)

MERGERS = (merge_queries, merge_media)


def coverage(merged):
    """Seconds of the timeline that actually have a clip."""
    return sum(end - start for (start, end), url in merged if url is not None)


def span(segments):
    """Total length of the timeline described by the segments."""
    return segments[-1][0][1] - segments[0][0][0]


@pytest.mark.parametrize("merge", MERGERS)
@pytest.mark.parametrize(
    ("name", "segments"),
    [
        ("gap in the middle", [[[0, 2], "a"], [[2, 4], None], [[4, 6], "b"]]),
        ("gap at the start", [[[0, 2], None], [[2, 4], "a"], [[4, 6], "b"]]),
        ("gap at the end", [[[0, 2], "a"], [[2, 4], "b"], [[4, 6], None]]),
        ("two gaps at the start", [[[0, 2], None], [[2, 4], None], [[4, 6], "a"]]),
        ("gaps at both ends", [[[0, 2], None], [[2, 4], "a"], [[4, 6], None]]),
        ("long run of gaps", [[[0, 2], "a"], [[2, 4], None], [[4, 6], None],
                              [[6, 8], None], [[8, 10], "b"]]),
        ("only the last has a clip", [[[0, 2], None], [[2, 4], None], [[4, 6], "a"]]),
    ],
)
def test_no_interval_is_left_without_a_clip(merge, name, segments):
    merged = merge([list(item) for item in segments])
    gaps = [interval for interval, url in merged if url is None]
    assert not gaps, f"{name}: these intervals would render black: {gaps}"


@pytest.mark.parametrize("merge", MERGERS)
@pytest.mark.parametrize(
    "segments",
    [
        [[[0, 2], "a"], [[2, 4], None], [[4, 6], "b"]],
        [[[0, 2], None], [[2, 4], "a"], [[4, 6], "b"]],
        [[[0, 2], None], [[2, 4], None], [[4, 6], "a"]],
        [[[0, 2], None], [[2, 4], "a"], [[4, 6], None]],
    ],
)
def test_the_whole_timeline_stays_covered(merge, segments):
    """A gap must be absorbed, never dropped: total duration cannot shrink."""
    merged = merge([list(item) for item in segments])
    assert coverage(merged) == pytest.approx(span(segments))


@pytest.mark.parametrize("merge", MERGERS)
def test_a_leading_gap_borrows_the_first_available_clip(merge):
    merged = merge([[[0, 2], None], [[2, 4], "first.mp4"]])
    assert merged[0][1] == "first.mp4"


@pytest.mark.parametrize("merge", MERGERS)
def test_all_empty_is_reported_rather_than_faked(merge):
    """With nothing to borrow there is no honest fix; the caller must handle it."""
    merged = merge([[[0, 2], None], [[2, 4], None]])
    assert all(url is None for _interval, url in merged)


@pytest.mark.parametrize("merge", MERGERS)
def test_none_input_is_passed_through(merge):
    assert merge(None) is None


@pytest.mark.parametrize("merge", MERGERS)
def test_a_full_timeline_is_left_alone(merge):
    segments = [[[0, 2], "a"], [[2, 4], "b"], [[4, 6], "c"]]
    assert merge([list(item) for item in segments]) == segments


def test_both_copies_of_the_merge_agree():
    """The helper is duplicated; the copies must not drift apart."""
    cases = [
        [[[0, 2], "a"], [[2, 4], None], [[4, 6], "b"]],
        [[[0, 2], None], [[2, 4], None], [[4, 6], "a"]],
        [[[0, 2], "a"], [[2, 4], None]],
    ]
    for segments in cases:
        assert merge_queries([list(i) for i in segments]) == merge_media(
            [list(i) for i in segments]
        )


# ----------------------------------------------------------------------
# Clip joins
# ----------------------------------------------------------------------
def test_clips_overlap_at_the_join():
    """A one-frame rounding gap at a cut would expose the black background."""
    from utility.video.video_pipeline import JOIN_OVERLAP_SECONDS

    assert JOIN_OVERLAP_SECONDS > 0
    # At least one frame at 30 fps, but small enough to stay invisible.
    assert 1 / 30 <= JOIN_OVERLAP_SECONDS <= 0.2


# ----------------------------------------------------------------------
# Relevance ranking
# ----------------------------------------------------------------------
def _rank(videos):
    """Mirror of the ranking used for Pexels and Pixabay results."""
    scored = []
    for position, video in enumerate(videos):
        penalty = min(abs(15 - video["duration"]), 30) / 30.0
        scored.append((position + penalty * RELEVANCE_DURATION_WEIGHT, video["id"]))
    return [identifier for _score, identifier in sorted(scored)]


def test_relevance_beats_a_convenient_duration():
    """The old sort picked the 14s clip purely because 14 is close to 15."""
    order = _rank([
        {"id": "relevant_30s", "duration": 30},
        {"id": "b", "duration": 25},
        {"id": "c", "duration": 22},
        {"id": "irrelevant_14s", "duration": 14},
    ])
    assert order[0] == "relevant_30s"


def test_duration_still_breaks_ties_between_close_results():
    order = _rank([
        {"id": "top_but_60s", "duration": 60},
        {"id": "second_16s", "duration": 16},
    ])
    assert order[0] == "second_16s"


def test_duration_cannot_override_relevance_outright():
    """A perfect duration must not drag a clip up from far down the list."""
    videos = [{"id": "top", "duration": 40}] + [
        {"id": f"filler{i}", "duration": 40} for i in range(1, 6)
    ] + [{"id": "far_down_15s", "duration": 15}]
    assert _rank(videos)[0] == "top"
