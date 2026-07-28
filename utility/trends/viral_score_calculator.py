"""Viral score (0-100) for generated title suggestions."""

from __future__ import annotations

import math
import re
from typing import Any, Dict, List, Optional

WEIGHTS = {
    "trend_relevance": 0.25,
    "curiosity_gap": 0.20,
    "emotional_trigger": 0.20,
    "shareability": 0.15,
    "seo_potential": 0.10,
    "competition": 0.10,
}

CURIOSITY_MARKERS = [
    "why", "how", "what if", "secret", "hidden", "nobody", "no one", "actually", "really",
    "truth", "reason", "until", "before", "after", "inside", "behind", "the one", "never",
    "still", "quietly", "just", "finally", "turns out", "?",
]

EMOTION_WORDS = {
    "shock": ["shocking", "insane", "unbelievable", "banned", "collapse", "exposed", "warning",
               "disaster", "crisis", "shut down", "erased", "vanished"],
    "excitement": ["breakthrough", "record", "first", "launch", "unlocked", "fastest", "biggest",
                    "wins", "beats", "new era"],
    "fear": ["danger", "risk", "threat", "too late", "trap", "mistake", "collapse", "lost",
              "dark side", "worst"],
    "joy": ["amazing", "beautiful", "genius", "brilliant", "hilarious", "wholesome", "perfect"],
    "curiosity": ["mystery", "unknown", "strange", "weird", "impossible", "paradox", "anomaly"],
}

SHARE_TRIGGERS = [
    "you", "your", "everyone", "nobody", "we", "this is why", "should", "must", "stop",
    "start", "never", "before you", "in 2026", "right now",
]

FILLER_WORDS = ["video", "watch", "subscribe", "channel", "today we", "in this"]

POWER_NUMBERS = re.compile(r"\b\d{1,4}\b")


def _clip(value: float, low: float = 0.0, high: float = 100.0) -> float:
    """Clamp a value into a range."""
    return max(low, min(value, high))


class ViralScoreCalculator:
    """Computes each component of the viral score and the weighted total."""

    def trend_relevance(self, title: str, trend_keywords: List[str]) -> float:
        """How closely the title uses live trending vocabulary."""
        if not trend_keywords:
            return 45.0
        words = set(re.findall(r"[a-z']+", title.lower()))
        overlap = len(words & {k.lower() for k in trend_keywords})
        score = 45.0 + overlap * 16.0
        return _clip(score)

    def curiosity_gap(self, title: str) -> float:
        """Presence of open loops, questions and withheld information."""
        lowered = title.lower()
        hits = sum(1 for marker in CURIOSITY_MARKERS if marker in lowered)
        score = 42.0 + hits * 13.0
        if lowered.endswith("?"):
            score += 8.0
        if len(title.split()) <= 5:
            score -= 6.0  # too short to build a gap
        return _clip(score)

    def emotional_trigger(self, title: str) -> float:
        """Strength and variety of emotional vocabulary."""
        lowered = title.lower()
        categories_hit = 0
        total_hits = 0
        for words in EMOTION_WORDS.values():
            hits = sum(1 for word in words if word in lowered)
            if hits:
                categories_hit += 1
                total_hits += hits
        score = 40.0 + total_hits * 12.0 + categories_hit * 9.0
        if POWER_NUMBERS.search(title):
            score += 7.0
        return _clip(score)

    def shareability(self, title: str) -> float:
        """Would a viewer send this to a friend?"""
        lowered = title.lower()
        hits = sum(1 for trigger in SHARE_TRIGGERS if trigger in lowered)
        score = 44.0 + hits * 11.0
        if any(filler in lowered for filler in FILLER_WORDS):
            score -= 15.0
        length = len(title)
        if 35 <= length <= 62:
            score += 10.0
        return _clip(score)

    def seo_potential(self, title: str, trend_keywords: List[str]) -> float:
        """Approximate search demand from keyword presence and title shape."""
        words = [w for w in re.findall(r"[a-z']+", title.lower()) if len(w) > 3]
        if not words:
            return 20.0
        keyword_hits = len(set(words) & {k.lower() for k in trend_keywords})
        score = 40.0 + keyword_hits * 13.0
        if 4 <= len(words) <= 10:
            score += 12.0
        if "2026" in title:
            score += 6.0
        return _clip(score)

    def competition(self, title: str, platform_count: int, history_titles: List[str]) -> float:
        """Lower saturation means a higher score."""
        score = 76.0 - min(platform_count, 5) * 5.0
        lowered = title.lower()
        similar = sum(
            1 for old in history_titles
            if len(set(lowered.split()) & set(old.lower().split())) >= 4
        )
        score -= similar * 12.0
        if len(title.split()) >= 8:
            score += 8.0  # long-tail phrasing faces less competition
        return _clip(score)

    # ------------------------------------------------------------------
    def score(
        self,
        title: str,
        trend_keywords: Optional[List[str]] = None,
        platform_count: int = 1,
        history_titles: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Return the component breakdown and the weighted total."""
        trend_keywords = trend_keywords or []
        history_titles = history_titles or []
        components = {
            "trend_relevance": self.trend_relevance(title, trend_keywords),
            "curiosity_gap": self.curiosity_gap(title),
            "emotional_trigger": self.emotional_trigger(title),
            "shareability": self.shareability(title),
            "seo_potential": self.seo_potential(title, trend_keywords),
            "competition": self.competition(title, platform_count, history_titles),
        }
        total = sum(components[key] * WEIGHTS[key] for key in WEIGHTS)
        total = round(_clip(total), 1)
        return {"total": total, "components": {k: round(v, 1) for k, v in components.items()},
                "badge": badge_for(total)}


def badge_for(score: float) -> Dict[str, str]:
    """Return the colored badge definition for a score."""
    if score >= 90:
        return {"label": "FIRE", "emoji": "\U0001F525", "color": "#16A34A", "tier": "fire"}
    if score >= 75:
        return {"label": "HIGH", "emoji": "\u2B50", "color": "#EAB308", "tier": "high"}
    if score >= 60:
        return {"label": "GOOD", "emoji": "\u2705", "color": "#2563EB", "tier": "good"}
    if score >= 40:
        return {"label": "FAIR", "emoji": "\U0001F4CA", "color": "#6B7280", "tier": "fair"}
    return {"label": "LOW", "emoji": "", "color": "#9CA3AF", "tier": "hidden"}


def uniqueness_score(title: str, history_titles: List[str]) -> float:
    """0-100 originality score against previously suggested titles."""
    if not history_titles:
        return 100.0
    words = set(re.findall(r"[a-z']+", title.lower()))
    if not words:
        return 0.0
    worst_overlap = 0.0
    for old in history_titles:
        old_words = set(re.findall(r"[a-z']+", old.lower()))
        if not old_words:
            continue
        overlap = len(words & old_words) / len(words | old_words)
        worst_overlap = max(worst_overlap, overlap)
    return round(_clip((1 - worst_overlap) * 100), 1)


def entropy(title: str) -> float:
    """Lexical entropy, used as a secondary novelty signal."""
    words = re.findall(r"[a-z']+", title.lower())
    if not words:
        return 0.0
    unique = len(set(words))
    return round(unique / len(words) * math.log(unique + 1), 3)
