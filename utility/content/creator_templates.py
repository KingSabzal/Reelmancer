"""Title, thumbnail and description templates modelled on proven creators."""

from __future__ import annotations

from typing import Any, Dict, List

CREATOR_TEMPLATES: Dict[str, Dict[str, Any]] = {
    "MrBeast": {
        "name": "MrBeast",
        "title_style": "Extreme challenge or stake, first person, concrete number",
        "title_examples": [
            "I Spent 50 Hours In Solitary Confinement",
            "$1 vs $500,000 Plane Ticket",
            "Last To Leave The Circle Wins $500,000",
        ],
        "title_formulas": [
            "I {extreme_action} For {number} {unit}",
            "${small} vs ${large} {object}",
            "Last To {action} Wins {prize}",
            "{number} People Fight For {prize}",
        ],
        "thumbnail_rules": "Shocked face, one giant object, 3 words maximum, saturated contrast",
        "thumbnail_words": 3,
        "description_style": "Short, punchy, links first, minimal text",
        "description_length": [400, 900],
        "tags": 20,
        "best_for": ["Countdown", "Action", "Adventure", "Sports", "Motivational"],
    },
    "Veritasium": {
        "name": "Veritasium",
        "title_style": "Paradox or counterintuitive question, no clickbait numbers",
        "title_examples": [
            "The Simplest Math Problem No One Can Solve",
            "Why No One Has Measured The Speed Of Light",
            "The Bizarre Behaviour Of Rotating Bodies",
        ],
        "title_formulas": [
            "The {superlative} {noun} No One Can {verb}",
            "Why {common_belief} Is Wrong",
            "This {object} Should Be Impossible",
            "The Problem With {topic}",
        ],
        "thumbnail_rules": "One intriguing object or diagram, curious expression, 2-4 words",
        "thumbnail_words": 4,
        "description_style": "Educational, sources listed, references and further reading",
        "description_length": [1500, 3000],
        "tags": 25,
        "best_for": ["Physics", "Quantum", "Deep Dive", "Explainer", "Astronomy", "Science"],
    },
    "Ali Abdaal": {
        "name": "Ali Abdaal",
        "title_style": "How-to plus credibility bracket",
        "title_examples": [
            "How I Learn Anything Fast (Science-Based)",
            "How To Study For Exams - Evidence-Based Revision Tips",
            "The Feynman Technique Explained In 5 Minutes",
        ],
        "title_formulas": [
            "How I {outcome} ({credibility})",
            "How To {outcome} - {method} Tips",
            "{number} Habits That {benefit}",
            "The {method} Explained In {time}",
        ],
        "thumbnail_rules": "Clean background, friendly face, 2-4 words, pastel accents",
        "thumbnail_words": 4,
        "description_style": "Personal story, timestamps, tools mentioned, newsletter CTA",
        "description_length": [1500, 3000],
        "tags": 22,
        "best_for": ["Tutorial", "How-To", "Guide", "Business", "Psychology", "Masterclass"],
    },
    "Kurzgesagt": {
        "name": "Kurzgesagt",
        "title_style": "Topic plus scale, explainer framing",
        "title_examples": [
            "What Happens If You Fall Into A Black Hole?",
            "The Largest Star In The Universe - Size Comparison",
            "Why Are You Alive - Life, Energy And ATP",
        ],
        "title_formulas": [
            "What Happens If {scenario}?",
            "The {superlative} {noun} In The Universe",
            "Why {big_question} - {subtitle}",
            "Can We {ambitious_goal}?",
        ],
        "thumbnail_rules": "Flat colourful illustration, bold silhouette, minimal text",
        "thumbnail_words": 3,
        "description_style": "Source-heavy, long, sponsor block, further reading list",
        "description_length": [2000, 3000],
        "tags": 25,
        "best_for": ["Space", "Galaxy", "Black Hole", "Biology", "Multiverse", "What If"],
    },
    "Gary Vaynerchuk": {
        "name": "Gary Vaynerchuk",
        "title_style": "Bold aggressive statement, often ALL CAPS fragments",
        "title_examples": [
            "STOP WASTING YOUR 20s",
            "The Truth About Hustle Nobody Tells You",
            "This Mindset Will Change Your Life",
        ],
        "title_formulas": [
            "STOP {bad_behaviour}",
            "The Truth About {topic} Nobody Tells You",
            "This {noun} Will Change Your {outcome}",
            "Why You Are Not {goal} Yet",
        ],
        "thumbnail_rules": "High energy expression, bold caps text, high contrast",
        "thumbnail_words": 4,
        "description_style": "Motivational, direct, social links, short paragraphs",
        "description_length": [1000, 2000],
        "tags": 20,
        "best_for": ["Motivational", "Business", "Speech", "Finance", "Crypto"],
    },
    "General Best Practices": {
        "name": "General Best Practices",
        "title_style": "Hook plus keyword plus benefit, 50-60 characters",
        "title_examples": [
            "The Hidden Cost Of Cheap Solar Panels In 2026",
            "Why Your Sleep Tracker Is Lying To You",
        ],
        "title_formulas": [
            "{hook} {keyword} {benefit}",
            "Why {keyword} Is {unexpected_adjective}",
            "{number} {keyword} Mistakes To Avoid In 2026",
        ],
        "thumbnail_rules": "3-5 bold words, high contrast, one focal subject",
        "thumbnail_words": 5,
        "description_style": "Two-line hook, timestamps, key points, resources, CTA, hashtags",
        "description_length": [1500, 3000],
        "tags": 25,
        "best_for": ["*"],
    },
}

STYLE_TO_CREATOR: Dict[str, str] = {}


def _build_style_map() -> None:
    """Map each video style to the closest creator template."""
    for creator, template in CREATOR_TEMPLATES.items():
        for style in template["best_for"]:
            if style != "*":
                STYLE_TO_CREATOR.setdefault(style, creator)


_build_style_map()


def template_for_style(style_name: str) -> Dict[str, Any]:
    """Return the creator template best suited to a video style."""
    creator = STYLE_TO_CREATOR.get(style_name, "General Best Practices")
    return CREATOR_TEMPLATES[creator]


def list_creators() -> List[str]:
    """Return every available creator template name."""
    return list(CREATOR_TEMPLATES.keys())


GENERAL_RULES = {
    "title_length": [50, 60],
    "description_length": [1500, 3000],
    "tag_count": [15, 30],
    "thumbnail_words": [3, 5],
    "keyword_density_percent": [2, 3],
    "instagram_hashtags": [20, 30],
    "tiktok_hashtags": [3, 5],
}
