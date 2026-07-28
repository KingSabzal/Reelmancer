"""Generates ready-to-upload YouTube, Instagram and TikTok packages for every video."""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

from utility.publishing.algorithmic_standards import SYNTHETIC_MEDIA_DISCLOSURE
from utility.content.creator_templates import GENERAL_RULES, template_for_style
from utility.llm.llm_router import SmartLLMRouter, get_router
from utility.publishing.platform_standards import (
    INSTAGRAM,
    TIKTOK,
    YOUTUBE,
    algospeak_advice,
    check_policy_risks,
    front_load_check,
    keyword_coverage,
    select_hashtags,
)
from utility.content.title_optimizer import TitleOptimizer, prompt_rules

LOGGER = logging.getLogger("seo_packages")
if not LOGGER.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("[%(name)s] %(message)s"))
    LOGGER.addHandler(_h)
LOGGER.setLevel(logging.INFO)

POSTING_TIMES = {
    "youtube": ["Tuesday 2-4 PM", "Thursday 5-7 PM", "Saturday 9-11 AM"],
    "instagram": ["Tuesday 11 AM-1 PM", "Thursday 7-9 PM", "Sunday 10 AM-12 PM"],
    "tiktok": ["Tuesday 9-11 AM", "Thursday 12-3 PM", "Friday 5-9 PM"],
}

PROMPT = """Create complete upload packages for this video, following 2026 SEO best practice.

TOPIC: {topic}
FINAL TITLE USED: {title}
VIDEO STYLE: {style}
DURATION: {duration} seconds ({format_label})
SCRIPT:
\"\"\"{script}\"\"\"

CREATOR MODEL TO IMITATE: {creator_name}
Title style: {creator_title_style}
Title formulas: {creator_formulas}
Thumbnail rules: {creator_thumbnail}
Description style: {creator_description}

{title_rules}

Rules:
- Provide the main YouTube title plus 5 alternatives, each using a DIFFERENT
  combination of the patterns above so they can be compared and A/B tested.
- Also provide "shorts_title": a {shorts_min}-{shorts_max} character version for the
  Shorts feed, which truncates earlier than search.
- Also provide "primary_keyword": the single keyword the title is built around.
- YouTube description: 1500-3000 characters, first 2 lines are a hook and summary,
  then timestamps, key points, resources, social links placeholder, subscribe CTA
  and 3-5 hashtags. Keep the main keyword density at 2-3%.
- 10-15 YouTube tags. Tags lost most of their ranking weight after 2019, so a
  focused set beats a long one. Include a few long-tail phrases.
- Thumbnail text: 3-5 words, bold, emotional trigger.
- Pinned comment: engaging question plus extra value.
- Community post: teaser plus a poll with exactly 4 options.
- Instagram caption: the first 125 characters must contain the hook AND the main
  keyword, because that is where Instagram truncates and because keyword-rich
  captions now drive roughly 30% more reach than hashtags do. Then 400-1200
  characters of substance. Strategic emoji, not decorative.
- Instagram hashtags: EXACTLY 5 or fewer. Instagram enforced a hard 5-tag cap in
  December 2025 and strips or blocks anything above it. Choose specific,
  descriptive tags. Never use #fyp, #viral, #love, #instagood or similar mega-tags:
  they are on billions of posts and give the algorithm no information.
- Instagram alt text for accessibility, under 100 characters.
- TikTok caption: 50-150 characters. Only about 80 characters show in the feed, so
  front-load the hook. Include the topic keyword naturally, because TikTok search
  now drives a meaningful share of views.
- TikTok hashtags: 3-5 specific, descriptive tags. Do NOT include #fyp or #viral:
  TikTok has confirmed they do not push content to the For You feed.
- All output in English.

Return strictly this JSON:
{{
 "youtube": {{"title": "...", "alt_titles": ["...","...","...","...","..."],
   "shorts_title": "...", "primary_keyword": "...", "description": "...",
   "tags": ["..."], "thumbnail_text": "...", "pinned_comment": "...",
   "community_post": {{"text": "...", "poll": ["...","...","...","..."]}},
   "chapters": [{{"time": "0:00", "label": "..."}}], "category": "...",
   "keywords": ["..."]}},
 "instagram": {{"hook_line": "...", "caption": "...",
   "hashtags": ["...", "...", "..."],
   "cover_text": "...", "alt_text": "...", "location_tag": "...", "tagged_accounts": ["..."]}},
 "tiktok": {{"hook_line": "...", "caption": "...", "hashtags": ["..."],
   "cover_text": "...", "sound_suggestion": "..."}}
}}
"""


