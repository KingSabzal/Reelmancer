"""Script rules derived from 2026 YouTube research, shared by every script path.

Both the topic-based generator and the source-based (URL) generator build their
prompts from this module, so a rule is never fixed in one path and forgotten in
the other.

Each rule below traces to a finding:

* Satisfaction replaced watch time as the primary ranking input (YouTube's
  "valued watchtime": only videos a viewer would rate 4-5 stars count). Padding
  now costs reach instead of buying it, so scripts are told to end when the
  material ends.
* The first 30 seconds became a core ranking input rather than a diagnostic.
* Shorts and long-form are separate discovery lanes since late 2025, so they get
  genuinely different instructions rather than one shared compromise.
* On a swipeable Shorts feed the decision happens at or before one second, so the
  hook is the first *frame*, not the first three seconds.
* Loop completion is the strongest positive signal a Short can produce, and it is
  designable in the script by ending on a question the opening line answers.
* Session contribution (does the viewer keep watching YouTube afterwards) is now
  the leading long-form signal, so the closing line points to more watching.
* Story formats retain 50-65% against 38-48% for listicles, so format is chosen
  deliberately rather than left to chance.
* YouTube's "inauthentic content" policy names channels whose videos differ only
  superficially. Rotating the narrative format across videos is therefore a
  compliance measure, not only a quality one.
"""

from __future__ import annotations

import json
import os
import random
from typing import Any, Dict, List, Optional

from utility.core.paths import ASSETS_DIR

# Below this a video is a vertical Short and follows the Shorts rules.
SHORTS_MAX_DURATION = 120

# Where recently used formats are remembered, so consecutive videos differ.
FORMAT_HISTORY_FILE = os.path.join(ASSETS_DIR, "script_format_history.json")
FORMAT_HISTORY_SIZE = 8


# ----------------------------------------------------------------------
# Narrative formats
# ----------------------------------------------------------------------
# Retention figures are the measured averages reported for each format. Story
# leads by a wide margin, which is why it carries the highest selection weight.
SCRIPT_FORMATS: Dict[str, Dict[str, Any]] = {
    "story": {
        "label": "Story / case study",
        "retention": "50-65%",
        "weight": 34,
        "spine": (
            "Situation, then complication, then consequence. One thread, told in "
            "order, with a change between the start and the end."
        ),
        "hook_formula": (
            "Open inside the most striking moment, or state the transformation "
            "before explaining it."
        ),
        "body_rule": (
            "Follow one continuous thread. Every beat must change something: a "
            "new obstacle, a reversal, or a consequence. Never list."
        ),
        "tip": "Put the change in the hook and tease one specific detail for the middle.",
    },
    "explainer": {
        "label": "Explainer / breakdown",
        "retention": "45-55%",
        "weight": 18,
        "spine": "A question worth asking, then the mechanism, then what it means.",
        "hook_formula": "Ask the high-stakes question the viewer cannot answer alone.",
        "body_rule": (
            "Build one idea at a time, each resting on the last. Answer 'why does "
            "that happen' before moving on."
        ),
        "tip": "Resolve the central question only after the mechanism is clear.",
    },
    "tutorial": {
        "label": "Tutorial / how-to",
        "retention": "45-55%",
        "weight": 12,
        "spine": "The end result first, then the ordered steps that produce it.",
        "hook_formula": "Show the finished result, then promise the exact route to it.",
        "body_rule": (
            "Number the steps out loud. Each step states what to do and why it "
            "matters, in that order."
        ),
        "tip": "Numbered steps create a mental checklist viewers stay to complete.",
    },
    "comparison": {
        "label": "Versus / comparison",
        "retention": "42-52%",
        "weight": 10,
        "spine": "Two options, judged on the same criteria, verdict withheld.",
        "hook_formula": "Name both options and promise a verdict that surprised you.",
        "body_rule": (
            "Alternate between the two on identical criteria. Keep a running "
            "score in the narration."
        ),
        "tip": "Delay the verdict until roughly 80% of the runtime.",
    },
    "investigation": {
        "label": "Investigation / mystery",
        "retention": "50-60%",
        "weight": 14,
        "spine": "An anomaly, the evidence, the explanation.",
        "hook_formula": "State the anomaly as plainly and strangely as possible.",
        "body_rule": (
            "Present evidence in escalating order. Each piece should narrow the "
            "possibilities and raise the stakes."
        ),
        "tip": "Never reveal the explanation before the evidence has built.",
    },
    "listicle": {
        "label": "Listicle / ranking",
        "retention": "38-48%",
        "weight": 12,
        "spine": "A counted set, ordered so the strongest item lands late.",
        "hook_formula": "Promise that one specific entry is the one nobody expects.",
        "body_rule": (
            "Order entries by ascending value. Announce each number out loud and "
            "keep entries roughly equal in length."
        ),
        "tip": "Tease the best entry early or viewers skip ahead to find it.",
    },
}

