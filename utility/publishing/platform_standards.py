"""Platform publishing rules verified against 2026 policy.

The most consequential change: Instagram enforced a hard **5 hashtag cap** in
December 2025. It is a platform limit, not advice. Posts with more than five
hashtags are either blocked at publish time or have the extras stripped, and
Instagram has said that stacking generic tags now reads as low-intent content and
reduces reach.

Other verified changes reflected here:

* Adam Mosseri has repeatedly stated hashtags do not boost reach. Keyword-rich
  captions replaced them as the discovery mechanism, measuring roughly 30% more
  reach in 2026 testing.
* TikTok's #fyp / #viral tags carry no information for the algorithm because
  hundreds of billions of videos use them.
* YouTube tags lost most of their weight after 2019; 10-15 relevant tags is enough.
* YouTube removed the "profanity in the first 7 seconds" penalty in July 2025, but
  profanity in a title or thumbnail still limits or removes ads, and slurs
  demonetise completely with no bleeping exemption.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

# ----------------------------------------------------------------------
# Hard limits per platform
# ----------------------------------------------------------------------
YOUTUBE = {
    "title_max": 100,
    "description_max": 5000,
    "description_visible": 125,      # what shows in search before the fold
    "description_target": (1500, 3000),
    "tags_total_chars": 500,
    "tags_recommended": (10, 15),    # tags lost most of their weight after 2019
    "tags_hard_max": 30,
    "hashtags_in_description": (3, 5),
}

INSTAGRAM = {
    "caption_max": 2200,
    "caption_visible": 125,
    "caption_engagement_target": (80, 300),   # short captions win on engagement
    "caption_education_target": (800, 2000),  # long captions win on dwell time
    # Hard platform cap since December 2025. Exceeding it blocks the post.
    "hashtag_hard_cap": 5,
    "hashtag_recommended": (3, 5),
    "alt_text_max": 100,
}

TIKTOK = {
    "caption_max": 4000,
    "caption_visible": 80,           # only 1-2 lines show in the feed
    "caption_engagement_target": (50, 150),
    "caption_seo_target": (300, 800),
    "hashtag_recommended": (3, 5),
    "hashtag_hard_max": 5,
    "disclosure_grace_hours": 24,    # undisclosed commercial content leaves the FYF
}

# Tags that tell the algorithm nothing because they are on billions of videos.
USELESS_HASHTAGS = {
    "fyp", "foryou", "foryoupage", "fypage", "fypシ", "fypツ", "viral", "viralvideo",
    "trending", "trend", "explore", "explorepage", "instagood", "love", "follow",
    "like4like", "followforfollow", "l4l", "f4f", "photooftheday", "picoftheday",
    "instadaily", "reels", "reelsinstagram", "reelsvideo", "viralreels", "tiktok",
    "video", "new", "best", "amazing", "nice", "cool", "fun",
}

# Hashtags that platforms have historically restricted or banned. Using one can
# quietly exclude a post from hashtag pages and search.
RISKY_HASHTAGS = {
    "adulting", "alone", "assundos", "beautyblogger", "bikinibody", "boho",
    "brain", "costumes", "curvygirls", "date", "desk", "dating", "direct",
    "dm", "elevator", "eggplant", "girlsonly", "graffitiigers", "happythanksgiving",
    "hardworkpaysoff", "hotweather", "hustler", "ice", "iphonegraphy", "kansas",
    "kickoff", "killingit", "leanin", "lean", "master", "mustfollow", "nasty",
    "newyearsday", "nude", "petite", "pornfood", "pushups", "single",
    "singlelife", "skype", "snap", "snapchat", "streetphoto", "sunbathing",
    "swole", "tag4like", "tagsforlikes", "teen", "teens", "thought", "todayimwearing",
    "undies", "valentinesday", "woman", "workflow", "besties", "bikinibody",
}


# ----------------------------------------------------------------------
# Hashtag handling
# ----------------------------------------------------------------------
def normalise_hashtag(tag: str) -> str:
    """Turn any input into a clean, valid hashtag."""
    cleaned = re.sub(r"[^A-Za-z0-9_]", "", str(tag or ""))
    return f"#{cleaned}" if cleaned else ""


def hashtag_body(tag: str) -> str:
    """The hashtag text without the leading hash, lowercased."""
    return re.sub(r"[^a-z0-9_]", "", str(tag or "").lower())


def score_hashtag(tag: str, topic_words: List[str]) -> float:
    """Rank a hashtag by how much information it gives the algorithm.

    Specific, descriptive tags classify content precisely. Generic mega-tags are
    on billions of posts and are effectively noise.
    """
    body = hashtag_body(tag)
    if not body:
        return -100.0
    score = 0.0

    if body in USELESS_HASHTAGS:
        return -50.0
    if body in RISKY_HASHTAGS:
        return -40.0

    # Specificity: longer, multi-concept tags carry more meaning. Very short tags
    # ("#me", "#sea") classify nothing and are rejected outright.
    if len(body) < 5:
        return -30.0
    score += min(len(body), 24) * 0.6

    # Relevance to the actual topic.
    for word in topic_words:
        word = word.lower().strip()
        if len(word) > 3 and word in body:
            score += 12.0
            break

    # Compound tags ("deepseamapping") are more precise than single words.
    if len(body) >= 12:
        score += 4.0
    return score


def select_hashtags(
    candidates: List[str],
    topic: str,
    keywords: Optional[List[str]] = None,
    limit: int = 5,
    platform: str = "instagram",
) -> Tuple[List[str], List[str]]:
    """Choose the best hashtags within the platform cap.

    Returns (selected, rejected_reasons).
    """
    topic_words = [w for w in re.findall(r"[A-Za-z]{4,}", topic or "")]
    topic_words += [str(k) for k in (keywords or [])]

    seen: set = set()
    scored: List[Tuple[float, str]] = []
    rejected: List[str] = []

    for raw in candidates:
        tag = normalise_hashtag(raw)
        if not tag:
            continue
        body = hashtag_body(tag)
        if body in seen:
            continue
        seen.add(body)

        value = score_hashtag(tag, topic_words)
        if body in USELESS_HASHTAGS:
            rejected.append(f"{tag} is on billions of posts and carries no signal")
            continue
        if body in RISKY_HASHTAGS:
            rejected.append(f"{tag} is a historically restricted tag")
            continue
        if value < 0:
            rejected.append(f"{tag} is too short or generic to classify the content")
            continue
        scored.append((value, tag))

    scored.sort(key=lambda item: item[0], reverse=True)
    # Only keep tags that actually carry information; a half-filled set of strong
    # tags beats padding to the cap with noise.
    selected = [tag for _score, tag in scored[:limit]]

    # Guarantee at least one tag by falling back to the topic itself.
    if not selected and topic_words:
        fallback = normalise_hashtag("".join(topic_words[:2]))
        if fallback:
            selected = [fallback]
    return selected, rejected


# ----------------------------------------------------------------------
# Caption handling
# ----------------------------------------------------------------------
def front_load_check(text: str, visible_chars: int) -> Dict[str, Any]:
    """Check whether the hook survives the platform's truncation point."""
    text = (text or "").strip()
    visible = text[:visible_chars]
    complete = bool(re.search(r"[.!?]", visible)) or len(text) <= visible_chars
    return {
        "visible": visible,
        "truncated": len(text) > visible_chars,
        "hook_complete": complete,
        "visible_chars": visible_chars,
    }