class SEOPackageGenerator:
    """Builds the three platform packages and post-processes them deterministically."""

    def __init__(self, router: Optional[SmartLLMRouter] = None):
        self.router = router or get_router()

    def generate(
        self,
        topic: str,
        title: str,
        script: str,
        style: str,
        duration_seconds: float,
        keywords: Optional[List[str]] = None,
        channel_handle: str = "@YourChannel",
        niche: str = "",
    ) -> Dict[str, Any]:
        """Generate the YouTube, Instagram and TikTok packages."""
        template = template_for_style(style)
        primary_keyword = (keywords or [topic])[0] if (keywords or topic) else ""
        format_label = "Shorts / vertical" if duration_seconds < 120 else "Long-form / horizontal"
        shorts_optimizer = TitleOptimizer(niche=niche or style, is_short=True)
        shorts_min, shorts_max = shorts_optimizer.target_range()

        prompt = PROMPT.format(
            title_rules=prompt_rules(niche or style, duration_seconds < 120, primary_keyword),
            shorts_min=shorts_min,
            shorts_max=shorts_max,
            topic=topic,
            title=title,
            style=style,
            duration=int(duration_seconds),
            format_label=format_label,
            script=script[:4000],
            creator_name=template["name"],
            creator_title_style=template["title_style"],
            creator_formulas=" | ".join(template["title_formulas"]),
            creator_thumbnail=template["thumbnail_rules"],
            creator_description=template["description_style"],
        )
        try:
            data = self.router.complete_json(
                prompt,
                system="You are a senior YouTube, Instagram and TikTok SEO strategist. Output JSON only.",
                temperature=0.75,
                required_fields=["youtube", "instagram", "tiktok"],
                max_tokens=6000,
            )
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("LLM package generation failed (%s); building a structural package.", exc)
            data = self._structural_package(topic, title, script, keywords or [])

        return self._post_process(
            data, topic, title, duration_seconds, keywords or [], channel_handle, style,
            niche=niche or style, primary_keyword=primary_keyword,
        )

    # ------------------------------------------------------------------
    def _structural_package(
        self, topic: str, title: str, script: str, keywords: List[str]
    ) -> Dict[str, Any]:
        """Deterministic package built from the actual script when the LLM is unreachable."""
        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", script) if s.strip()]
        summary = " ".join(sentences[:2])
        body = "\n\n".join(f"- {s}" for s in sentences[:8])
        keywords = keywords or re.findall(r"[A-Za-z]{5,}", topic)[:8]
        return {
            "youtube": {
                "title": title[:60],
                "alt_titles": [title[:60], f"{topic}: What Nobody Tells You", f"The Truth About {topic}"],
                "description": f"{summary}\n\n{body}\n\nKey points:\n{body}",
                "tags": keywords,
                "thumbnail_text": " ".join(topic.split()[:3]).upper(),
                "pinned_comment": f"What surprised you most about {topic}? Tell me below.",
                "community_post": {
                    "text": f"New video on {topic}. What should I cover next?",
                    "poll": ["More detail", "A case study", "A shorter version", "A different topic"],
                },
                "chapters": [{"time": "0:00", "label": "Hook"}],
                "category": "Education",
                "keywords": keywords,
            },
            "instagram": {
                "hook_line": sentences[0] if sentences else topic,
                "caption": f"{summary}\n\n{body}",
                "hashtags": {"medium": [], "high_reach": [], "niche": [], "branded": []},
                "cover_text": " ".join(topic.split()[:3]).upper(),
                "alt_text": f"Short video about {topic}.",
                "location_tag": "",
                "tagged_accounts": [],
            },
            "tiktok": {
                "hook_line": sentences[0] if sentences else topic,
                "caption": (sentences[0] if sentences else topic)[:200],
                "hashtags": ["#fyp", "#foryou", "#viral"],
                "cover_text": " ".join(topic.split()[:3]).upper(),
                "sound_suggestion": "Original sound",
            },
        }

    # ------------------------------------------------------------------
    def _post_process(
        self,
        data: Dict[str, Any],
        topic: str,
        title: str,
        duration_seconds: float,
        keywords: List[str],
        channel_handle: str,
        style: str,
        niche: str = "",
        primary_keyword: str = "",
    ) -> Dict[str, Any]:
        """Enforce hard limits, add disclosure, settings and posting times."""
        youtube = dict(data.get("youtube", {}))
        instagram = dict(data.get("instagram", {}))
        tiktok = dict(data.get("tiktok", {}))
        is_short = duration_seconds < 120

        # --- YouTube ---
        # Pick the strongest title by scoring every candidate against the 2026
        # patterns instead of blindly truncating to 60 characters.
        optimizer = TitleOptimizer(niche=niche or style, is_short=is_short)
        candidates = [str(youtube.get("title", title))]
        candidates += [str(t) for t in youtube.get("alt_titles", [])]
        candidates = [c for c in candidates if c.strip()]

        best, all_scores = optimizer.best_of(candidates, primary_keyword)
        if best:
            youtube["title"] = best
            youtube["alt_titles"] = [s.title for s in all_scores[1:6]]
        else:
            youtube["title"] = optimizer.enforce_limits(str(youtube.get("title", title)))
            youtube["alt_titles"] = []

        winner = all_scores[0] if all_scores else optimizer.score(youtube["title"], primary_keyword)
        youtube["title_analysis"] = winner.as_dict()
        youtube["title_alternatives_scored"] = [s.as_dict() for s in all_scores[:6]]
        youtube["primary_keyword"] = primary_keyword

        # Shorts feed truncates earlier, so keep a dedicated short version.
        shorts_optimizer = TitleOptimizer(niche=niche or style, is_short=True)
        shorts_title = str(youtube.get("shorts_title", "")).strip()
        if not shorts_title:
            low, high = shorts_optimizer.target_range()
            shorts_title = youtube["title"] if len(youtube["title"]) <= high else ""
            if not shorts_title:
                trimmed = youtube["title"][:high]
                shorts_title = trimmed[: trimmed.rfind(" ")] if " " in trimmed else trimmed
        youtube["shorts_title"] = shorts_optimizer.enforce_limits(shorts_title)
        youtube["shorts_title_analysis"] = shorts_optimizer.score(
            youtube["shorts_title"], primary_keyword
        ).as_dict()
        tags = [str(t).strip("# ") for t in youtube.get("tags", []) if str(t).strip()]
        tag_stopwords = {"and", "the", "for", "with", "that", "this", "from", "your", "you"}
        topic_words = [
            w for w in re.findall(r"[A-Za-z][A-Za-z0-9]{2,}", topic)
            if w.lower() not in tag_stopwords
        ]
        topic_short = " ".join(topic_words[:4]).lower() or topic.lower()
        long_tail = [
            f"how to {topic_short}", f"best {topic_short} 2026", f"{topic_short} explained",
            f"{topic_short} 2026", f"{topic_short} guide", f"{topic_short} tips",
            f"what is {topic_short}", f"{topic_short} for beginners", f"{topic_short} facts",
            f"{style.lower()} {topic_short}", f"{topic_short} breakdown", f"{topic_short} analysis",
        ]
        secondary = [k.lower() for k in keywords] + [w.lower() for w in topic_words]
        for extra in secondary + long_tail:
            if len(tags) >= YOUTUBE["tags_recommended"][1]:
                break
            if extra and extra.lower() not in [t.lower() for t in tags]:
                tags.append(extra)
        # Tags lost most of their ranking weight after 2019, and the field is capped
        # at 500 characters. A focused set beats padding to the old 30-tag advice.
        selected_tags: List[str] = []
        used_chars = 0
        for tag in tags:
            cost = len(tag) + 1
            if used_chars + cost > YOUTUBE["tags_total_chars"]:
                break
            selected_tags.append(tag)
            used_chars += cost
            if len(selected_tags) >= YOUTUBE["tags_recommended"][1]:
                break
        youtube["tags"] = selected_tags
        youtube["tags_char_count"] = used_chars

        description = str(youtube.get("description", "")).strip()
        hashtags = [f"#{re.sub(r'[^A-Za-z0-9]', '', w)}" for w in topic.split()[:3] if len(w) > 3]
        hashtags += ["#Shorts"] if is_short else ["#YouTube"]
        footer = (
            f"\n\nSubscribe for more: {channel_handle}\n"
            f"{' '.join(hashtags[:5])}\n\n{SYNTHETIC_MEDIA_DISCLOSURE}"
        )
        if len(description) + len(footer) < GENERAL_RULES["description_length"][0]:
            description += "\n\nWhat you will learn:\n" + "\n".join(
                f"- {kw}" for kw in (keywords or tags)[:6]
            )
        youtube["description"] = (description + footer)[:4900]
        thumb = " ".join(str(youtube.get("thumbnail_text", topic)).split()[:5])
        # The title and thumbnail are read as one unit; repeating the same words
        # wastes the thumbnail. Flag heavy overlap so the user can fix it.
        title_words = {w.lower().strip(".,!?:") for w in youtube["title"].split() if len(w) > 3}
        thumb_words = {w.lower().strip(".,!?:") for w in thumb.split() if len(w) > 3}
        overlap = title_words & thumb_words
        youtube["thumbnail_text"] = thumb
        youtube["thumbnail_overlaps_title"] = len(overlap) >= 2
        if len(overlap) >= 2:
            youtube["thumbnail_warning"] = (
                "The thumbnail text repeats the title (" + ", ".join(sorted(overlap))
                + "). The thumbnail should add what the title cannot say."
            )
        youtube["settings"] = {
            "category": youtube.get("category", "Education"),
            "language": "English",
            "license": "Standard YouTube License",
            "made_for_kids": False,
            "age_restriction": False,
            "synthetic_media_disclosure": True,
            "format": "Shorts (9:16)" if is_short else "Long-form (16:9)",
        }
        youtube["end_screen_elements"] = [
            "Subscribe button",
            "Best for viewer video",
            "Most recent upload",
            f"Playlist: {style} series",
        ]
        youtube["cards"] = [
            {"time": "0:15", "target": "Related video on the same topic"},
            {"time": f"0:{int(min(duration_seconds * 0.6, 59)):02d}", "target": "Playlist"},
        ]
        youtube["recommended_posting_times"] = POSTING_TIMES["youtube"]
        youtube["synthetic_media_disclosure"] = SYNTHETIC_MEDIA_DISCLOSURE

        # --- Instagram ---
        # Instagram enforced a hard 5-hashtag cap in December 2025. Anything above
        # it is stripped or blocks the post, and stacking generic tags now reads as
        # low-intent content. Hashtags no longer drive reach; keyword-rich captions do.
        groups = instagram.get("hashtags", {}) or {}
        raw_tags: List[str] = []
        if isinstance(groups, list):
            raw_tags = [str(t) for t in groups]
            groups = {}
        else:
            for key in ("medium", "high_reach", "niche", "branded"):
                raw_tags.extend(str(t) for t in (groups.get(key, []) or []))
        raw_tags.extend(str(k) for k in keywords)
        raw_tags.append("".join(w for w in topic.split()[:2]))

        ig_tags, ig_rejected = select_hashtags(
            raw_tags, topic, keywords, limit=INSTAGRAM["hashtag_hard_cap"], platform="instagram"
        )
        instagram["hashtags"] = ig_tags
        instagram["hashtag_rejected"] = ig_rejected
        instagram["hashtag_policy"] = (
            f"Instagram caps hashtags at {INSTAGRAM['hashtag_hard_cap']} since December 2025. "
            "Extra tags are stripped or block the post."
        )

        caption = str(instagram.get("caption", "")).strip()
        # Keywords in the caption are what actually drive discovery now, so verify
        # they are present and land before the 125-character truncation point.
        ig_front = front_load_check(caption, INSTAGRAM["caption_visible"])
        ig_keywords = keyword_coverage(caption, [primary_keyword] + list(keywords)[:4])
        instagram["caption_analysis"] = {**ig_front, **ig_keywords}
        if not ig_keywords["keywords_early"]:
            instagram["caption_warning"] = (
                "No keyword appears in the first 125 characters. Instagram indexes "
                "caption text for discovery, and that is where the caption truncates."
            )

        instagram["caption"] = (
            caption + ("\n.\n.\n.\n" + " ".join(ig_tags) if ig_tags else "")
        )[: INSTAGRAM["caption_max"]]
        instagram["cover_text"] = " ".join(str(instagram.get("cover_text", topic)).split()[:5])
        instagram["alt_text"] = str(instagram.get("alt_text", ""))[: INSTAGRAM["alt_text_max"]]
        instagram["aspect_ratio"] = "9:16"
        instagram["recommended_posting_times"] = POSTING_TIMES["instagram"]
        instagram["cross_promotion"] = {"share_to_story": True, "share_to_facebook": True, "share_to_threads": True}
        instagram["synthetic_media_disclosure"] = SYNTHETIC_MEDIA_DISCLOSURE

        # --- TikTok ---
        # TikTok has confirmed #fyp and #viral do not push content to the For You
        # feed: they sit on hundreds of billions of videos, so they carry no signal.
        # Specific descriptive tags are what the algorithm can actually use.
        tt_candidates = [str(t) for t in tiktok.get("hashtags", []) if str(t).strip()]
        tt_candidates.extend(str(k) for k in keywords)
        tt_tags, tt_rejected = select_hashtags(
            tt_candidates, topic, keywords,
            limit=TIKTOK["hashtag_recommended"][1], platform="tiktok",
        )
        tiktok["hashtags"] = tt_tags
        tiktok["hashtag_rejected"] = tt_rejected
        tiktok["hashtag_policy"] = (
            "3-5 specific tags. #fyp and #viral are excluded because TikTok has "
            "confirmed they do not drive For You feed placement."
        )

        tiktok_caption = str(tiktok.get("caption", "")).strip()
        # Only ~80 characters show in the feed, so the hook must land first.
        tt_front = front_load_check(tiktok_caption, TIKTOK["caption_visible"])
        tiktok["caption_analysis"] = tt_front
        if not tt_front["hook_complete"]:
            tiktok["caption_warning"] = (
                "The hook does not complete within the first 80 characters, which is "
                "all TikTok shows in the feed before the 'more' link."
            )
        low, high = TIKTOK["caption_engagement_target"]
        body = tiktok_caption[:high]
        tiktok["caption"] = (body + (" " + " ".join(tt_tags) if tt_tags else ""))[
            : TIKTOK["caption_max"]
        ]
        tiktok["cover_text"] = " ".join(str(tiktok.get("cover_text", topic)).split()[:5])
        tiktok["sound"] = {
            "original_sound": True,
            "trending_suggestion": tiktok.get("sound_suggestion", "Use a trending low-volume beat under the voiceover"),
        }
        tiktok["settings"] = {
            "allow_duet": True,
            "allow_stitch": True,
            "allow_comments": True,
            "add_to_series": duration_seconds >= 120,
            "ai_generated_content_label": True,
        }
        tiktok["recommended_posting_times"] = POSTING_TIMES["tiktok"]
        tiktok["cross_promotion"] = {"share_to_instagram": True, "share_to_youtube_shorts": True}

        # The first 125 characters of a YouTube description show in search results.
        desc_front = front_load_check(youtube["description"], YOUTUBE["description_visible"])
        youtube["description_analysis"] = {
            **desc_front,
            **keyword_coverage(
                youtube["description"][: YOUTUBE["description_visible"]],
                [primary_keyword] + list(keywords)[:4],
            ),
            "length": len(youtube["description"]),
            "target": list(YOUTUBE["description_target"]),
        }

        # Documented policy rules only. Words like "war" or "death" are deliberately
        # not flagged: YouTube's January 2026 update allows non-graphic coverage of
        # sensitive subjects, so censoring them would cost credibility for nothing.
        risks = check_policy_risks(
            title=youtube["title"],
            description=youtube["description"],
            thumbnail_text=youtube.get("thumbnail_text", ""),
        )
        policy = {
            "risks": [r.as_dict() for r in risks],
            "blocking": any(r.severity == "blocking" for r in risks),
            "note": algospeak_advice(),
        }
        youtube["policy_check"] = policy

        return {"youtube": youtube, "instagram": instagram, "tiktok": tiktok,
                "policy_check": policy}

    @staticmethod
    def to_text(package: Dict[str, Any]) -> str:
        """Flatten a package to plain text for download."""
        return json.dumps(package, indent=2, ensure_ascii=False)
