"""2026 script generation engine: Hook, Open Loop, density, pattern interrupt cues, CTA."""

from __future__ import annotations

import re
from typing import Any, Dict, List

from utility.publishing.algorithmic_standards import aspect_ratio_for_duration, word_count_for_duration
from utility.content.script_standards import (
    SCRIPT_FORMATS,
    build_structure_block,
    choose_format,
    is_short,
    output_fields,
    remember_format,
)
from utility.llm.llm_router import SmartLLMRouter, get_router
from utility.content.video_styles import get_style

SYSTEM_PROMPT = (
    "You are an elite YouTube scriptwriter working to 2026 standards. You know that "
    "the ranking model now estimates viewer satisfaction rather than counting minutes, "
    "so you never pad. You always answer with a single valid JSON object and nothing else."
)

SOURCE_SYSTEM_PROMPT = (
    "You are a factual video scriptwriter working to 2026 YouTube standards. You never "
    "invent facts, you never pad to reach a length, and you always answer with a single "
    "valid JSON object and nothing else."
)

PROMPT_TEMPLATE = """Write a video script about: {topic}

STYLE: {style_name}
TONE: {tone}
PACING: {pacing}
TARGET DURATION: {duration} seconds ({format_lane})
ASPECT RATIO: {aspect_ratio}

{structure}

ALSO PRODUCE:
- title: under 60 characters, curiosity-driven, and honest about what the script delivers.
- search_queries: {query_count} short visual search phrases (2-4 words each) that combine
  the subject with these style visuals: {visual_keywords}. They must be concrete, filmable
  and in English. Each phrase must name something that can actually be seen.
- keywords: 8 SEO keywords for this video.

Return strictly this JSON shape and nothing else:
{output_shape}
"""


SOURCE_PROMPT_TEMPLATE = """Write a video script based ONLY on the source material below.

{source_material}

STYLE: {style_name}
TONE: {tone}
PACING: {pacing}
TARGET DURATION: {duration} seconds ({format_lane})
ASPECT RATIO: {aspect_ratio}

CRITICAL SOURCING RULES:
- Use ONLY facts, numbers, names and quotes that appear in the source material above.
- Never invent statistics, dates, studies, causes or outcomes. If the source does not
  say it, do not say it, even if you believe it to be true.
- Do not present speculation as fact. If the source is uncertain, say so in the script.
- Attribute central claims naturally in the narration, for example "according to {site}".
- If the material will not fill the target length, write a shorter script. Never pad
  with invention. Framing and transitions are fine as long as they add no new claim.

{structure}

ALSO PRODUCE:
- title: under 60 characters, curiosity-driven, and faithful to the source.
- search_queries: {query_count} short visual search phrases (2-4 words each) describing
  concrete, filmable things actually mentioned in the source. Where it fits, combine them
  with these style visuals: {visual_keywords}
- keywords: 8 SEO keywords.
- key_facts: the 3-5 specific facts from the source that the script relies on.

Return strictly this JSON shape and nothing else:
{output_shape}
"""


def _avg_sentence_words(script: str) -> float:
    """Average sentence length, used to verify the sub-15-word guideline."""
    sentences = [s for s in re.split(r"(?<=[.!?])\s+", script or "") if s.strip()]
    if not sentences:
        return 0.0
    return round(sum(len(s.split()) for s in sentences) / len(sentences), 1)


