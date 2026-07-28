"""KeyMomentDetector: find script moments that deserve a sound effect."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

TRIGGERS: Dict[str, Dict[str, Any]] = {
    "emphasis": {
        "words": ["very", "extremely", "absolutely", "insane", "massive", "never", "always"],
        "sfx": "impact hit",
        "weight": 0.7,
    },
    "transition": {
        "words": ["but", "however", "now", "then", "finally", "meanwhile", "suddenly"],
        "sfx": "whoosh transition",
        "weight": 0.6,
    },
    "question": {
        "words": ["what", "why", "how", "who", "where", "when"],
        "sfx": "notification ping",
        "weight": 0.5,
    },
    "reveal": {
        "words": ["the secret is", "the answer is", "here it is", "turns out", "the truth"],
        "sfx": "riser reveal",
        "weight": 0.95,
    },
    "action": {
        "words": ["run", "jump", "explode", "crash", "break", "launch", "smash", "hit"],
        "sfx": "impact crash",
        "weight": 0.85,
    },
    "emotion": {
        "words": ["love", "fear", "joy", "anger", "hate", "hope", "panic"],
        "sfx": "emotional swell",
        "weight": 0.5,
    },
    "nature": {
        "words": ["rain", "wind", "thunder", "ocean", "storm", "forest", "fire"],
        "sfx": "nature ambience",
        "weight": 0.6,
    },
    "technology": {
        "words": ["computer", "robot", "digital", "algorithm", "software", "data", "ai"],
        "sfx": "digital beep",
        "weight": 0.55,
    },
    "comedy": {
        "words": ["haha", "funny", "joke", "hilarious", "silly"],
        "sfx": "cartoon boing",
        "weight": 0.7,
    },
    "horror": {
        "words": ["scary", "ghost", "dark", "creepy", "haunted", "terrifying", "blood"],
        "sfx": "horror sting",
        "weight": 0.9,
    },
}

DENSITY_LIMITS = {"low": 2.0, "medium": 5.0, "high": 9.0}  # sound effects per minute


@dataclass
class KeyMoment:
    """A single detected moment in the script timeline."""

    time: float
    category: str
    trigger: str
    sfx_query: str
    score: float

    def as_dict(self) -> Dict[str, Any]:
        """Serializable representation of the moment."""
        return {
            "time": round(self.time, 2),
            "category": self.category,
            "trigger": self.trigger,
            "sfx_query": self.sfx_query,
            "score": round(self.score, 3),
        }


class KeyMomentDetector:
    """Scans a script (optionally with word timings) for SFX-worthy moments."""

    def __init__(self, sfx_density: str = "medium"):
        self.sfx_density = sfx_density if sfx_density in DENSITY_LIMITS else "medium"

    def detect(
        self,
        script: str,
        timed_captions: Optional[List[Tuple[Tuple[float, float], str]]] = None,
        duration_seconds: Optional[float] = None,
    ) -> List[Dict[str, Any]]:
        """Return the ranked list of key moments, capped by the density setting."""
        words = script.split()
        total_words = max(len(words), 1)
        duration = duration_seconds or (
            timed_captions[-1][0][1] if timed_captions else total_words / 140 * 60
        )

        word_times = self._word_times(words, timed_captions, duration)
        moments: List[KeyMoment] = []
        lowered = script.lower()

        # Phrase-level triggers (reveals)
        for category, config in TRIGGERS.items():
            for phrase in config["words"]:
                if " " not in phrase:
                    continue
                for match in re.finditer(re.escape(phrase), lowered):
                    index = len(lowered[: match.start()].split())
                    moments.append(
                        KeyMoment(
                            word_times.get(index, 0.0), category, phrase, config["sfx"], config["weight"]
                        )
                    )

        # Word-level triggers
        for index, raw_word in enumerate(words):
            token = re.sub(r"[^a-z']", "", raw_word.lower())
            if not token:
                continue
            for category, config in TRIGGERS.items():
                if token in config["words"]:
                    moments.append(
                        KeyMoment(
                            word_times.get(index, 0.0), category, token, config["sfx"], config["weight"]
                        )
                    )
            if raw_word.endswith("!"):
                moments.append(
                    KeyMoment(word_times.get(index, 0.0), "emphasis", "!", "impact hit", 0.8)
                )
            if raw_word.endswith("?"):
                moments.append(
                    KeyMoment(word_times.get(index, 0.0), "question", "?", "notification ping", 0.6)
                )

        return self._apply_density(moments, duration)

    @staticmethod
    def _word_times(
        words: List[str],
        timed_captions: Optional[List[Tuple[Tuple[float, float], str]]],
        duration: float,
    ) -> Dict[int, float]:
        """Map word index -> start time, using real caption timings when available."""
        mapping: Dict[int, float] = {}
        if timed_captions:
            index = 0
            for (start, _end), text in timed_captions:
                for _ in str(text).split():
                    mapping[index] = float(start)
                    index += 1
            if mapping:
                return mapping
        per_word = duration / max(len(words), 1)
        return {i: i * per_word for i in range(len(words))}

    def _apply_density(self, moments: List[KeyMoment], duration: float) -> List[Dict[str, Any]]:
        """Keep the highest-scoring moments, respecting the per-minute density cap."""
        limit = int(DENSITY_LIMITS[self.sfx_density] * max(duration, 1) / 60) or 1
        moments.sort(key=lambda moment: moment.score, reverse=True)
        chosen: List[KeyMoment] = []
        min_gap = max(1.2, duration / max(limit * 2, 1))
        for moment in moments:
            if len(chosen) >= limit:
                break
            if all(abs(moment.time - other.time) >= min_gap for other in chosen):
                chosen.append(moment)
        chosen.sort(key=lambda moment: moment.time)
        return [moment.as_dict() for moment in chosen]
