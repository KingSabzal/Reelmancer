"""TrendAnalyzer: aggregate, deduplicate, categorize, filter and rank raw trends."""

from __future__ import annotations

import logging
import math
import re
from collections import defaultdict
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional

from utility.trends.trend_sources import TREND_SOURCES

LOGGER = logging.getLogger("trend_analyzer")
if not LOGGER.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("[%(name)s] %(message)s"))
    LOGGER.addHandler(_h)
LOGGER.setLevel(logging.INFO)

CATEGORY_KEYWORDS: Dict[str, List[str]] = {
    "Technology": ["ai", "artificial intelligence", "robot", "chip", "software", "app", "iphone",
                    "android", "startup", "quantum", "cyber", "data", "model", "gpu", "code",
                    "openai", "google", "apple", "microsoft", "tesla", "spacex", "launch"],
    "Entertainment": ["movie", "film", "series", "netflix", "album", "song", "concert", "trailer",
                       "actor", "singer", "celebrity", "award", "premiere", "box office", "anime"],
    "Science": ["study", "research", "nasa", "space", "climate", "discovery", "physics", "gene",
                 "brain", "fossil", "telescope", "mars", "vaccine", "experiment"],
    "Politics": ["election", "president", "minister", "parliament", "vote", "policy", "senate",
                  "government", "sanction", "treaty", "summit", "law"],
    "Sports": ["match", "cup", "league", "goal", "final", "olympic", "cricket", "football",
                "soccer", "nba", "nfl", "tennis", "score", "transfer"],
    "Health": ["health", "disease", "diet", "fitness", "mental", "sleep", "doctor", "cancer",
                "virus", "therapy", "workout", "nutrition"],
    "Business": ["market", "stock", "economy", "inflation", "crypto", "bitcoin", "ipo", "merger",
                  "revenue", "layoff", "bank", "price", "salary", "fund"],
    "Culture": ["viral", "trend", "meme", "fashion", "food", "travel", "festival", "art",
                 "museum", "tradition", "language"],
    "Mystery": ["ufo", "unexplained", "mystery", "missing", "ancient", "secret", "conspiracy",
                 "haunted", "discovery of", "hidden"],
    "Controversy": ["ban", "lawsuit", "scandal", "protest", "backlash", "controversy", "fired",
                     "accused", "leak", "boycott"],
}

BLOCKED_TERMS = [
    "porn", "nsfw", "nude", "sex tape", "suicide", "self harm", "terrorist attack", "beheading",
    "massacre", "child abuse", "rape", "gore", "shooting victim", "obituary", "dies at",
    "death of", "killed", "murder of", "onlyfans", "gambling site", "casino bonus",
]

STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "of", "to", "in", "on", "at", "for", "with", "as",
    "by", "is", "are", "was", "were", "be", "this", "that", "it", "its", "from", "new", "how",
    "why", "what", "who", "will", "has", "have", "after", "over", "into", "vs", "amid",
}


def is_english(text: str) -> bool:
    """Keep only Latin-script, mostly-ASCII trends (the app is English-only)."""
    stripped = text.strip()
    if not stripped:
        return False
    letters = [c for c in stripped if c.isalpha()]
    if not letters:
        return False
    ascii_letters = sum(1 for c in letters if c.isascii())
    if ascii_letters / len(letters) < 0.9:
        return False
    # Reject Latin-script but clearly non-English strings via diacritic density.
    diacritics = sum(1 for c in stripped if c.isalpha() and not c.isascii())
    return diacritics <= 1


def normalize(text: str) -> str:
    """Lowercase alphanumeric normalization used for deduplication."""
    return re.sub(r"[^a-z0-9 ]", " ", text.lower()).strip()


def keywords_of(text: str) -> List[str]:
    """Content words of a trend title."""
    return [w for w in normalize(text).split() if w not in STOPWORDS and len(w) > 2]


