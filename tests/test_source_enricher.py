"""Tests for thin-source handling.

The behaviour being protected: a short page must not silently produce a long
video, and topping it up must not let the video drift onto a different subject.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

import pytest

from utility.articles.article_analyzer import assess_sufficiency
from utility.articles.source_enricher import (
    count_mentions,
    enrich,
    rank_links,
    relevant_sentences,
    subject_terms,
)


@dataclass
class FakeArticle:
    """Stand-in for an extracted Article, so tests need no network."""

    url: str
    title: str
    text: str
    supporting_text: str = ""
    supporting_sources: List[dict] = field(default_factory=list)

    @property
    def word_count(self) -> int:
        return len(self.text.split())


# ----------------------------------------------------------------------
# Subject matching
# ----------------------------------------------------------------------
def test_subject_terms_strip_the_site_suffix() -> None:
    terms = subject_terms("Dad Shah - Wikipedia")
    assert "dad shah" in terms
    assert not any("wikipedia" in term for term in terms)


def test_subject_terms_cover_transliteration_variants() -> None:
    """'Dad Shah' and 'Daad Shah' are the same person on different pages."""
    terms = subject_terms("Dad Shah - Wikipedia")
    assert "dadshah" in terms
    assert "daad shah" in terms


def test_count_mentions_is_case_insensitive() -> None:
    terms = subject_terms("Dad Shah - Wikipedia")
    assert count_mentions("DAD SHAH was a farmer.", terms) >= 1
    assert count_mentions("A page about photosynthesis.", terms) == 0


def test_rank_links_puts_matching_titles_first() -> None:
    terms = subject_terms("Dad Shah - Wikipedia")
    links = [("Iran", "u1"), ("Dadshah", "u2"), ("Farmer", "u3")]
    assert rank_links(links, terms)[0][0] == "Dadshah"


# ----------------------------------------------------------------------
# Sentence selection
# ----------------------------------------------------------------------
def test_relevant_sentences_follow_pronouns() -> None:
    """Prose stops repeating a name; dropping those sentences loses real facts."""
    terms = subject_terms("Dad Shah")
    text = (
        "Dad Shah was a farmer in Nillag village. "
        "He hated the administration and took up arms. "
        "Photosynthesis converts light into chemical energy."
    )
    picked = " ".join(relevant_sentences(text, terms))
    assert "took up arms" in picked
    assert "Photosynthesis" not in picked


def test_relevant_sentences_drop_citation_lines() -> None:
    terms = subject_terms("Dad Shah")
    text = (
        "Dad Shah led a rebellion in Balochistan. "
        "Archived from the original on 29 September 2007. "
        "Retrieved 2025-04-05."
    )
    picked = relevant_sentences(text, terms)
    assert any("rebellion" in s for s in picked)
    assert not any("Archived" in s or "Retrieved" in s for s in picked)


# ----------------------------------------------------------------------
# Enrichment gate
# ----------------------------------------------------------------------
def test_enrich_rejects_pages_that_never_name_the_subject(monkeypatch) -> None:
    """The core guard: a broad background page must not be pulled in."""
    import utility.articles.source_enricher as enricher

    article = FakeArticle(
        url="https://en.wikipedia.org/wiki/Dad_Shah",
        title="Dad Shah - Wikipedia",
        text="Mir Dad Shah was an Iranian Baloch farmer.",
    )
    monkeypatch.setattr(
        enricher, "wikipedia_links",
        lambda url, session=None, limit=40: [
            ("Mohammad Reza Pahlavi", "https://en.wikipedia.org/wiki/Mohammad_Reza_Pahlavi"),
        ],
    )

    def fake_extract(url: str) -> FakeArticle:
        # A long page about someone else that never names our subject.
        return FakeArticle(url=url, title="Mohammad Reza Pahlavi",
                           text="The Shah ruled Iran. " * 40)

    result = enrich(article, fake_extract)
    assert not result.used
    assert result.rejected_irrelevant == 1


def test_enrich_accepts_a_page_that_names_the_subject(monkeypatch) -> None:
    import utility.articles.source_enricher as enricher

    article = FakeArticle(
        url="https://en.wikipedia.org/wiki/Dad_Shah",
        title="Dad Shah - Wikipedia",
        text="Mir Dad Shah was an Iranian Baloch farmer.",
    )
    monkeypatch.setattr(
        enricher, "wikipedia_links",
        lambda url, session=None, limit=40: [
            ("Dadshah", "https://en.wikipedia.org/wiki/Dadshah"),
        ],
    )

    def fake_extract(url: str) -> FakeArticle:
        return FakeArticle(
            url=url, title="Dadshah",
            text=(
                "Daadshah is a 1984 Iranian film about the Baloch rebel Dad Shah. "
                "Dad Shah's wife Bibi Hatun fought alongside him. "
                "In 1957 Dad Shah was killed in a gun battle."
            ),
        )

    result = enrich(article, fake_extract)
    assert result.used
    assert result.sources[0].mentions >= 3
    assert "Bibi Hatun" in result.sources[0].text


def test_enrich_rejects_oversized_pages(monkeypatch) -> None:
    """A huge survey that mentions the subject once is still about something else."""
    import utility.articles.source_enricher as enricher

    article = FakeArticle(url="https://en.wikipedia.org/wiki/Dad_Shah",
                          title="Dad Shah - Wikipedia", text="Mir Dad Shah was a farmer.")
    monkeypatch.setattr(
        enricher, "wikipedia_links",
        lambda url, session=None, limit=40: [("Balochistan", "https://x/wiki/Balochistan")],
    )
    monkeypatch.setattr(
        enricher, "extract_stub", None, raising=False,
    )

    def fake_extract(url: str) -> FakeArticle:
        return FakeArticle(url=url, title="Balochistan",
                           text="Dad Shah. " + ("region history " * 5000))

    result = enrich(article, fake_extract)
    assert not result.used
    assert result.rejected_too_large == 1


def test_enrich_respects_the_accept_limit(monkeypatch) -> None:
    import utility.articles.source_enricher as enricher

    article = FakeArticle(url="https://en.wikipedia.org/wiki/Subject_Name",
                          title="Subject Name", text="Subject Name did things.")
    monkeypatch.setattr(
        enricher, "wikipedia_links",
        lambda url, session=None, limit=40: [(f"P{i}", f"https://x/wiki/P{i}") for i in range(10)],
    )

    def fake_extract(url: str) -> FakeArticle:
        return FakeArticle(url=url, title=url[-2:],
                           text="Subject Name appears here and did something notable.")

    result = enrich(article, fake_extract, max_accept=2)
    assert len(result.sources) == 2


def test_enrich_survives_a_broken_link(monkeypatch) -> None:
    import utility.articles.source_enricher as enricher

    article = FakeArticle(url="https://en.wikipedia.org/wiki/Subject_Name",
                          title="Subject Name", text="Subject Name did things.")
    monkeypatch.setattr(
        enricher, "wikipedia_links",
        lambda url, session=None, limit=40: [("Dead", "https://x/wiki/Dead")],
    )

    def fake_extract(url: str):
        raise RuntimeError("404")

    result = enrich(article, fake_extract)
    assert not result.used
    assert result.failed == 1


# ----------------------------------------------------------------------
# Duration sufficiency
# ----------------------------------------------------------------------
def test_thin_source_is_flagged_and_capped() -> None:
    """102 usable words cannot honestly fill 60 seconds."""
    max_duration, coverage, status, note = assess_sufficiency(102, 60)
    assert status in {"thin", "insufficient"}
    assert max_duration < 60
    assert coverage < 1.0
    assert note


def test_rich_source_is_not_flagged() -> None:
    max_duration, coverage, status, note = assess_sufficiency(5000, 300)
    assert status == "ok"
    assert note == ""
    assert coverage == pytest.approx(1.0)


def test_sufficiency_never_recommends_an_unusable_duration() -> None:
    max_duration, _coverage, _status, _note = assess_sufficiency(5, 60)
    assert max_duration >= 15