def keyword_coverage(text: str, keywords: List[str]) -> Dict[str, Any]:
    """How well the caption carries the keywords that now drive discovery."""
    lowered = (text or "").lower()
    present = [k for k in keywords if k and k.lower() in lowered]
    early = [k for k in present if lowered.find(k.lower()) < INSTAGRAM["caption_visible"]]
    return {
        "keywords_present": present,
        "keywords_early": early,
        "coverage": round(len(present) / max(len(keywords), 1), 2),
    }


# ----------------------------------------------------------------------
# Policy risk checks (documented rules only, no guesswork)
# ----------------------------------------------------------------------
# YouTube demonetises these completely. Bleeping does not exempt them.
SLUR_PATTERNS = [
    r"\bn[i1]gg[ae3]r?s?\b", r"\bf[a4]gg?[o0]ts?\b", r"\bk[i1]kes?\b",
    r"\bsp[i1]cs?\b", r"\bch[i1]nks?\b", r"\btr[a4]nn(?:y|ies)\b",
    r"\bret[a4]rds?\b", r"\bw[e3]tb[a4]cks?\b",
]

# Profanity in a title or thumbnail limits or removes ads. This rule is current.
STRONG_PROFANITY = [r"\bfucks?\b", r"\bfucking\b", r"\bmotherfucker\b", r"\bcunts?\b"]
MODERATE_PROFANITY = [r"\bshits?\b", r"\bbitch(?:es)?\b", r"\bassholes?\b", r"\bdicks?\b"]

