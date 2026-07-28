"""Automatic voice selection based on video style, topic keywords and mood."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple

from utility.content.video_styles import get_style
from utility.audio.voice_profiles import DEFAULT_VOICE, VOICE_PROFILES, get_voice

# Topic keyword -> preferred voice traits
TOPIC_HINTS: Dict[str, Dict[str, Any]] = {
    "horror": {"tone": ["dark", "low"], "energy": "low", "moods": ["drama"]},
    "ghost": {"tone": ["dark", "soft"], "energy": "low", "moods": ["drama"]},
    "crime": {"tone": ["serious", "measured"], "energy": "low", "moods": ["news"]},
    "money": {"tone": ["confident", "professional"], "energy": "medium", "moods": ["corporate"]},
    "finance": {"tone": ["confident", "professional"], "energy": "medium", "moods": ["corporate"]},
    "crypto": {"tone": ["cool", "modern"], "energy": "medium", "moods": ["tech"]},
    "ai": {"tone": ["clear", "modern"], "energy": "medium", "moods": ["tech"]},
    "robot": {"tone": ["clear", "modern"], "energy": "medium", "moods": ["tech"]},
    "space": {"tone": ["deep", "calm"], "energy": "low", "moods": ["documentary"]},
    "science": {"tone": ["clear", "measured"], "energy": "low", "moods": ["educational"]},
    "history": {"tone": ["mature", "measured"], "energy": "low", "moods": ["documentary"]},
    "workout": {"tone": ["energetic", "bold"], "energy": "high", "moods": ["motivational"]},
    "fitness": {"tone": ["energetic", "bold"], "energy": "high", "moods": ["motivational"]},
    "game": {"tone": ["cool", "youthful"], "energy": "high", "moods": ["gaming"]},
    "funny": {"tone": ["playful", "bright"], "energy": "high", "moods": ["comedy"]},
    "comedy": {"tone": ["playful", "bright"], "energy": "high", "moods": ["comedy"]},
    "relax": {"tone": ["soft", "gentle"], "energy": "low", "moods": ["wellness"]},
    "sleep": {"tone": ["soft", "gentle"], "energy": "low", "moods": ["wellness"]},
    "kids": {"tone": ["youthful", "playful"], "energy": "high", "moods": ["kids"]},
    "travel": {"tone": ["warm", "relaxed"], "energy": "medium", "moods": ["travel"]},
    "nature": {"tone": ["warm", "calm"], "energy": "low", "moods": ["nature"]},
    "luxury": {"tone": ["refined", "elegant"], "energy": "low", "moods": ["luxury"]},
    "business": {"tone": ["confident", "professional"], "energy": "medium", "moods": ["corporate"]},
}

ENERGY_BY_PACING = {
    "very fast": "high",
    "fast": "high",
    "building": "high",
    "medium": "medium",
    "slow": "low",
    "very slow": "low",
    "irregular": "medium",
}


def _pacing_energy(pacing: str) -> str:
    """Map a style pacing description to an energy level."""
    pacing = (pacing or "").lower()
    for prefix, energy in ENERGY_BY_PACING.items():
        if pacing.startswith(prefix):
            return energy
    return "medium"


class IntelligentVoiceSelector:
    """Scores every EdgeTTS voice against the style, topic and mood."""

    def __init__(self, profiles: Dict[str, Dict[str, Any]] | None = None):
        self.profiles = profiles or VOICE_PROFILES

    def _topic_hint(self, topic: str) -> Dict[str, Any]:
        """Aggregate hints from every keyword found in the topic."""
        topic_words = set(re.findall(r"[a-z]+", (topic or "").lower()))
        merged: Dict[str, Any] = {"tone": [], "energy": None, "moods": []}
        for keyword, hint in TOPIC_HINTS.items():
            if keyword in topic_words or keyword in (topic or "").lower():
                merged["tone"].extend(hint["tone"])
                merged["moods"].extend(hint["moods"])
                merged["energy"] = merged["energy"] or hint["energy"]
        return merged

    def score_voice(
        self, voice: Dict[str, Any], style: Dict[str, Any], topic: str
    ) -> float:
        """Return a 0-100 suitability score for one voice."""
        score = 0.0
        style_name = style["name"]

        if style_name in voice["best_for"]:
            score += 45.0

        profile_words = set(style["voice_profile"].lower().split())
        voice_words = set(" ".join(voice["tone"]).lower().split()) | {
            voice["gender"],
            voice["energy_level"],
            voice["age_range"],
        }
        score += 6.0 * len(profile_words & voice_words)

        if voice["energy_level"] == _pacing_energy(style["pacing"]):
            score += 12.0

        tone_words = set(style["tone"].lower().replace("and", " ").split())
        score += 4.0 * len(tone_words & set(" ".join(voice["emotion_range"]).lower().split()))

        hint = self._topic_hint(topic)
        if hint["tone"]:
            score += 5.0 * len(set(hint["tone"]) & set(voice["tone"]))
        if hint["moods"]:
            score += 4.0 * len(set(hint["moods"]) & set(voice["moods"]))
        if hint["energy"] and hint["energy"] == voice["energy_level"]:
            score += 8.0

        # Prefer American/British general-purpose voices when nothing else matches.
        if voice["accent"] in ("American", "British"):
            score += 2.0
        return min(score, 100.0)

    def select(self, style_name: str, topic: str = "", top_k: int = 1) -> Dict[str, Any]:
        """Return the best-matching voice profile for a style and topic."""
        ranked = self.rank(style_name, topic)
        return ranked[0][0] if ranked else get_voice(DEFAULT_VOICE)

    def rank(self, style_name: str, topic: str = "") -> List[Tuple[Dict[str, Any], float]]:
        """Return all voices ranked by score, highest first."""
        style = get_style(style_name)
        scored = [
            (voice, self.score_voice(voice, style, topic)) for voice in self.profiles.values()
        ]
        scored.sort(key=lambda item: item[1], reverse=True)
        return scored

    def tts_settings(self, style_name: str, voice_id: str) -> Dict[str, str]:
        """Return EdgeTTS rate/pitch/volume tuned to the style."""
        style = get_style(style_name)
        energy = _pacing_energy(style["pacing"])
        rate = {"high": "+12%", "medium": "+0%", "low": "-8%"}[energy]
        voice = get_voice(voice_id)
        if "soothing" in voice["emotion_range"] or style_name in ("Meditation", "ASMR"):
            rate = "-18%"
        pitch = "+0Hz"
        if style_name in ("Horror", "Cosmic Horror", "Psychological Horror", "Ghost", "Noir"):
            pitch = "-3Hz"
        elif style_name in ("Comedy", "Kids Educational", "Gaming", "Anime"):
            pitch = "+4Hz"
        return {"rate": rate, "pitch": pitch, "volume": "+0%"}


def auto_select_voice(style_name: str, topic: str = "") -> Dict[str, Any]:
    """Convenience helper returning the auto-selected voice profile."""
    return IntelligentVoiceSelector().select(style_name, topic)