# Some subjects genuinely do not suit some shapes. A biography is not a tutorial.
STYLE_FORMAT_HINTS: Dict[str, List[str]] = {
    "tutorial": ["tutorial", "explainer"],
    "how-to": ["tutorial", "explainer"],
    "guide": ["tutorial", "explainer"],
    "review": ["comparison", "explainer"],
    "true crime": ["investigation", "story"],
    "mystery": ["investigation", "story"],
    "documentary": ["story", "investigation", "explainer"],
    "history": ["story", "investigation"],
    "biopic": ["story"],
    "explainer": ["explainer", "story"],
    "news report": ["explainer", "story"],
    "listicle": ["listicle"],
    "countdown": ["listicle"],
}


def _load_history() -> List[str]:
    """Recently used formats, oldest first."""
    try:
        with open(FORMAT_HISTORY_FILE, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return [str(item) for item in data][-FORMAT_HISTORY_SIZE:]
    except (OSError, json.JSONDecodeError, TypeError):
        return []


def remember_format(format_key: str) -> None:
    """Record a used format so the next video is unlikely to repeat it."""
    history = _load_history()
    history.append(format_key)
    try:
        os.makedirs(ASSETS_DIR, exist_ok=True)
        with open(FORMAT_HISTORY_FILE, "w", encoding="utf-8") as handle:
            json.dump(history[-FORMAT_HISTORY_SIZE:], handle)
    except OSError:
        pass  # Remembering is an optimisation, never a requirement.


def choose_format(
    style_name: str = "", duration_seconds: int = 60, explicit: Optional[str] = None
) -> str:
    """Pick the narrative shape for this video.

    Formats are weighted towards the ones that retain best, filtered to those
    that suit the style, and biased away from whatever the last few videos used.
    Two consecutive videos sharing a shape is what the inauthentic content policy
    describes as "only superficial differences".
    """
    if explicit and explicit in SCRIPT_FORMATS:
        return explicit

    candidates = list(SCRIPT_FORMATS)
    lowered = (style_name or "").lower()
    for marker, allowed in STYLE_FORMAT_HINTS.items():
        if marker in lowered:
            candidates = [key for key in allowed if key in SCRIPT_FORMATS] or candidates
            break

    # A very short Short cannot carry a comparison or a numbered list well.
    if duration_seconds <= 25:
        tight = [key for key in candidates if key in {"story", "explainer", "investigation"}]
        candidates = tight or candidates

    # Recency is a penalty, not an exclusion. Hard-excluding the last few formats
    # looks correct until the candidate pool is small: with three viable formats it
    # produces a perfectly predictable A-B-C-A-B-C cycle, which is just a slower
    # template. Damping the weights keeps the choice genuinely stochastic while
    # still making an immediate repeat unlikely.
    history = _load_history()
    weights = []
    for key in candidates:
        weight = float(SCRIPT_FORMATS[key]["weight"])
        if history and key == history[-1]:
            weight *= 0.08   # almost never twice in a row
        elif len(history) > 1 and key == history[-2]:
            weight *= 0.35
        elif key in history[-4:]:
            weight *= 0.7
        weights.append(max(weight, 0.5))

    return random.choices(candidates, weights=weights, k=1)[0]


# ----------------------------------------------------------------------
# Timing rules
# ----------------------------------------------------------------------
def is_short(duration_seconds: float) -> bool:
    """True when the video follows the Shorts rules."""
    return duration_seconds < SHORTS_MAX_DURATION


def hook_spec(duration_seconds: float) -> Dict[str, Any]:
    """How long the hook has, and what it must achieve.

    On a swipeable feed the keep-or-swipe decision is reflexive and lands at or
    before one second, so a Short's hook is the opening frame. The older "first
    three seconds" guidance came from long-form discovery and is too slow here.
    """
    if is_short(duration_seconds):
        return {
            "seconds": 1.0,
            "words": "5 to 8",
            "instruction": (
                "The hook is the FIRST FRAME, not the first three seconds. On a "
                "swipe feed the viewer decides within one second. The very first "
                "spoken words must already be the surprising claim, the result, or "
                "the strange fact. No preamble of any kind: no greeting, no channel "
                "name, no 'in this video', no scene setting, not even one warm-up "
                "word before the payload."
            ),
        }
    return {
        "seconds": 10.0,
        "words": "12 to 25",
        "instruction": (
            "The hook occupies the first 10 seconds and must do three things at "
            "once: validate the click so the viewer knows they are in the right "
            "place, raise the stakes so they know why it matters now, and open one "
            "curiosity loop that hints at the payoff without giving it away. Never "
            "open with a greeting, a channel introduction, or 'in this video'."
        ),
    }


def pacing_spec(duration_seconds: float) -> Dict[str, Any]:
    """Content-level and visual-level change intervals.

    These are two different things and are frequently conflated. A pattern
    interrupt is a change of content or format: a question, an aside, a shift in
    tone. A visual change is a cut or a new clip. Long-form wants a content
    change every 60-90 seconds but a visual change far more often; a genuine
    format change every four seconds would be incoherent.
    """
    if is_short(duration_seconds):
        return {
            "content_interval": 6.0,
            "visual_interval": 3.0,
            "instruction": (
                "Change the kind of thing being said every 5 to 8 seconds: a "
                "question, a contrast, a consequence. Mark a visual change with "
                "[CUE] every 2 to 4 seconds of speech."
            ),
        }
    return {
        "content_interval": 75.0,
        "visual_interval": 4.0,
        "instruction": (
            "Change the kind of thing being said every 60 to 90 seconds: move to a "
            "new sub-question, an aside, a concrete example, or a shift in tone. "
            "Separately, mark a visual change with [CUE] every 3 to 5 seconds of "
            "speech. These are different: the first is a change of content, the "
            "second is only a change of picture."
        ),
    }


def ending_spec(duration_seconds: float) -> Dict[str, Any]:
    """How the script should close.

    Shorts: a loop. When the final line leads back into the opening line the
    viewer rewatches without deciding to, which registers as a replay and is the
    strongest positive signal a Short can produce.

    Long-form: session contribution. The leading long-form signal is now whether
    the viewer keeps watching YouTube afterwards, so the ending opens a door
    rather than closing one, and never sends the viewer off the platform.
    """
    if is_short(duration_seconds):
        return {
            "kind": "loop",
            "instruction": (
                "END ON A LOOP. The final sentence must lead straight back into the "
                "first sentence, so that when the video restarts it reads as one "
                "continuous thought. The cleanest way is to end on the question that "
                "the opening line answers. Do not end with 'thanks for watching' or "
                "any closing that signals the video is over. Put this closing line "
                "in the \"loop_line\" field and make sure it joins onto the hook."
            ),
        }
    return {
        "kind": "session",
        "instruction": (
            "END BY OPENING A DOOR. The closing lines should leave one specific "
            "question raised and unanswered, so the viewer wants another video "
            "immediately. Never send the viewer off YouTube. Put that forward "
            "-looking line in the \"next_hook\" field."
        ),
    }


def cta_spec(duration_seconds: float) -> Dict[str, Any]:
    """Call to action rules.

    One click-level call to action, not several: a viewer given two things to
    click tends to choose neither. It must also stay on YouTube, because
    off-platform calls to action work against session contribution.
    """
    if is_short(duration_seconds):
        return {
            "instruction": (
                "ONE short call to action in the last three seconds, and only one. "
                "Keep it to a handful of words and never let it interrupt the loop: "
                "it comes before the looping final line, not after it."
            )
        }
    return {
        "instruction": (
            "ONE call to action, placed after the payoff has landed, in the final "
            "10% of the script. Make it specific to what the viewer just learned "
            "rather than a generic 'like and subscribe'. Never give two different "
            "things to click, and never point off YouTube."
        )
    }


def length_discipline(word_count: int, duration_seconds: float) -> str:
    """Instruction about honest length.

    Satisfaction, not raw watch time, is the primary ranking input, and it is
    estimated from whether a viewer would rate the video highly. Filler lowers
    that estimate, so a short honest script now beats a padded long one.
    """
    return (
        f"LENGTH: aim for about {word_count} words, which fits {int(duration_seconds)} "
        "seconds of narration. Treat this as a ceiling, not a quota. If the material "
        "runs out, finish early: a shorter script that satisfies outranks a padded one, "
        "because the ranking model estimates whether a viewer would rate the video "
        "highly rather than counting minutes. Never repeat a point, restate the hook, "
        "or add filler to reach the number."
    )


def voice_rules() -> str:
    """How the words should sound when spoken aloud."""
    return (
        "WRITE FOR THE EAR, NOT THE PAGE. Every line will be read aloud by a "
        "synthetic voice, so write the way people speak. Short sentences. "
        "Fragments are fine. Average under 15 words per sentence, never above 25. "
        "Plain words over technical ones wherever the meaning survives. If a "
        "sentence would sound strange said out loud, rewrite it."
    )


def writing_order() -> str:
    """Force the payoff to exist before the hook promises it.

    Writing the hook first is how scripts end up promising something the body
    never delivers, and a gap between the promise and the content is one of the
    most common causes of early drop-off.
    """
    return (
        "WRITING ORDER (internal, do not describe this in the output): decide the "
        "payoff first, then the beats that lead to it, then write the hook LAST so "
        "that it promises exactly what the script actually delivers. A hook that "
        "over-promises causes the viewer to leave and is worse than a plain one."
    )


def originality_rules() -> str:
    """Keep output clear of the inauthentic-content standard.

    The policy names channels whose videos carry only superficial differences and
    slideshows that share the same narration. Enforcement is channel-wide, so the
    risk accumulates across uploads rather than sitting in any single video.
    """
    return (
        "ORIGINALITY: this script must not read like a template with the subject "
        "swapped in. Do not use stock scaffolding such as 'buckle up', 'let's dive "
        "in', 'you won't believe', 'stay tuned' or 'here's the kicker'. Find the "
        "angle that is specific to this subject and could not be reused for a "
        "different one. Offer a connection, framing or consequence that a plain "
        "summary would miss."
    )


def build_structure_block(
    duration_seconds: float,
    word_count: int,
    format_key: str,
    sourced: bool = False,
) -> str:
    """Assemble the shared MANDATORY STRUCTURE block used by both prompts."""
    fmt = SCRIPT_FORMATS.get(format_key, SCRIPT_FORMATS["story"])
    hook = hook_spec(duration_seconds)
    pacing = pacing_spec(duration_seconds)
    ending = ending_spec(duration_seconds)
    cta = cta_spec(duration_seconds)
    short = is_short(duration_seconds)

    fact_line = (
        "every beat carries a fact from the source"
        if sourced
        else "every sentence delivers information or moves the story"
    )

    lines = [
        f"NARRATIVE FORMAT: {fmt['label']} (typical retention {fmt['retention']}).",
        f"  Shape: {fmt['spine']}",
        f"  Body rule: {fmt['body_rule']}",
        f"  Note: {fmt['tip']}",
        "",
        "MANDATORY STRUCTURE:",
        f"1. HOOK ({hook['words']} words). {hook['instruction']}",
        f"   Hook approach for this format: {fmt['hook_formula']}",
        "2. OPEN LOOP: raise one question early and answer it at about 80% of the "
        "script. Exactly one; competing loops cancel each other out.",
        f"3. DENSITY: no filler, {fact_line}. Cut anything that does not serve the "
        "hook's promise.",
        f"4. PACING: {pacing['instruction']}",
    ]

    if short:
        lines += [
            "5. FRONT-LOAD THE PAYOFF: deliver the most valuable moment in the first "
            "half. A Short is ranked on the share of it that gets watched, so nothing "
            "worth staying for may sit at the end.",
            "6. SOUND-OFF READABILITY: most viewers watch muted, so the opening claim "
            "must survive as on-screen text. Put the first 3 to 6 words of the hook in "
            "\"screen_text\" as the very first entry.",
        ]
    else:
        lines += [
            "5. RETENTION TRAP at the midpoint: a surprising reveal or a question the "
            "viewer urgently wants answered.",
            "6. INTERNAL TEASERS: between major beats, one short forward-looking line "
            "such as 'the part nobody expected comes next'.",
        ]

    lines += [
        f"7. {voice_rules()}",
        "8. ON-SCREEN NUMBERS: whenever the narration states a figure, put the bare "
        "figure in \"screen_text\" so it can be shown at that moment.",
        f"9. CTA: {cta['instruction']}",
        f"10. ENDING: {ending['instruction']}",
        "",
        length_discipline(word_count, duration_seconds),
        "",
        writing_order(),
        "",
        originality_rules(),
    ]
    return "\n".join(lines)


def output_fields(duration_seconds: float, sourced: bool = False) -> str:
    """The JSON shape both prompts must return."""
    ending_field = (
        '"loop_line": "the final line, which must join back onto the hook"'
        if is_short(duration_seconds)
        else '"next_hook": "the unanswered question that makes the viewer want another video"'
    )
    extra = '\n  "key_facts": ["the specific source facts the script relies on"],' if sourced else ""
    # Single braces: this string is injected after .format() has already run on the
    # prompt template, so doubling them would show the model "{{" and break the JSON.
    return (
        "{\n"
        '  "title": "under 60 characters, curiosity-driven",\n'
        '  "script": "the full narration with [CUE] markers",\n'
        '  "hook": "the hook on its own",\n'
        '  "open_loop": "the question raised early",\n'
        '  "payoff": "the sentence that resolves it",\n'
        '  "cta": "the single call to action",\n'
        f"  {ending_field},\n"
        '  "retention_trap": "the midpoint reveal, or \\"\\" for a Short",\n'
        '  "search_queries": ["short concrete visual phrases"],\n'
        '  "keywords": ["8 SEO keywords"],\n'
        f'  "screen_text": ["bare figures and the opening claim"]{extra}\n'
        "}"
    )
