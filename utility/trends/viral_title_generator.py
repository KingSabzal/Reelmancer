"""ViralTitleGenerator: 10-15 unique, fresh title suggestions with auto-applied settings.

Uniqueness is enforced structurally: previously suggested titles are fed back to the
model as a ban list, temperature is high, unrelated trends are deliberately combined,
and any title that is too close to history is regenerated rather than kept. There is
no predefined template fallback for the suggestions themselves.
"""

from __future__ import annotations

import logging
import random
import re
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Set

from utility.publishing.algorithmic_standards import aspect_ratio_for_duration
from utility.video.caption_styles import caption_style_for_video_style
from utility.core.config_manager import get_config
from utility.audio.intelligent_voice_selector import IntelligentVoiceSelector
from utility.llm.llm_router import SmartLLMRouter, get_router
from utility.media.media_sources import music_moods_for_style
from utility.trends.trend_analyzer import TrendAnalyzer
from utility.trends.trend_cache_manager import TrendCacheManager
from utility.trends.trend_sources import fetch_all_sync
from utility.content.video_styles import VIDEO_STYLES, get_style
from utility.trends.viral_score_calculator import ViralScoreCalculator, uniqueness_score

LOGGER = logging.getLogger("viral_titles")
if not LOGGER.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("[%(name)s] %(message)s"))
    LOGGER.addHandler(_h)
LOGGER.setLevel(logging.INFO)

CATEGORY_STYLE_MAP: Dict[str, List[str]] = {
    "Technology": ["Tech Review", "AI & Tech", "Sci-Fi", "Singularity", "Robot", "Cyberpunk"],
    "Entertainment": ["Comedy", "Music Video" if "Music Video" in VIDEO_STYLES else "Cinematic",
                       "Cinematic", "Mockumentary", "Review"],
    "Science": ["Physics", "Astronomy", "Space", "Biology", "Quantum", "Deep Dive"],
    "Politics": ["News Report", "Analysis", "Alternate History", "Breakdown"],
    "Sports": ["Sports", "Countdown", "Motivational", "Breakdown"],
    "Health": ["Fitness", "Psychology", "Explainer", "How-To"],
    "Business": ["Business", "Finance", "Crypto", "Data Story", "Analysis"],
    "Culture": ["Travel Vlog", "Fashion", "Urban", "Historical", "Storytelling" if "Storytelling" in VIDEO_STYLES else "Documentary"],
    "Mystery": ["Mystery", "Lost Civilization", "UFO", "Cryptid", "True Crime"],
    "Controversy": ["True Crime", "Analysis", "Deep Dive", "News Report"],
}

CREATIVE_ANGLES = [
    "an unexpected combination of two unrelated trending topics",
    "a contrarian angle that argues against the popular take",
    "a curiosity gap that withholds the key fact until the end",
    "a bold claim that is backed by the trend data",
    "a 'what if' scenario built on a current event",
    "a hidden connection between two trends nobody has linked yet",
    "a concrete future prediction extrapolated from today's data",
    "a behind-the-scenes look at how a trending story actually happened",
    "a personal challenge framing applied to a viral topic",
    "a reversal that shows the trend means the opposite of what people think",
]

PROMPT = """You generate viral YouTube titles from live trend data. Today is {today}.

LIVE TRENDS (title | category | platforms | heat score):
{trend_block}

FORBIDDEN TITLES (already suggested previously, never repeat or paraphrase these):
{history_block}

Generate exactly {count} video title suggestions.

HARD RULES:
- Every title must be 100% original, fresh and unexpected. Never reuse a template.
- Do not produce generic, predictable or evergreen filler titles.
- Each title must be traceable to at least one of the live trends above.
- Use these creative angles, one per title where possible: {angles}
- Titles must be 35-62 characters, written in English, no clickbait lies.
- No two titles may share more than three significant words with each other.
- Vary the sentence structure across all titles; do not start two titles the same way.

For each suggestion also decide:
- category: one of Technology, Entertainment, Science, Politics, Sports, Health, Business, Culture, Mystery, Controversy
- angle: the creative angle you used
- source_trend: the exact trend title you built on
- recommended_duration_seconds: 45-90 for a fast reaction Short, 180-300 for an explainer
- topic: a one-sentence description of the video content
- keywords: 5 SEO keywords

Return strictly this JSON:
{{"suggestions": [{{"title": "...", "category": "...", "angle": "...",
  "source_trend": "...", "recommended_duration_seconds": 60, "topic": "...",
  "keywords": ["..."]}}]}}
"""