def clean_markdown(text: str) -> str:
    """Strip markdown so TTS never reads formatting characters aloud."""
    text = re.sub(r"\*\*(.*?)\*\*", r"\1", text)
    text = re.sub(r"\*(.*?)\*", r"\1", text)
    text = re.sub(r"_(.*?)_", r"\1", text)
    text = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
    text = re.sub(r"`(.*?)`", r"\1", text)
    text = re.sub(r"^#+\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"\[(.*?)\]\((.*?)\)", r"\1", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def extract_cues(script: str) -> List[int]:
    """Return the word indices where [CUE] markers appear."""
    cues: List[int] = []
    words = script.split()
    counter = 0
    for word in words:
        if "[CUE]" in word.upper():
            cues.append(counter)
        else:
            counter += 1
    return cues


def strip_cues(script: str) -> str:
    """Remove [CUE] markers so they are never spoken."""
    return re.sub(r"\s*\[CUE\]\s*", " ", script, flags=re.IGNORECASE).strip()


class ScriptGenerator:
    """Generates a structured, validated 2026-standard script."""

    def __init__(self, router: SmartLLMRouter | None = None):
        self.router = router or get_router()

    def generate(
        self,
        topic: str,
        style_name: str = "Cinematic",
        duration_seconds: int = 60,
        script_format: str | None = None,
    ) -> Dict[str, Any]:
        """Generate a script package for a topic, style and duration."""
        style = get_style(style_name)
        word_count = word_count_for_duration(duration_seconds)
        aspect_ratio = aspect_ratio_for_duration(duration_seconds)
        query_count = max(6, int(duration_seconds / 4))
        format_key = choose_format(style_name, duration_seconds, script_format)

        prompt = PROMPT_TEMPLATE.format(
            topic=topic,
            style_name=style["name"],
            tone=style["tone"],
            pacing=style["pacing"],
            duration=duration_seconds,
            aspect_ratio=aspect_ratio,
            format_lane="vertical Short" if is_short(duration_seconds) else "long-form",
            structure=build_structure_block(duration_seconds, word_count, format_key),
            output_shape=output_fields(duration_seconds),
            query_count=query_count,
            visual_keywords=", ".join(style["visual_keywords"]),
        )

        data = self.router.complete_json(
            prompt,
            system=SYSTEM_PROMPT,
            temperature=0.8,
            required_fields=["script", "title", "search_queries"],
            retries=3,
            max_tokens=6000,
        )
        remember_format(format_key)

        raw_script = str(data.get("script", ""))
        cue_positions = extract_cues(raw_script)
        script = clean_markdown(strip_cues(raw_script))

        queries = [str(q).strip() for q in data.get("search_queries", []) if str(q).strip()]
        queries = self._enrich_queries(queries, topic, style, query_count)

        return {
            "topic": topic,
            "style": style["name"],
            "title": clean_markdown(str(data.get("title", topic)))[:100],
            "script": script,
            "hook": clean_markdown(str(data.get("hook", ""))),
            "open_loop": clean_markdown(str(data.get("open_loop", ""))),
            "payoff": clean_markdown(str(data.get("payoff", ""))),
            "cta": clean_markdown(str(data.get("cta", ""))),
            "search_queries": queries,
            "keywords": [str(k) for k in data.get("keywords", [])][:12],
            "retention_trap": clean_markdown(str(data.get("retention_trap", ""))),
            "screen_text": [str(t) for t in data.get("screen_text", [])][:12],
            "loop_line": clean_markdown(str(data.get("loop_line", ""))),
            "next_hook": clean_markdown(str(data.get("next_hook", ""))),
            "script_format": format_key,
            "script_format_label": SCRIPT_FORMATS[format_key]["label"],
            "is_short": is_short(duration_seconds),
            "avg_sentence_words": _avg_sentence_words(script),
            "cue_positions": cue_positions,
            "word_count_target": word_count,
            "word_count_actual": len(script.split()),
            "duration_seconds": duration_seconds,
            "aspect_ratio": aspect_ratio,
            "model_used": self.router.last_used_model,
        }

    @staticmethod
    def _enrich_queries(
        queries: List[str], topic: str, style: Dict[str, Any], needed: int
    ) -> List[str]:
        """Combine topic with style visual keywords; never use unrelated filler queries."""
        topic_core = " ".join(re.findall(r"[A-Za-z]+", topic)[:3]).strip() or style["name"]
        enriched = [f"{q}" for q in queries]
        for keyword in style["visual_keywords"]:
            enriched.append(f"{topic_core} {keyword}")
        for keyword in style["visual_keywords"]:
            enriched.append(keyword)
        seen: set[str] = set()
        unique: List[str] = []
        for query in enriched:
            key = query.lower().strip()
            if key and key not in seen:
                seen.add(key)
                unique.append(query.strip())
        return unique[: max(needed, 6)]


    def generate_from_source(
        self,
        source_material: str,
        style_name: str = "Documentary",
        duration_seconds: int = 60,
        site: str = "the source",
        fallback_topic: str = "",
        script_format: str | None = None,
    ) -> Dict[str, Any]:
        """Generate a script grounded in supplied source material (an article or page)."""
        style = get_style(style_name)
        word_count = word_count_for_duration(duration_seconds)
        aspect_ratio = aspect_ratio_for_duration(duration_seconds)
        query_count = max(6, int(duration_seconds / 4))
        format_key = choose_format(style_name, duration_seconds, script_format)

        prompt = SOURCE_PROMPT_TEMPLATE.format(
            source_material=source_material[:9000],
            style_name=style["name"],
            tone=style["tone"],
            pacing=style["pacing"],
            duration=duration_seconds,
            aspect_ratio=aspect_ratio,
            format_lane="vertical Short" if is_short(duration_seconds) else "long-form",
            structure=build_structure_block(
                duration_seconds, word_count, format_key, sourced=True
            ),
            output_shape=output_fields(duration_seconds, sourced=True),
            query_count=query_count,
            visual_keywords=", ".join(style["visual_keywords"]),
            site=site,
        )

        data = self.router.complete_json(
            prompt,
            system=SOURCE_SYSTEM_PROMPT,
            temperature=0.7,
            required_fields=["script", "title", "search_queries"],
            retries=3,
            max_tokens=6000,
        )
        remember_format(format_key)

        raw_script = str(data.get("script", ""))
        cue_positions = extract_cues(raw_script)
        script = clean_markdown(strip_cues(raw_script))
        topic = fallback_topic or str(data.get("title", ""))

        queries = [str(q).strip() for q in data.get("search_queries", []) if str(q).strip()]
        queries = self._enrich_queries(queries, topic, style, query_count)

        return {
            "topic": topic,
            "style": style["name"],
            "title": clean_markdown(str(data.get("title", topic)))[:100],
            "script": script,
            "hook": clean_markdown(str(data.get("hook", ""))),
            "open_loop": clean_markdown(str(data.get("open_loop", ""))),
            "payoff": clean_markdown(str(data.get("payoff", ""))),
            "cta": clean_markdown(str(data.get("cta", ""))),
            "search_queries": queries,
            "keywords": [str(k) for k in data.get("keywords", [])][:12],
            "key_facts": [str(f) for f in data.get("key_facts", [])][:6],
            "retention_trap": clean_markdown(str(data.get("retention_trap", ""))),
            "screen_text": [str(t) for t in data.get("screen_text", [])][:12],
            "loop_line": clean_markdown(str(data.get("loop_line", ""))),
            "next_hook": clean_markdown(str(data.get("next_hook", ""))),
            "script_format": format_key,
            "script_format_label": SCRIPT_FORMATS[format_key]["label"],
            "is_short": is_short(duration_seconds),
            "avg_sentence_words": _avg_sentence_words(script),
            "cue_positions": cue_positions,
            "word_count_target": word_count,
            "word_count_actual": len(script.split()),
            "duration_seconds": duration_seconds,
            "aspect_ratio": aspect_ratio,
            "model_used": self.router.last_used_model,
        }


def generate_script(topic: str, style_name: str = "Cinematic", duration_seconds: int = 60) -> Dict[str, Any]:
    """Module-level convenience wrapper around ScriptGenerator.generate()."""
    return ScriptGenerator().generate(topic, style_name, duration_seconds)