# Words that are perfectly allowed. Included so the checker never flags them,
# because YouTube's January 2026 update explicitly permits non-graphic coverage
# of sensitive subjects, and censoring them ("w4r") only harms credibility.
EXPLICITLY_ALLOWED = [
    "war", "death", "died", "kill", "killed", "suicide", "abortion", "abuse",
    "violence", "weapon", "drug", "murder", "crime", "attack", "disease",
]


@dataclass
class PolicyRisk:
    """A single documented policy risk found in the metadata."""

    field_name: str
    severity: str          # blocking | high | medium | info
    message: str
    matched: str = ""

    def as_dict(self) -> Dict[str, str]:
        return {
            "field": self.field_name,
            "severity": self.severity,
            "message": self.message,
            "matched": self.matched,
        }


def check_policy_risks(
    title: str = "", description: str = "", thumbnail_text: str = ""
) -> List[PolicyRisk]:
    """Flag only rules YouTube has actually published. No speculation.

    Deliberately does NOT flag words like "war" or "death". YouTube's January 2026
    update allows non-graphic coverage of sensitive subjects, so censoring them
    into "w4r" costs credibility and accessibility for no policy benefit.
    """
    risks: List[PolicyRisk] = []

    for field_name, text in (
        ("title", title), ("thumbnail_text", thumbnail_text), ("description", description)
    ):
        if not text:
            continue
        lowered = text.lower()

        for pattern in SLUR_PATTERNS:
            match = re.search(pattern, lowered)
            if match:
                risks.append(PolicyRisk(
                    field_name, "blocking",
                    "Slurs cause full demonetisation on YouTube. Bleeping or "
                    "asterisks do not exempt this.",
                    match.group(0),
                ))

        if field_name in ("title", "thumbnail_text"):
            for pattern in STRONG_PROFANITY:
                match = re.search(pattern, lowered)
                if match:
                    risks.append(PolicyRisk(
                        field_name, "high",
                        "Strong profanity in a title or thumbnail removes ad revenue. "
                        "It is allowed inside the video itself.",
                        match.group(0),
                    ))
            for pattern in MODERATE_PROFANITY:
                match = re.search(pattern, lowered)
                if match:
                    risks.append(PolicyRisk(
                        field_name, "medium",
                        "Moderate profanity in a title or thumbnail limits ads. "
                        "It is fine inside the video.",
                        match.group(0),
                    ))
    return risks


def algospeak_advice() -> str:
    """The project's documented position on algospeak, shown in the UI."""
    return (
        "This tool does not censor words like 'war' or 'death'. YouTube's January 2026 "
        "update explicitly allows non-graphic coverage of sensitive subjects, and a "
        "peer-reviewed study found that broken spellings such as 'w4r' are the easiest "
        "form for moderation systems to detect anyway. Substitutions also break "
        "captions for deaf viewers and damage credibility in factual content. The "
        "checks above cover only rules the platforms have actually published."
    )


# ----------------------------------------------------------------------
def platform_summary() -> Dict[str, Any]:
    """Current rule set, for display in the UI."""
    return {
        "youtube": {
            "tags": f"{YOUTUBE['tags_recommended'][0]}-{YOUTUBE['tags_recommended'][1]} "
                    "(tags lost most of their weight after 2019)",
            "description_hashtags": "3-5, in the description",
            "note": "Profanity allowed in-video since July 2025, never in title/thumbnail.",
        },
        "instagram": {
            "hashtags": f"{INSTAGRAM['hashtag_hard_cap']} maximum "
                        "(hard cap enforced since December 2025)",
            "note": "Hashtags do not boost reach. Keyword-rich captions drive ~30% more.",
        },
        "tiktok": {
            "hashtags": "3-5, never #fyp",
            "note": "Undisclosed commercial or AI content leaves the For You feed in 24h.",
        },
    }
