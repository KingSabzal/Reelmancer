"""Deep analysis of an extracted article.

The extractor only recovers raw text. This module reads the whole article and decides
everything the video needs:

* a clean topic (site suffixes and section markers removed)
* the recommended duration, derived from how much substance the article actually holds
* the strongest facts, numbers, quotes and entities, ranked by newsworthiness
* the key passages, so a long article is condensed by importance instead of being
  truncated at the first N words
* the emotional angle and the best narrative structure

Everything here is local text analysis, so it costs nothing and works offline. The LLM
then writes the script from a condensed, high-signal brief rather than a raw dump.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Dict, List, Tuple

LOGGER = logging.getLogger("article_analyzer")
if not LOGGER.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("[%(name)s] %(message)s"))
    LOGGER.addHandler(_h)
LOGGER.setLevel(logging.INFO)

# Words that carry no topical meaning when ranking sentences.
STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "of", "to", "in", "on", "at", "for", "with",
    "as", "by", "is", "are", "was", "were", "be", "been", "being", "this", "that",
    "these", "those", "it", "its", "they", "them", "their", "we", "us", "our", "you",
    "your", "he", "she", "his", "her", "from", "have", "has", "had", "not", "no",
    "which", "who", "what", "when", "where", "how", "why", "can", "could", "would",
    "should", "will", "may", "might", "must", "there", "here", "than", "then", "so",
    "if", "also", "more", "most", "some", "such", "only", "other", "into", "about",
    "after", "before", "over", "under", "between", "during", "while", "because",
    "said", "says", "say", "one", "two", "new", "like", "just", "out", "up", "down",
}

# Sentences containing these are usually navigation, legal or promotional noise.
NOISE_MARKERS = (
    "cookie", "subscribe to", "sign up", "newsletter", "advertisement", "all rights reserved",
    "privacy policy", "terms of service", "follow us on", "share this", "read more",
    "click here", "log in", "related articles", "photograph:", "getty images",
    "this article was", "correction:", "editor's note", "skip to content",
)

# Signals that a sentence carries real information worth putting in a script.
FACT_PATTERNS = [
    (re.compile(r"\b\d+(?:[.,]\d+)?\s*(?:percent|%)"), 3.0, "statistic"),
    (re.compile(r"\$\s?\d|\b\d+(?:[.,]\d+)?\s*(?:billion|million|trillion|thousand)\b", re.I), 3.0, "figure"),
    (re.compile(r"\b(?:19|20)\d{2}\b"), 1.5, "date"),
    (re.compile(r"\b\d+(?:[.,]\d+)?\s*(?:times|x)\s+(?:more|less|faster|slower|bigger|smaller)", re.I), 3.0, "comparison"),
    (re.compile(r"\b(?:first|only|largest|smallest|fastest|slowest|oldest|newest|worst|best|record)\b", re.I), 2.0, "superlative"),
    (re.compile(r"\b(?:study|research|scientists?|researchers?|according to|survey|report)\b", re.I), 2.0, "evidence"),
    (re.compile(r"\b(?:discovered|revealed|found|announced|proved|confirmed|warns?|shows?)\b", re.I), 1.5, "finding"),
    (re.compile(r"\b(?:never|nobody|no one|unprecedented|unexpected|surprising|contrary)\b", re.I), 2.0, "surprise"),
    (re.compile(r"\b\d+\s*(?:years?|months?|days?|hours?|minutes?|seconds?)\b", re.I), 1.5, "duration"),
    (re.compile(r"\b\d+(?:[.,]\d+)?\s*(?:km|miles|meters|metres|feet|kg|tons?|degrees)\b", re.I), 2.0, "measurement"),
]

EMOTION_LEXICON = {
    "alarming": ["crisis", "danger", "threat", "warning", "risk", "collapse", "emergency",
                  "catastroph", "disaster", "alarming", "urgent", "deadly"],
    "hopeful": ["breakthrough", "solution", "hope", "promising", "success", "improve",
                 "recovery", "progress", "cure", "advance"],
    "surprising": ["surprising", "unexpected", "shocking", "astonishing", "remarkable",
                    "bizarre", "strange", "mystery", "puzzle", "paradox"],
    "controversial": ["controversy", "dispute", "criticis", "backlash", "accus", "denied",
                       "lawsuit", "ban", "protest", "scandal"],
    "inspiring": ["achievement", "triumph", "overcame", "record", "pioneer", "champion",
                   "historic", "milestone"],
}

# Words per minute used when converting substance into a duration.
WORDS_PER_MINUTE = 140


@dataclass
class Fact:
    """A ranked, script-worthy sentence from the article."""

    text: str
    score: float
    kinds: List[str] = field(default_factory=list)
    position: float = 0.0

    def as_dict(self) -> Dict[str, Any]:
        return {"text": self.text, "score": round(self.score, 2), "kinds": self.kinds}


@dataclass
class ArticleAnalysis:
    """Everything the pipeline needs, derived from the whole article."""

    clean_title: str
    topic: str
    summary: str
    key_facts: List[Fact]
    key_passages: List[str]
    entities: List[str]
    keywords: List[str]
    numbers: List[str]
    quotes: List[str]
    emotion: str
    substance_score: float
    recommended_duration: int
    duration_range: Tuple[int, int]
    reading_time_seconds: int
    condensed_text: str
    # How far the source material actually goes.
    source_words: int = 0
    max_supported_duration: int = 0
    coverage_ratio: float = 1.0
    sufficiency: str = "ok"
    sufficiency_note: str = ""

    def as_dict(self) -> Dict[str, Any]:
        """Serializable summary for the UI."""
        return {
            "clean_title": self.clean_title,
            "topic": self.topic,
            "summary": self.summary,
            "key_facts": [f.as_dict() for f in self.key_facts],
            "entities": self.entities,
            "keywords": self.keywords,
            "numbers": self.numbers[:10],
            "quotes": self.quotes[:3],
            "emotion": self.emotion,
            "substance_score": round(self.substance_score, 1),
            "recommended_duration": self.recommended_duration,
            "duration_range": list(self.duration_range),
            "reading_time_seconds": self.reading_time_seconds,
            "source_words": self.source_words,
            "max_supported_duration": self.max_supported_duration,
            "coverage_ratio": round(self.coverage_ratio, 2),
            "sufficiency": self.sufficiency,
            "sufficiency_note": self.sufficiency_note,
        }


# ----------------------------------------------------------------------
def clean_title(raw_title: str, site: str = "") -> str:
    """Strip site suffixes and section prefixes so the title reads as a topic."""
    title = re.sub(r"\s+", " ", raw_title or "").strip()
    # "Article name - BBC News", "Article name | The Guardian", "Article name — Wired"
    for separator in (" - ", " | ", " \u2013 ", " \u2014 ", " :: ", " \u00b7 "):
        if separator in title:
            parts = [p.strip() for p in title.split(separator) if p.strip()]
            if len(parts) > 1:
                # Drop trailing parts that look like a publication name.
                site_words = {w.lower() for w in re.findall(r"[A-Za-z]+", site)}
                while len(parts) > 1:
                    tail_words = {w.lower() for w in re.findall(r"[A-Za-z]+", parts[-1])}
                    short_tail = len(parts[-1].split()) <= 4
                    if short_tail and (tail_words & site_words or len(parts[-1]) < 30):
                        parts.pop()
                    else:
                        break
                title = max(parts, key=len) if len(parts) > 1 else parts[0]
    title = re.sub(r"^\s*(?:news|opinion|analysis|video|live)\s*[:\-]\s*", "", title, flags=re.I)
    return title.strip(" -|\u2013\u2014:")


def split_sentences(text: str) -> List[str]:
    """Split text into sentences, keeping ones long enough to be meaningful."""
    text = re.sub(r"\s+", " ", text)
    raw = re.split(r"(?<=[.!?])\s+(?=[A-Z\"'\u201c])", text)
    sentences = []
    for sentence in raw:
        sentence = sentence.strip()
        if 30 <= len(sentence) <= 400:
            sentences.append(sentence)
    return sentences


def _dedupe(sentences: List[str]) -> List[str]:
    """Drop repeated sentences, which are common in wikis and syndicated articles."""
    seen: set = set()
    unique: List[str] = []
    for sentence in sentences:
        fingerprint = re.sub(r"[^a-z0-9]+", " ", sentence.lower()).strip()
        fingerprint = " ".join(fingerprint.split()[:14])
        if fingerprint and fingerprint not in seen:
            seen.add(fingerprint)
            unique.append(sentence)
    return unique


def is_noise(sentence: str) -> bool:
    """True when a sentence is navigation, legal or promotional text."""
    lowered = sentence.lower()
    return any(marker in lowered for marker in NOISE_MARKERS)


def extract_keywords(text: str, limit: int = 12) -> List[str]:
    """Most frequent meaningful words in the article."""
    words = [w.lower() for w in re.findall(r"[A-Za-z][A-Za-z'-]{2,}", text)]
    words = [w for w in words if w not in STOPWORDS and len(w) > 3]
    return [word for word, _ in Counter(words).most_common(limit)]


def extract_entities(text: str, limit: int = 12) -> List[str]:
    """Capitalised multi-word names, a light substitute for full entity recognition."""
    candidates = re.findall(r"\b(?:[A-Z][a-z]{2,}(?:\s+[A-Z][a-z]{2,}){0,3})\b", text)
    counts: Counter = Counter()
    for candidate in candidates:
        if candidate.split()[0].lower() in STOPWORDS:
            continue
        if len(candidate) < 4:
            continue
        counts[candidate] += 1
    # Prefer names that appear more than once and are not sentence-start artefacts.
    ranked = [name for name, count in counts.most_common(limit * 3) if count >= 2]
    return ranked[:limit]


def extract_numbers(text: str, limit: int = 12) -> List[str]:
    """Numeric claims worth keeping in the script."""
    patterns = [
        r"\$\s?\d[\d,.]*\s*(?:billion|million|trillion|thousand)?",
        r"\b\d+(?:[.,]\d+)?\s*(?:percent|%)",
        r"\b\d+(?:[.,]\d+)?\s*(?:billion|million|trillion|thousand)\b",
        r"\b\d+(?:[.,]\d+)?\s*(?:km|miles|meters|metres|feet|kg|tons?|degrees|years?)\b",
    ]
    found: List[str] = []
    for pattern in patterns:
        for match in re.findall(pattern, text, flags=re.IGNORECASE):
            value = match.strip()
            if value and value not in found:
                found.append(value)
    return found[:limit]


def extract_quotes(text: str, limit: int = 5) -> List[str]:
    """Direct quotations, which make strong script lines."""
    quotes = re.findall(r"[\"\u201c]([^\"\u201d]{40,240})[\"\u201d]", text)
    return [re.sub(r"\s+", " ", q).strip() for q in quotes][:limit]


def score_sentence(sentence: str, keywords: List[str], position: float) -> Tuple[float, List[str]]:
    """Score a sentence by informational value."""
    score = 0.0
    kinds: List[str] = []

    for pattern, weight, label in FACT_PATTERNS:
        if pattern.search(sentence):
            score += weight
            kinds.append(label)

    lowered = sentence.lower()
    score += sum(1.0 for keyword in keywords[:8] if keyword in lowered)

    # Journalism front-loads the important material.
    score += (1.0 - position) * 2.0

    words = len(sentence.split())
    if 12 <= words <= 45:
        score += 1.0
    elif words < 8:
        score -= 1.5

    if sentence.rstrip().endswith("?"):
        score += 0.5
    return score, sorted(set(kinds))


def detect_emotion(text: str) -> str:
    """Dominant emotional angle of the article."""
    lowered = text.lower()
    scores = {
        emotion: sum(lowered.count(word) for word in words)
        for emotion, words in EMOTION_LEXICON.items()
    }
    best = max(scores.items(), key=lambda item: item[1])
    return best[0] if best[1] >= 2 else "neutral"


# ----------------------------------------------------------------------
def recommend_duration(
    facts: List[Fact], word_count: int, quotes: int, numbers: int
) -> Tuple[int, Tuple[int, int], float]:
    """Derive a duration from how much genuine substance the article holds.

    A 300 word news brief and a 5000 word deep dive should not produce the same video.
    The score counts distinct strong facts rather than raw length, because padding does
    not justify a longer script.
    """
    strong_facts = [f for f in facts if f.score >= 4.0]
    usable_facts = [f for f in facts if f.score >= 2.5]

    substance = min(
        len(strong_facts) * 2.0
        + min(len(usable_facts), 25) * 0.8
        + min(numbers, 8) * 0.6
        + min(quotes, 3) * 0.8
        + min(word_count / 400.0, 6.0),
        100.0,
    )

    # Every solid fact needs roughly 8-10 seconds of narration to land.
    seconds = 25 + len(usable_facts) * 7 + len(strong_facts) * 4
    seconds = int(max(25, min(seconds, 480)))

    # Snap to friendly values and keep Shorts under the 120 second boundary.
    if seconds < 55:
        recommended = 45
    elif seconds < 75:
        recommended = 60
    elif seconds < 105:
        recommended = 90
    elif seconds < 150:
        recommended = 120
    elif seconds < 210:
        recommended = 180
    elif seconds < 300:
        recommended = 240
    else:
        recommended = 300

    lower = max(25, int(recommended * 0.6))
    upper = min(600, int(recommended * 1.8))
    return recommended, (lower, upper), substance


# A script can restate and frame a source, but it cannot conjure facts. This is how
# many spoken words one word of source material can honestly support: a little
# above 1.0, because framing and transitions are legitimate additions, but well
# below the point where the model would be filling gaps from its own memory.
WORDS_PER_SOURCE_WORD = 1.25

# Never recommend less than this: below it there is no room for a hook and a payoff.
ABSOLUTE_MIN_DURATION = 15


def assess_sufficiency(
    source_words: int, recommended: int
) -> Tuple[int, float, str, str]:
    """Check whether the source can actually fill the recommended duration.

    Returns the longest honestly supportable duration, the share of the
    recommended script the source covers, a status, and a message for the user.

    Without this a 102 word stub silently produced a 60 second recommendation,
    which needs about 140 spoken words. The model had to invent roughly half the
    script, and for a historical subject that means invented history.
    """
    supportable_words = source_words * WORDS_PER_SOURCE_WORD
    max_duration = int(supportable_words / WORDS_PER_MINUTE * 60)
    max_duration = max(ABSOLUTE_MIN_DURATION, min(max_duration, 600))

    needed_words = recommended / 60.0 * WORDS_PER_MINUTE
    coverage = min(supportable_words / needed_words, 1.0) if needed_words else 1.0

    if coverage >= 0.95:
        return max_duration, coverage, "ok", ""

    if coverage >= 0.7:
        status = "thin"
        note = (
            f"This source holds about {source_words} words of usable material, which "
            f"honestly supports roughly {max_duration}s of narration. The suggested "
            f"length was reduced from {recommended}s to match it. A longer video would "
            f"need content that is not in the source."
        )
    else:
        status = "insufficient"
        note = (
            f"This page holds only about {source_words} words of usable material, "
            f"enough for roughly {max_duration}s. The suggested length was reduced from "
            f"{recommended}s. For a longer video, use a page with more detail, or accept "
            f"that most of the script would be invented rather than sourced."
        )
    return max_duration, coverage, status, note


def condense(sentences: List[str], scored: List[Fact], target_words: int = 1200) -> str:
    """Keep the highest-value passages in their original order.

    Truncating at the first N words throws away the best material in long articles;
    ranking keeps the substance and preserves reading order for coherence.
    """
    if not scored:
        return " ".join(sentences)[: target_words * 6]

    order = {fact.text: index for index, fact in enumerate(scored)}
    chosen: List[str] = []
    total = 0
    for fact in scored:
        words = len(fact.text.split())
        if total + words > target_words:
            continue
        chosen.append(fact.text)
        total += words
        if total >= target_words:
            break

    # Restore the original narrative order.
    index_in_article = {sentence: i for i, sentence in enumerate(sentences)}
    chosen.sort(key=lambda s: index_in_article.get(s, order.get(s, 0)))
    return " ".join(chosen)


def analyze(article, max_facts: int = 12) -> ArticleAnalysis:
    """Run the full analysis over an extracted Article.

    Supporting material gathered from linked pages is analysed together with the
    main text, so facts borrowed from a related page can be used in the script.
    """
    text = getattr(article, "full_text", None) or article.text or ""
    title = clean_title(article.title, article.site)

    sentences = _dedupe([s for s in split_sentences(text) if not is_noise(s)])
    keywords = extract_keywords(text)
    entities = extract_entities(text)
    numbers = extract_numbers(text)
    quotes = extract_quotes(text)
    emotion = detect_emotion(text)

    scored: List[Fact] = []
    total = max(len(sentences), 1)
    for index, sentence in enumerate(sentences):
        score, kinds = score_sentence(sentence, keywords, index / total)
        scored.append(Fact(sentence, score, kinds, index / total))
    scored.sort(key=lambda fact: fact.score, reverse=True)

    key_facts = scored[:max_facts]
    key_passages = [fact.text for fact in scored[: max_facts * 2]]

    recommended, duration_range, substance = recommend_duration(
        scored, len(text.split()), len(quotes), len(numbers)
    )

    # The recommendation above says how much the material deserves; this says how
    # much it can actually fill. A video is capped by whichever is smaller, so we
    # never ask the model to invent the difference.
    # Raw page length overstates what is usable: navigation, link lists and
    # citation lines all count as words but carry no narration. Only sentences
    # that survived scoring are counted.
    usable_facts = [fact for fact in scored if fact.score >= 2.5]
    source_words = sum(len(fact.text.split()) for fact in usable_facts)
    if not source_words:
        source_words = len(text.split())
    max_supported, coverage, sufficiency, note = assess_sufficiency(source_words, recommended)
    if sufficiency != "ok":
        recommended = max(ABSOLUTE_MIN_DURATION, min(recommended, max_supported))
        duration_range = (
            max(ABSOLUTE_MIN_DURATION, int(recommended * 0.6)),
            max(recommended, int(recommended * 1.4)),
        )

    summary = " ".join(sentence for sentence in sentences[:2])[:400]
    condensed = condense(sentences, scored)

    analysis = ArticleAnalysis(
        clean_title=title,
        topic=title,
        summary=summary,
        key_facts=key_facts,
        key_passages=key_passages,
        entities=entities,
        keywords=keywords,
        numbers=numbers,
        quotes=quotes,
        emotion=emotion,
        substance_score=substance,
        recommended_duration=recommended,
        duration_range=duration_range,
        reading_time_seconds=int(len(text.split()) / WORDS_PER_MINUTE * 60),
        condensed_text=condensed,
        source_words=source_words,
        max_supported_duration=max_supported,
        coverage_ratio=coverage,
        sufficiency=sufficiency,
        sufficiency_note=note,
    )
    LOGGER.info(
        "Analyzed '%s': %d facts, %d numbers, %d quotes, emotion=%s, "
        "substance=%.1f, recommended %ds, sufficiency=%s.",
        title[:50], len(key_facts), len(numbers), len(quotes), emotion,
        substance, recommended, sufficiency,
    )
    return analysis


def build_analyzed_brief(article, analysis: ArticleAnalysis) -> str:
    """Build a high-signal brief: the ranked findings plus the condensed article."""
    lines = [
        f"SOURCE ARTICLE: {analysis.clean_title}",
        f"PUBLISHER: {article.site}",
    ]
    if article.published:
        lines.append(f"PUBLISHED: {article.published}")
    if article.author:
        lines.append(f"AUTHOR: {article.author}")
    lines.append(f"URL: {article.url}")
    lines.append(f"EMOTIONAL ANGLE: {analysis.emotion}")
    lines.append("")

    if analysis.key_facts:
        lines.append("MOST IMPORTANT FACTS (ranked, use these first):")
        for index, fact in enumerate(analysis.key_facts[:10], 1):
            label = f" [{', '.join(fact.kinds)}]" if fact.kinds else ""
            lines.append(f"{index}. {fact.text}{label}")
        lines.append("")

    if analysis.numbers:
        lines.append("KEY NUMBERS: " + ", ".join(analysis.numbers[:10]))
    if analysis.entities:
        lines.append("KEY NAMES: " + ", ".join(analysis.entities[:8]))
    if analysis.quotes:
        lines.append("")
        lines.append("DIRECT QUOTES:")
        for quote in analysis.quotes[:3]:
            lines.append(f'- "{quote}"')
    lines.append("")
    lines.append("CONDENSED ARTICLE (highest value passages, in reading order):")
    lines.append(analysis.condensed_text)

    if getattr(article, "supporting_sources", None):
        titles = ", ".join(s["title"] for s in article.supporting_sources)
        lines.append("")
        lines.append(
            "NOTE ON SOURCES: the main page was short, so some facts above come from "
            f"closely related pages ({titles}). They all concern the same subject and "
            "may be used freely."
        )

    # Without this the model quietly fills a thin source with plausible invention,
    # which for a historical or political subject means invented history.
    lines.append("")
    lines.append("SOURCING RULES (strict):")
    lines.append(
        "- Use ONLY the facts above. Do not add dates, names, numbers, causes or "
        "outcomes that do not appear in this brief, even if you believe them to be true."
    )
    lines.append(
        "- If the material does not fill the requested length, write a shorter, denser "
        "script instead of padding it with invented detail."
    )
    lines.append(
        "- General framing, context sentences and transitions are fine, as long as they "
        "state no new factual claim."
    )
    if analysis.sufficiency != "ok":
        lines.append(
            f"- WARNING: this source is thin ({analysis.source_words} usable words). "
            "Prefer brevity over completeness."
        )
    return "\n".join(lines)