class ViralTitleGenerator:
    """Fetches trends, generates unique titles and attaches full auto-settings."""

    def __init__(
        self,
        router: Optional[SmartLLMRouter] = None,
        cache: Optional[TrendCacheManager] = None,
        config=None,
    ):
        self.config = config or get_config()
        self.router = router or get_router()
        self.cache = cache or TrendCacheManager(
            ttl_minutes=int(self.config.get("trend_cache_ttl_minutes", 60)),
            history_size=int(self.config.get("trend_history_size", 100)),
        )
        self.analyzer = TrendAnalyzer()
        self.scorer = ViralScoreCalculator()
        self.voice_selector = IntelligentVoiceSelector()
        self.random = random.Random()

    # ------------------------------------------------------------------
    def collect_trends(
        self, force_refresh: bool = False, progress: Optional[Callable[[str, str], None]] = None
    ) -> List[Dict[str, Any]]:
        """Return analyzed trends, using the 1 hour cache when possible."""
        if not force_refresh:
            cached = self.cache.get_cached_trends()
            if cached:
                LOGGER.info("Using cached trend data (%s).", self.cache.last_updated_label())
                if progress:
                    progress("cache", "using cached trends")
                return cached["analyzed"]
        raw = fetch_all_sync(progress)
        analyzed = self.analyzer.analyze(raw, limit=45)
        self.cache.save_trends(raw, analyzed)
        return analyzed

    # ------------------------------------------------------------------
    def generate(
        self,
        count: int = 12,
        force_refresh: bool = False,
        category: str = "All",
        progress: Optional[Callable[[str, str], None]] = None,
    ) -> Dict[str, Any]:
        """Full pipeline: trends -> unique titles -> viral scores -> auto settings."""
        count = max(10, min(count, 15))
        trends = self.collect_trends(force_refresh, progress)
        if category and category != "All":
            filtered = [t for t in trends if t["category"] == category]
            trends = filtered or trends
        if not trends:
            raise RuntimeError(
                "No trend data could be retrieved. Check your internet connection and retry."
            )

        if progress:
            progress("generate", "Generating viral title suggestions...")

        # Deliberate randomness: shuffle a wide slice so pairings differ every run.
        pool = trends[:30]
        self.random.shuffle(pool)
        selected = pool[: min(len(pool), 18)]
        history_titles = self.cache.history_titles()

        suggestions = self._ask_model(selected, history_titles, count)
        suggestions = self._enforce_uniqueness(suggestions, history_titles, selected, count)

        enriched = [self._enrich(item, selected, history_titles) for item in suggestions]
        enriched = [item for item in enriched if item["viral_score"] >= 40]
        enriched.sort(key=lambda item: item["viral_score"], reverse=True)

        self.cache.add_to_history(enriched)
        average_uniqueness = (
            sum(item["uniqueness"] for item in enriched) / len(enriched) if enriched else 0.0
        )
        if average_uniqueness < 70:
            LOGGER.warning(
                "Uniqueness score dropped to %.1f%%. Consider refreshing the trend scan.",
                average_uniqueness,
            )
        return {
            "suggestions": enriched,
            "trend_summary": self.analyzer.summary(trends),
            "uniqueness_average": round(average_uniqueness, 1),
            "last_updated": self.cache.last_updated_label(),
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }

    # ------------------------------------------------------------------
    def _ask_model(
        self, trends: List[Dict[str, Any]], history_titles: List[str], count: int
    ) -> List[Dict[str, Any]]:
        """Call the LLM with a high temperature for creative, unexpected output."""
        trend_block = "\n".join(
            f"- {t['title']} | {t['category']} | {', '.join(t['sources'])} | heat {t['heat_score']}"
            for t in trends
        )
        history_block = "\n".join(f"- {title}" for title in history_titles[:40]) or "- (none yet)"
        angles = "; ".join(self.random.sample(CREATIVE_ANGLES, k=min(len(CREATIVE_ANGLES), count)))

        prompt = PROMPT.format(
            today=datetime.now().strftime("%d %B %Y"),
            trend_block=trend_block,
            history_block=history_block,
            count=count,
            angles=angles,
        )
        data = self.router.complete_json(
            prompt,
            system=(
                "You are a viral content strategist who never repeats a formula. "
                "You output JSON only."
            ),
            temperature=0.9,
            required_fields=["suggestions"],
            max_tokens=5000,
        )
        return [s for s in data.get("suggestions", []) if s.get("title")]

    def _enforce_uniqueness(
        self,
        suggestions: List[Dict[str, Any]],
        history_titles: List[str],
        trends: List[Dict[str, Any]],
        count: int,
    ) -> List[Dict[str, Any]]:
        """Drop repetitive titles and ask the model again with a forced different angle."""
        kept: List[Dict[str, Any]] = []
        seen_words: List[Set[str]] = []
        for item in suggestions:
            title = str(item["title"]).strip().strip('"')
            words = {w for w in re.findall(r"[a-z']+", title.lower()) if len(w) > 3}
            if uniqueness_score(title, history_titles) < 70:
                continue
            if self._too_similar(words, seen_words):
                continue
            item["title"] = title
            kept.append(item)
            seen_words.append(words)

        if len(kept) >= count:
            return kept[:count]

        missing = count - len(kept)
        LOGGER.info("Regenerating %d suggestions to satisfy the uniqueness rule.", missing)
        banned = history_titles + [item["title"] for item in kept]
        try:
            extra = self._ask_model(
                self.random.sample(trends, k=min(len(trends), 12)), banned, missing + 3
            )
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("Uniqueness regeneration failed: %s", exc)
            extra = []
        for item in extra:
            title = str(item["title"]).strip().strip('"')
            words = {w for w in re.findall(r"[a-z']+", title.lower()) if len(w) > 3}
            if uniqueness_score(title, banned) < 70:
                continue
            if self._too_similar(words, seen_words):
                continue
            item["title"] = title
            kept.append(item)
            seen_words.append(words)
            if len(kept) >= count:
                break
        return kept[:count]

    @staticmethod
    def _too_similar(words: Set[str], seen: List[Set[str]]) -> bool:
        """Reject a title only when it substantially overlaps an accepted one."""
        for other in seen:
            union = words | other
            if not union:
                continue
            overlap = len(words & other) / len(union)
            if overlap >= 0.5 or len(words & other) >= 5:
                return True
        return False

    # ------------------------------------------------------------------
    def _pick_style(self, category: str, title: str) -> str:
        """Choose the video style from the category, biased by title vocabulary."""
        candidates = [s for s in CATEGORY_STYLE_MAP.get(category, []) if s in VIDEO_STYLES]
        if not candidates:
            candidates = ["Documentary", "Explainer", "Cinematic"]
        lowered = title.lower()
        for style_name in candidates:
            style = get_style(style_name)
            if any(word in lowered for word in style["visual_keywords"][0].split()):
                return style_name
        return self.random.choice(candidates)

    def _enrich(
        self, item: Dict[str, Any], trends: List[Dict[str, Any]], history_titles: List[str]
    ) -> Dict[str, Any]:
        """Attach viral score plus the complete auto-applied production settings."""
        title = item["title"]
        category = item.get("category", "Culture")
        source_trend = item.get("source_trend", "")

        matched = next((t for t in trends if t["title"] == source_trend), None)
        trend_keywords = (matched or {}).get("keywords", []) + [
            k.lower() for k in item.get("keywords", [])
        ]
        platform_count = (matched or {}).get("platform_count", 1)

        score = self.scorer.score(title, trend_keywords, platform_count, history_titles)

        style_name = self._pick_style(category, title)
        style = get_style(style_name)
        duration = int(item.get("recommended_duration_seconds", 60) or 60)
        duration = max(20, min(duration, 600))
        aspect = aspect_ratio_for_duration(duration)
        voice = self.voice_selector.select(style_name, item.get("topic", title))

        watermark = dict(self.config.watermark())
        settings = {
            "video_style": style_name,
            "voice_id": voice["voice_id"],
            "voice_name": voice["name"],
            "voice_description": f"{voice['name']} ({', '.join(voice['tone'][:2])}, {voice['accent']})",
            "duration_seconds": duration,
            "aspect_ratio": aspect,
            "music_mood": music_moods_for_style(style_name),
            "sfx_density": style["sfx_density"],
            "caption_style": caption_style_for_video_style(style_name, duration),
            "watermark_settings": watermark,
            "emoji_enabled": style_name in (
                "Comedy", "Gaming", "Anime", "Kids Educational", "Travel Vlog", "Fashion"
            ),
            "pattern_interrupts": True,
            "target_platforms": ["YouTube", "Instagram", "TikTok"] if duration < 120 else ["YouTube"],
        }

        return {
            "title": title,
            "topic": item.get("topic", title),
            "category": category,
            "angle": item.get("angle", ""),
            "source_trend": source_trend,
            "keywords": item.get("keywords", [])[:8],
            "viral_score": score["total"],
            "score_components": score["components"],
            "badge": score["badge"],
            "uniqueness": uniqueness_score(title, history_titles),
            "settings": settings,
        }
