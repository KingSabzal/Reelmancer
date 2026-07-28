"""Top up a thin source page with material from the pages it links to.

A short page is the one case where writing a script from the source alone forces
the model to invent the difference. A 102 word stub asked to fill a 60 second
video (about 140 spoken words) has to make up most of the script, and for a
historical or political subject that means fabricated detail.

The fix is not to follow every link. Measured on a real stub
(en.wikipedia.org/wiki/Dad_Shah, 102 words, 17 outgoing links):

    Dadshah (film about him)      308 words   names the subject 4 times   useful
    Insurgency in Balochistan    8000 words   names the subject 0 times   drift
    Mohammad Reza Pahlavi        8000 words   names the subject 0 times   drift
    Baloch people                8000 words   names the subject 0 times   drift

Following links blindly would bury 102 relevant words under 24000 irrelevant
ones and quietly change what the video is about. So a linked page is only
accepted when it actually names the subject. That single test keeps the useful
page and rejects all three broad ones.

Enrichment only runs when the main page is too thin to support the video, is
capped at a few pages, and every borrowed sentence is marked with where it came
from so the script generator can treat it as supporting material.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Set, Tuple
from urllib.parse import unquote, urljoin, urlparse

LOGGER = logging.getLogger("source_enricher")
if not LOGGER.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("[%(name)s] %(message)s"))
    LOGGER.addHandler(_handler)
LOGGER.setLevel(logging.INFO)

# Enrichment is a repair for thin pages, so it never runs on a healthy one.
THIN_PAGE_WORDS = 250

# Hard ceiling on extra network work.
MAX_PAGES_TO_FETCH = 6
MAX_PAGES_TO_ACCEPT = 3

# A linked page must name the subject at least this many times to be accepted.
MIN_SUBJECT_MENTIONS = 1

# Ignore huge pages: a 8000 word survey that happens to mention the subject once
# is still mostly about something else.
MAX_LINKED_PAGE_WORDS = 4000

# Wikipedia namespaces and meta pages that never contain article prose.
_WIKI_NAMESPACE_PREFIXES = (
    "special:", "help:", "category:", "file:", "template:", "portal:",
    "wikipedia:", "talk:", "user:", "draft:", "module:", "mediawiki:",
)

# Link targets that are identifiers or navigation rather than subject matter.
_LINK_STOPWORDS = (
    "isbn", "issn", "doi", "pmid", "oclc", "identifier", "main page",
    "list of", "index of", "outline of", "portal", "disambiguation",
)

_WORD_RE = re.compile(r"[A-Za-z0-9']+")

# Sentences that continue talking about the subject without naming them.
_PRONOUN_START = re.compile(
    r"^\s*(he|she|they|his|her|their|him|the\s+\w+'s)\b", re.I
)

# Citation and archive furniture that survives extraction on some pages.
_REFERENCE_MARKERS = (
    "archived from the original", "retrieved ", "cite web", "permanent dead link",
    "archived copy", "cs1 maint", "issn", "isbn", "doi:",
)


@dataclass
class SupportingSource:
    """One accepted linked page and the sentences taken from it."""

    url: str
    title: str
    mentions: int
    word_count: int
    sentences: List[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        """The borrowed sentences as a single block."""
        return " ".join(self.sentences)


@dataclass
class EnrichmentResult:
    """What enrichment managed to add, and what it decided along the way."""

    sources: List[SupportingSource] = field(default_factory=list)
    considered: int = 0
    rejected_irrelevant: int = 0
    rejected_too_large: int = 0
    failed: int = 0

    @property
    def added_words(self) -> int:
        """Total words gained from accepted pages."""
        return sum(len(s.text.split()) for s in self.sources)

    @property
    def used(self) -> bool:
        """True when at least one page was accepted."""
        return bool(self.sources)

    def as_dict(self) -> dict:
        """Serializable summary for the UI."""
        return {
            "sources": [
                {"url": s.url, "title": s.title, "mentions": s.mentions,
                 "words_taken": len(s.text.split())}
                for s in self.sources
            ],
            "considered": self.considered,
            "rejected_irrelevant": self.rejected_irrelevant,
            "rejected_too_large": self.rejected_too_large,
            "failed": self.failed,
            "added_words": self.added_words,
        }


# ----------------------------------------------------------------------
# Subject detection
# ----------------------------------------------------------------------
def subject_terms(title: str, text: str = "") -> List[str]:
    """Return the phrases a related page must mention to count as relevant.

    The page title is the subject, minus the site suffix that most titles carry
    ("Dad Shah - Wikipedia"). Both the full name and its distinctive parts are
    returned, because a related page may use a variant spelling.
    """
    cleaned = re.split(r"\s+[-|\u2013\u2014]\s+", title or "")[0].strip()
    cleaned = re.sub(r"\s*\([^)]*\)\s*$", "", cleaned).strip()
    if not cleaned:
        return []

    terms = [cleaned.lower()]

    # Multi-word subjects: also accept the name written without spaces
    # ("Dad Shah" -> "dadshah"), which is a very common variant.
    words = _WORD_RE.findall(cleaned)
    if len(words) > 1:
        terms.append("".join(words).lower())
        # The longest word is usually the distinctive surname or keyword.
        longest = max(words, key=len)
        if len(longest) >= 5:
            terms.append(longest.lower())

    # Transliterated names vary in vowel length between sources: the same person
    # is "Dad Shah" on one page and "Daad Shah" on the next. Without this, real
    # facts on an accepted page are silently dropped.
    for base in list(terms):
        for doubled in _vowel_variants(base):
            terms.append(doubled)

    unique: List[str] = []
    for term in terms:
        if term and term not in unique:
            unique.append(term)
    return unique


def _vowel_variants(term: str, limit: int = 4) -> List[str]:
    """Return spellings of *term* with a single vowel doubled, and vice versa.

    Covers the common transliteration difference ("Dad" / "Daad") without
    pulling in a heavy fuzzy-matching dependency.
    """
    variants: List[str] = []
    # Collapse existing doubles: "daad" -> "dad".
    collapsed = re.sub(r"([aeiou])\1", r"\1", term)
    if collapsed != term:
        variants.append(collapsed)
    # Double each vowel in turn: "dad" -> "daad".
    for match in re.finditer(r"[aeiou]", term):
        index = match.start()
        variants.append(term[:index] + term[index] * 2 + term[index + 1:])
        if len(variants) >= limit:
            break
    return variants


def count_mentions(text: str, terms: Sequence[str]) -> int:
    """Count how often any subject term appears in the text."""
    if not text or not terms:
        return 0
    haystack = re.sub(r"\s+", " ", text.lower())
    return sum(haystack.count(term) for term in terms)


# ----------------------------------------------------------------------
# Link discovery
# ----------------------------------------------------------------------
def _is_wikipedia(url: str) -> bool:
    """True for any Wikipedia host."""
    return "wikipedia.org" in urlparse(url).netloc.lower()


def wikipedia_links(url: str, session=None, limit: int = 40) -> List[Tuple[str, str]]:
    """Return (title, url) for the articles a Wikipedia page links to.

    The rendered HTML is built with JavaScript and exposes almost no links to a
    plain fetch, so the MediaWiki API is used instead. Measured on the Dad Shah
    stub: HTML scraping found 1 link, the API found 17.
    """
    import requests

    parsed = urlparse(url)
    title = unquote(parsed.path.rsplit("/", 1)[-1]).replace("_", " ")
    if not title:
        return []

    api = f"{parsed.scheme}://{parsed.netloc}/w/api.php"
    try:
        getter = session.get if session is not None else requests.get
        response = getter(
            api,
            params={
                "action": "query", "prop": "links", "titles": title,
                "pllimit": "max", "plnamespace": 0, "format": "json",
            },
            headers={"User-Agent": "reelmancer/1.0 (article enrichment)"},
            timeout=20,
        )
        if response.status_code != 200:
            LOGGER.info("Wikipedia link lookup returned HTTP %s.", response.status_code)
            return []
        pages = response.json().get("query", {}).get("pages", {})
    except Exception as exc:  # noqa: BLE001 - enrichment must never break the run
        LOGGER.info("Wikipedia link lookup failed: %s", str(exc)[:80])
        return []

    found: List[Tuple[str, str]] = []
    for page in pages.values():
        for link in page.get("links", []):
            name = link.get("title", "")
            if not name or _should_skip_link(name):
                continue
            target = f"{parsed.scheme}://{parsed.netloc}/wiki/{name.replace(' ', '_')}"
            found.append((name, target))
            if len(found) >= limit:
                return found
    return found


def html_links(url: str, html: str, limit: int = 40) -> List[Tuple[str, str]]:
    """Return (anchor text, url) for on-site links found in raw HTML.

    Used for ordinary sites. Only same-host links are followed: an outbound link
    is usually a citation or an advert, not more of the same story.
    """
    host = urlparse(url).netloc.lower()
    found: List[Tuple[str, str]] = []
    seen: Set[str] = set()

    for match in re.finditer(
        r'<a\b[^>]*href=["\']([^"\'#]+)["\'][^>]*>(.*?)</a>', html, re.I | re.S
    ):
        href, anchor = match.group(1), re.sub(r"<[^>]+>", " ", match.group(2))
        anchor = re.sub(r"\s+", " ", anchor).strip()
        absolute = urljoin(url, href)

        if urlparse(absolute).netloc.lower() != host:
            continue
        if absolute.rstrip("/") == url.rstrip("/"):
            continue
        if absolute in seen or _should_skip_link(anchor):
            continue
        if not anchor or len(anchor) < 4:
            continue

        seen.add(absolute)
        found.append((anchor, absolute))
        if len(found) >= limit:
            break
    return found


def _should_skip_link(name: str) -> bool:
    """True for navigation, identifier and meta links."""
    lowered = (name or "").strip().lower()
    if not lowered:
        return True
    if any(lowered.startswith(prefix) for prefix in _WIKI_NAMESPACE_PREFIXES):
        return True
    return any(word in lowered for word in _LINK_STOPWORDS)


def rank_links(
    links: Sequence[Tuple[str, str]], terms: Sequence[str]
) -> List[Tuple[str, str]]:
    """Put links whose own title names the subject first.

    A link titled "Dadshah" is far more promising than one titled "Iran", and
    checking the title costs nothing, so the pages most likely to be accepted
    are fetched first and the budget is not wasted.
    """
    def score(item: Tuple[str, str]) -> int:
        name = item[0].lower()
        return -sum(2 if term in name else 0 for term in terms)

    return sorted(links, key=score)


# ----------------------------------------------------------------------
# Enrichment
# ----------------------------------------------------------------------
def relevant_sentences(text: str, terms: Sequence[str], limit: int = 12) -> List[str]:
    """Keep the sentences of a linked page that actually concern the subject.

    Even an accepted page is mostly about itself. Taking only the sentences that
    name the subject, plus the opening sentence for context, keeps the borrowed
    material on topic.
    """
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
    if not sentences:
        return []

    picked: List[str] = []
    previous_was_subject = False
    for index, sentence in enumerate(sentences):
        if len(sentence.split()) < 5 or _is_reference_line(sentence):
            previous_was_subject = False
            continue

        names_subject = count_mentions(sentence, terms) > 0
        # Prose keeps talking about someone without repeating their name:
        # "Dad Shah was a farmer. He hated the administration." Dropping the
        # second sentence loses a real fact, so a pronoun sentence directly
        # after a subject sentence is carried along.
        continues_subject = previous_was_subject and _starts_with_pronoun(sentence)

        # The first sentence defines what the page is, which gives the borrowed
        # facts their context.
        if names_subject or continues_subject or index == 0:
            if sentence not in picked:
                picked.append(sentence)

        previous_was_subject = names_subject or continues_subject
        if len(picked) >= limit:
            break

    # A page that names the subject in its opening but not in individual
    # sentences afterwards still has a useful lead paragraph. The same filters
    # must apply here, or citation lines slip in through the fallback.
    if len(picked) <= 1 and len(sentences) > 1:
        picked = [
            sentence for sentence in sentences[:6]
            if len(sentence.split()) >= 5 and not _is_reference_line(sentence)
        ][:4] or picked
    return picked


def _starts_with_pronoun(sentence: str) -> bool:
    """True when a sentence continues about someone already named."""
    return bool(_PRONOUN_START.match(sentence))


def _is_reference_line(sentence: str) -> bool:
    """True for citation and archive lines that carry no narrative content."""
    lowered = sentence.lower()
    if any(marker in lowered for marker in _REFERENCE_MARKERS):
        return True
    # Lines that are mostly dates and punctuation, e.g. "2007-09-29."
    letters = sum(character.isalpha() for character in sentence)
    return letters < max(6, len(sentence) // 3)


def enrich(
    article,
    extract_fn,
    html: Optional[str] = None,
    max_accept: int = MAX_PAGES_TO_ACCEPT,
    max_fetch: int = MAX_PAGES_TO_FETCH,
) -> EnrichmentResult:
    """Gather supporting material for a thin article from the pages it links to.

    ``extract_fn`` is the ordinary extractor, injected so this module stays
    testable without network access.
    """
    result = EnrichmentResult()
    terms = subject_terms(article.title, article.text)
    if not terms:
        return result

    if _is_wikipedia(article.url):
        candidates = wikipedia_links(article.url)
    elif html:
        candidates = html_links(article.url, html)
    else:
        candidates = []

    if not candidates:
        LOGGER.info("No followable links found on the source page.")
        return result

    candidates = rank_links(candidates, terms)[:max_fetch]
    LOGGER.info(
        "Checking %d linked page(s) for material about '%s'.", len(candidates), terms[0]
    )

    for name, link_url in candidates:
        if len(result.sources) >= max_accept:
            break
        result.considered += 1
        try:
            linked = extract_fn(link_url)
        except Exception as exc:  # noqa: BLE001 - a dead link must not stop the run
            result.failed += 1
            LOGGER.info("Could not read '%s': %s", name[:40], str(exc)[:60])
            continue

        if linked.word_count > MAX_LINKED_PAGE_WORDS:
            result.rejected_too_large += 1
            LOGGER.info(
                "Skipped '%s': %d words, too broad to be about this subject.",
                name[:40], linked.word_count,
            )
            continue

        mentions = count_mentions(linked.text, terms)
        if mentions < MIN_SUBJECT_MENTIONS:
            result.rejected_irrelevant += 1
            LOGGER.info("Skipped '%s': never mentions the subject.", name[:40])
            continue

        sentences = relevant_sentences(linked.text, terms)
        if not sentences:
            result.rejected_irrelevant += 1
            continue

        result.sources.append(
            SupportingSource(
                url=link_url, title=linked.title or name, mentions=mentions,
                word_count=linked.word_count, sentences=sentences,
            )
        )
        LOGGER.info(
            "Accepted '%s': mentions the subject %d time(s), took %d sentence(s).",
            (linked.title or name)[:40], mentions, len(sentences),
        )

    if result.used:
        LOGGER.info(
            "Enrichment added %d words from %d supporting page(s).",
            result.added_words, len(result.sources),
        )
    else:
        LOGGER.info("No linked page was relevant enough to use.")
    return result
