"""Title analysis and scoring against the 2026 research.

Two large studies disagreed on optimal title length:

* a 3-million-video study found 70-100 characters scored best, because it measured
  SEO/discoverability, where extra keywords help;
* an 18,080-channel study across 11 niches found 30-50 characters best and 90+ the
  worst, because it measured engagement and views, where mobile truncation hurts.

Both are consistent once you separate the metrics. The rule this module applies is
the practical synthesis: **optimise the first 50 characters ruthlessly**, allow the
tail only when it earns its place, and vary the target by niche, because the
18k-channel data shows gaming and food peak under 30 characters while education and
tech peak at 30-50.

Everything here is local text analysis. No API calls, no cost.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Tuple

# --- Hard platform limits -------------------------------------------------
MAX_TITLE_CHARS = 100          # YouTube rejects anything longer
MOBILE_NOTIFICATION_CUT = 45   # strictest surface
MOBILE_FEED_CUT = 52
DESKTOP_SEARCH_CUT = 65

# Where the hook must land so it survives every surface.
HOOK_WINDOW = 50
KEYWORD_WINDOW = 30

# --- Niche-specific optimal ranges (from the 18k-channel study) -----------
NICHE_TITLE_RANGES: Dict[str, Tuple[int, int]] = {
    "gaming": (18, 30),
    "entertainment": (18, 32),
    "food": (18, 32),
    "cooking": (18, 32),
    "technology": (35, 55),
    "science": (35, 55),
    "education": (35, 55),
    "business": (35, 55),
    "finance": (35, 55),
    "health": (32, 52),
    "fitness": (32, 52),
    "beauty": (40, 62),
    "travel": (32, 52),
    "mystery": (35, 58),
    "politics": (35, 55),
    "sports": (28, 48),
    "culture": (32, 52),
    "environment": (35, 55),
    "default": (40, 60),
}

# Shorts have less room in the player UI.
SHORTS_RANGE = (28, 42)

# --- Pattern vocabularies -------------------------------------------------
CURIOSITY_MARKERS = [
    "secret", "hidden", "nobody", "no one", "the truth", "what nobody", "why",
    "reason", "actually", "really", "turns out", "never told", "they don't",
    "what happens", "the one", "until", "before", "behind", "inside",
]

POWER_WORDS = [
    "shocking", "insane", "secret", "brutal", "genius", "never", "always",
    "exposed", "instantly", "ultimate", "proven", "surprising", "unbelievable",
    "banned", "destroyed", "terrifying", "impossible", "forbidden", "deadly",
]

URGENCY_MARKERS = [
    "now", "today", "before it's too late", "immediately", "stop", "right now",
    "this week", "finally", "just", "already",
]

FIRST_PERSON_RE = re.compile(r"\b(i|my|me|we|our|i'm|i've|my)\b", re.IGNORECASE)

CONTRARIAN_MARKERS = [
    "don't", "do not", "stop", "never", "wrong", "myth", "truth about",
    "nobody tells", "worst", "avoid", "mistake", "isn't", "not what",
]

TIME_MARKERS = [
    "2026", "2027", "in 24 hours", "in a day", "in a week", "in a month",
    "days", "hours", "minutes", "seconds", "years",
]

# Words that signal a title is generic filler.
WEAK_WORDS = [
    "video", "watch this", "my new", "check out", "update", "vlog", "episode",
    "part 1", "untitled", "amazing video", "must watch",
]

CLICKBAIT_RED_FLAGS = [
    "you won't believe", "gone wrong", "gone sexual", "doctors hate",
    "this one trick", "number 7 will", "shocking truth revealed",
]


def visual_length(title: str) -> int:
    """Character cost as YouTube counts it, including real emoji cost.

    A simple emoji costs 2, a skin-tone variant 4, and a ZWJ compound emoji can
    cost 7-11 while rendering as a single glyph. Two complex emoji can silently
    consume 20 of the 100 available characters.
    """
    total = 0
    index = 0
    text = title or ""
    while index < len(text):
        char = text[index]
        code = ord(char)
        if code < 0x2000:  # ordinary text
            total += 1
            index += 1
            continue
        # Emoji or symbol: measure the whole grapheme cluster.
        cluster_end = index + 1
        while cluster_end < len(text):
            nxt = ord(text[cluster_end])
            if nxt == 0x200D:  # zero-width joiner, cluster continues
                cluster_end += 2 if cluster_end + 1 < len(text) else 1
                continue
            if nxt == 0xFE0F or 0x1F3FB <= nxt <= 0x1F3FF:  # variation / skin tone
                cluster_end += 1
                continue
            break
        cluster = text[index:cluster_end]
        joiners = cluster.count("\u200d")
        modifiers = sum(1 for c in cluster if 0x1F3FB <= ord(c) <= 0x1F3FF)
        total += 2 + joiners * 3 + modifiers * 2
        index = cluster_end
    return total


def emoji_count(title: str) -> int:
    """Number of emoji glyphs in the title."""
    return len(re.findall(
        r"[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF]", title or ""
    ))


@dataclass
class TitleScore:
    """Result of scoring a title against the 2026 patterns."""

    title: str
    total: float
    patterns: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    length: int = 0
    visual_length: int = 0
    hook_fits: bool = False
    keyword_early: bool = False

    def as_dict(self) -> Dict[str, Any]:
        return {
            "title": self.title,
            "score": round(self.total, 1),
            "patterns": self.patterns,
            "warnings": self.warnings,
            "length": self.length,
            "visual_length": self.visual_length,
            "hook_fits_mobile": self.hook_fits,
            "keyword_in_first_30": self.keyword_early,
        }


class TitleOptimizer:
    """Scores and repairs titles against the seven viral patterns."""

    def __init__(self, niche: str = "default", is_short: bool = False):
        self.niche = (niche or "default").lower()
        self.is_short = is_short

    # ------------------------------------------------------------------
    def target_range(self) -> Tuple[int, int]:
        """Optimal character range for this niche and format."""
        if self.is_short:
            return SHORTS_RANGE
        for key, bounds in NICHE_TITLE_RANGES.items():
            if key in self.niche:
                return bounds
        return NICHE_TITLE_RANGES["default"]

    # ------------------------------------------------------------------
    def detect_patterns(self, title: str) -> List[str]:
        """Which of the seven viral patterns the title uses."""
        lowered = f" {title.lower()} "
        found: List[str] = []

        if re.search(r"\b\d+\b|\$\d|\d+%", title):
            found.append("specific_number")
        if any(marker in lowered for marker in CURIOSITY_MARKERS) or title.rstrip().endswith("?"):
            found.append("curiosity_gap")
        if FIRST_PERSON_RE.search(title):
            found.append("first_person")
        if any(marker in lowered for marker in CONTRARIAN_MARKERS):
            found.append("contrarian")
        if any(marker in lowered for marker in TIME_MARKERS):
            found.append("time_marker")
        if any(word in lowered for word in POWER_WORDS):
            found.append("power_word")

        low, high = self.target_range()
        if low <= len(title) <= high:
            found.append("optimal_length")
        return found

    # ------------------------------------------------------------------
    def score(self, title: str, keyword: str = "") -> TitleScore:
        """Score a title from 0 to 100 and list its problems."""
        title = (title or "").strip()
        lowered = title.lower()
        length = len(title)
        vlength = visual_length(title)
        patterns = self.detect_patterns(title)
        warnings: List[str] = []
        score = 0.0

        # --- Pattern coverage: three or four patterns is the sweet spot ---
        core = [p for p in patterns if p != "optimal_length"]
        score += min(len(core), 4) * 11.0
        if len(core) >= 3:
            score += 6.0  # combining patterns is what the research rewards

        # --- Length ---
        low, high = self.target_range()
        if low <= length <= high:
            score += 18.0
        elif length < low:
            score += 8.0
            warnings.append(f"Shorter than the {low}-{high} range for this niche.")
        elif length <= DESKTOP_SEARCH_CUT:
            score += 12.0
        elif length <= 90:
            score += 5.0
            warnings.append("Longer than ideal; the tail is cut off on mobile.")
        else:
            warnings.append("Over 90 characters: consistently the worst-performing bucket.")
        if length > MAX_TITLE_CHARS or vlength > MAX_TITLE_CHARS:
            warnings.append("Exceeds the 100-character hard limit; YouTube will reject it.")

        # --- Hook must survive mobile truncation ---
        hook_fits = vlength <= HOOK_WINDOW or _has_complete_thought(title[:HOOK_WINDOW])
        if hook_fits:
            score += 10.0
        else:
            warnings.append(
                f"The hook does not resolve within the first {HOOK_WINDOW} characters, "
                "so mobile viewers see a truncated fragment."
            )

        # --- Keyword position ---
        keyword_early = False
        if keyword:
            position = lowered.find(keyword.lower().strip())
            keyword_early = 0 <= position <= KEYWORD_WINDOW
            if keyword_early:
                score += 10.0
            elif position >= 0:
                score += 3.0
                warnings.append("Main keyword appears after character 30; move it earlier.")
            else:
                warnings.append("Main keyword is missing from the title.")

        # --- Power word discipline: exactly one is ideal ---
        power_hits = sum(1 for word in POWER_WORDS if word in lowered)
        if power_hits == 1:
            score += 6.0
        elif power_hits >= 3:
            score -= 12.0 * power_hits
            warnings.append(
                f"{power_hits} power words. More than one reads as parody clickbait "
                "and erodes algorithmic trust."
            )
        elif power_hits == 2:
            score -= 2.0

        # --- Number quality: a promise, not decoration ---
        if "specific_number" in patterns:
            if re.search(r"\$\s?\d|\d+\s*%|\d+\s*(day|days|hour|hours|month|months|year|years|k\b|x\b)", lowered):
                score += 6.0  # the number makes a concrete promise
            else:
                warnings.append(
                    "The number is decorative. Numbers only lift CTR when they promise "
                    "something specific."
                )

        # --- Penalties ---
        if any(weak in lowered for weak in WEAK_WORDS):
            score -= 12.0
            warnings.append("Contains generic filler wording.")
        if any(flag in lowered for flag in CLICKBAIT_RED_FLAGS):
            score -= 15.0
            warnings.append("Uses a discredited clickbait phrase.")
        if title.isupper() and length > 12:
            score -= 25.0
            warnings.append("ALL CAPS shows no measurable benefit and looks spammy.")

        emojis = emoji_count(title)
        if emojis:
            hidden = vlength - length
            if hidden > 4:
                warnings.append(
                    f"Emoji cost {vlength - len(re.sub(r'[^ -~]', '', title))} extra characters "
                    "against the 100 limit."
                )

        # --- Uniqueness of wording ---
        words = [w for w in re.findall(r"[a-z']+", lowered) if len(w) > 3]
        if words and len(set(words)) < len(words):
            score -= 4.0
            warnings.append("Repeats a word; use the space for new information.")

        return TitleScore(
            title=title,
            total=max(0.0, min(score, 100.0)),
            patterns=patterns,
            warnings=warnings,
            length=length,
            visual_length=vlength,
            hook_fits=hook_fits,
            keyword_early=keyword_early,
        )

    # ------------------------------------------------------------------
    def enforce_limits(self, title: str) -> str:
        """Trim a title to the hard limit without cutting mid-word."""
        title = re.sub(r"\s+", " ", (title or "")).strip().strip('"').strip("'")
        if visual_length(title) <= MAX_TITLE_CHARS and len(title) <= MAX_TITLE_CHARS:
            return title
        cut = title[:MAX_TITLE_CHARS]
        if " " in cut:
            cut = cut[: cut.rfind(" ")]
        return cut.rstrip(" ,:;-\u2013\u2014")

    def best_of(self, titles: List[str], keyword: str = "") -> Tuple[str, List[TitleScore]]:
        """Score several candidates and return the winner plus every score."""
        scored = [self.score(self.enforce_limits(t), keyword) for t in titles if t and t.strip()]
        if not scored:
            return "", []
        scored.sort(key=lambda s: s.total, reverse=True)
        return scored[0].title, scored

    def guidance(self) -> Dict[str, Any]:
        """The rules the LLM should follow, for injection into a prompt."""
        low, high = self.target_range()
        return {
            "niche": self.niche,
            "format": "Shorts" if self.is_short else "long-form",
            "target_min": low,
            "target_max": high,
            "hard_max": MAX_TITLE_CHARS,
            "hook_window": HOOK_WINDOW,
            "keyword_window": KEYWORD_WINDOW,
            "mobile_cut": MOBILE_FEED_CUT,
        }


def _has_complete_thought(fragment: str) -> bool:
    """True when a truncated fragment still reads as a self-contained idea."""
    fragment = fragment.strip()
    if len(fragment) < 20:
        return False
    # Ending on a preposition or article means the thought is cut mid-phrase.
    trailing = fragment.rstrip(" .,:;-").split()
    if not trailing:
        return False
    dangling = {
        "the", "a", "an", "of", "to", "in", "on", "at", "for", "with", "and",
        "or", "but", "that", "this", "is", "are", "was", "were", "from", "by",
    }
    return trailing[-1].lower() not in dangling


def prompt_rules(niche: str = "default", is_short: bool = False, keyword: str = "") -> str:
    """Build the title instruction block used inside LLM prompts."""
    optimizer = TitleOptimizer(niche, is_short)
    low, high = optimizer.target_range()
    keyword_line = (
        f'- Place the keyword "{keyword}" within the first {KEYWORD_WINDOW} characters.'
        if keyword else
        f"- Place the main keyword within the first {KEYWORD_WINDOW} characters."
    )
    return f"""TITLE RULES (2026 research, {optimizer.niche} niche, {'Shorts' if is_short else 'long-form'}):
- Target {low}-{high} characters. Never exceed {MAX_TITLE_CHARS}.
- The hook must fully resolve within the first {HOOK_WINDOW} characters, because mobile
  truncates there and mobile is over 70% of viewing.
{keyword_line}
- Combine THREE or FOUR of these patterns, not all seven:
  specific number that promises something concrete (not decoration),
  curiosity gap that names the subject but withholds the answer,
  first-person framing, contrarian or negative angle, time marker,
  one power word, optimal length.
- Use at most ONE power word. Two or more reads as parody clickbait.
- Odd numbers outperform even numbers by roughly 20%.
- Do not repeat what the thumbnail text will say; the title adds what the image cannot.
- No ALL CAPS, no discredited clickbait phrases, no generic filler.
- The video must actually close the curiosity gap the title opens."""