class TrendAnalyzer:
    """Turns raw source payloads into a ranked list of trend clusters."""

    def __init__(self, similarity_threshold: float = 0.72):
        self.similarity_threshold = similarity_threshold

    # ------------------------------------------------------------------
    def is_safe(self, title: str) -> bool:
        """Filter out risky, harmful or demonetization-prone topics."""
        lowered = title.lower()
        return not any(term in lowered for term in BLOCKED_TERMS)

    def categorize(self, title: str) -> str:
        """Assign a category by keyword voting."""
        lowered = " " + normalize(title) + " "
        scores: Dict[str, int] = defaultdict(int)
        for category, words in CATEGORY_KEYWORDS.items():
            for word in words:
                if f" {word} " in lowered or lowered.startswith(word + " "):
                    scores[category] += 1
        if not scores:
            return "Culture"
        return max(scores.items(), key=lambda item: item[1])[0]

    # ------------------------------------------------------------------
    def aggregate(self, raw: Dict[str, List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
        """Cluster near-duplicate trends across every source."""
        clusters: List[Dict[str, Any]] = []
        for source, items in raw.items():
            for item in items:
                title = (item.get("title") or "").strip()
                if len(title) < 4 or not self.is_safe(title) or not is_english(title):
                    continue
                key = normalize(title)
                if not key:
                    continue
                matched = None
                for cluster in clusters:
                    if SequenceMatcher(None, key, cluster["key"]).ratio() >= self.similarity_threshold:
                        matched = cluster
                        break
                    title_keywords = set(keywords_of(title))
                    cluster_keywords = set(cluster["keywords"])
                    shared = title_keywords & cluster_keywords
                    smaller = min(len(title_keywords), len(cluster_keywords)) or 1
                    if len(shared) >= 2 and len(shared) / smaller >= 0.5:
                        matched = cluster
                        break
                if matched:
                    matched["variants"].append(title)
                    matched["sources"].add(source)
                    matched["raw"].append(item)
                    matched["keywords"] = list(set(matched["keywords"]) | set(keywords_of(title)))
                else:
                    clusters.append(
                        {
                            "key": key,
                            "title": title,
                            "variants": [title],
                            "sources": {source},
                            "raw": [item],
                            "keywords": keywords_of(title),
                        }
                    )
        return clusters

    # ------------------------------------------------------------------
    def heat_score(self, cluster: Dict[str, Any]) -> float:
        """Trend Heat Score: platforms 30%, growth 25%, engagement 25%, recency 20%."""
        sources = cluster["sources"]
        platform_component = min(len(sources) / 4.0, 1.0) * 30.0

        # Growth proxy: Google Trends approximate traffic and how many countries carry it.
        traffic = 0
        countries = set()
        for item in cluster["raw"]:
            if item.get("country"):
                countries.add(item["country"])
            approx = str(item.get("approx_traffic", "")).replace("+", "").replace(",", "").strip()
            if approx.endswith("K"):
                traffic = max(traffic, int(float(approx[:-1]) * 1000))
            elif approx.endswith("M"):
                traffic = max(traffic, int(float(approx[:-1]) * 1_000_000))
            elif approx.isdigit():
                traffic = max(traffic, int(approx))
        growth_component = 0.0
        if traffic:
            growth_component += min(math.log10(max(traffic, 10)) / 6.0, 1.0) * 15.0
        growth_component += min(len(countries) / 6.0, 1.0) * 10.0

        # Engagement proxy: Reddit / Hacker News scores and comments.
        engagement = 0
        for item in cluster["raw"]:
            engagement += int(item.get("score", 0) or 0) + 2 * int(item.get("comments", 0) or 0)
        engagement_component = min(math.log10(engagement + 1) / 5.0, 1.0) * 25.0

        # Recency: everything is fetched live, so weight by source freshness weight.
        weights = [TREND_SOURCES.get(s, {}).get("weight", 0.5) for s in sources]
        recency_component = (sum(weights) / max(len(weights), 1)) * 20.0

        total = platform_component + growth_component + engagement_component + recency_component
        return round(min(total, 100.0), 1)

    # ------------------------------------------------------------------
    def analyze(
        self, raw: Dict[str, List[Dict[str, Any]]], limit: int = 40, category: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Return the ranked, categorized trend list."""
        clusters = self.aggregate(raw)
        analyzed: List[Dict[str, Any]] = []
        for cluster in clusters:
            title = max(cluster["variants"], key=len) if cluster["variants"] else cluster["title"]
            entry = {
                "title": title[:180],
                "variants": cluster["variants"][:6],
                "sources": sorted(cluster["sources"]),
                "platform_count": len(cluster["sources"]),
                "countries": sorted({i["country"] for i in cluster["raw"] if i.get("country")}),
                "category": self.categorize(title),
                "keywords": cluster["keywords"][:10],
                "heat_score": self.heat_score(cluster),
                "cross_platform": len(cluster["sources"]) > 1,
            }
            analyzed.append(entry)

        if category and category != "All":
            analyzed = [t for t in analyzed if t["category"] == category]
        analyzed.sort(key=lambda t: (t["cross_platform"], t["heat_score"]), reverse=True)
        LOGGER.info("Analyzed %d trend clusters.", len(analyzed))
        return analyzed[:limit]

    @staticmethod
    def summary(trends: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Category distribution and coverage summary for the UI."""
        distribution: Dict[str, int] = defaultdict(int)
        sources: Dict[str, int] = defaultdict(int)
        for trend in trends:
            distribution[trend["category"]] += 1
            for source in trend["sources"]:
                sources[source] += 1
        return {
            "total": len(trends),
            "categories": dict(sorted(distribution.items(), key=lambda i: i[1], reverse=True)),
            "sources": dict(sorted(sources.items(), key=lambda i: i[1], reverse=True)),
            "cross_platform": sum(1 for t in trends if t["cross_platform"]),
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
