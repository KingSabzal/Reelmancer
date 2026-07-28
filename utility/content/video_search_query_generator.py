"""Timed visual search queries.

This is the module that makes the footage match the narration word by word. The model
receives the script *and the word-level timed captions*, then returns three visually
concrete keywords for every 2-4 second segment, covering the whole timeline
consecutively. That is what keeps the image in sync with the exact sentence being
spoken.

Sending the timings along with the script matters: without them the model describes
the video as a whole and the footage drifts away from what is actually being said at
each moment. Every LLM call is routed through SmartLLMRouter, so the provider the user
selected is the one that answers.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, List, Optional

from utility.llm.llm_router import SmartLLMRouter, get_router

LOGGER = logging.getLogger("search_queries")
if not LOGGER.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("[%(name)s] %(message)s"))
    LOGGER.addHandler(_h)
LOGGER.setLevel(logging.INFO)

# Prompt tuned for concrete, searchable visuals rather than abstract description.
PROMPT = """# Instructions

Given the following video script and timed captions, extract three visually concrete
and specific keywords for each time segment that can be used to search for background
videos. The keywords should be short and capture the main essence of the sentence.
They can be synonyms or related terms. If a caption is vague or general, consider the
next timed caption for more context. If a keyword is a single word, try to return a
two-word keyword that is visually concrete. If a time frame contains two or more
important pieces of information, divide it into shorter time frames with one keyword
each. Ensure that the time periods are strictly consecutive and cover the entire
length of the video. Each keyword should cover between 2-4 seconds. The output should
be in JSON format, like this: [[[t1, t2], ["keyword1", "keyword2", "keyword3"]],
[[t2, t3], ["keyword4", "keyword5", "keyword6"]], ...]. Please handle all edge cases,
such as overlapping time segments, vague or general captions, and single-word keywords.

For example, if the caption is 'The cheetah is the fastest land animal, capable of
running at speeds up to 75 mph', the keywords should include 'cheetah running',
'fastest animal', and '75 mph'. Similarly, for 'The Great Wall of China is one of the
most iconic landmarks in the world', the keywords should be 'Great Wall of China',
'iconic landmark', and 'China landmark'.

Important Guidelines:

If the script is about a specific person, organisation, place or event, that proper
noun MUST appear in at least one of the three keywords for every segment. A generic
word alone retrieves the wrong subject: 'president' returns any president in the world,
while 'Donald Trump' returns the one the script is about. Keep the name attached even
when the sentence itself only says 'he' or 'the president'.

Use only English in your text queries.
Each search string must depict something visual.
The depictions have to be extremely visually concrete, like rainy street, or cat sleeping.
'emotional moment' <= BAD, because it doesn't depict something visually.
'crying child' <= GOOD, because it depicts something visual.
The list must always contain the most relevant and appropriate query searches.
['Car', 'Car driving', 'Car racing', 'Car parked'] <= BAD, because it's 4 strings.
['Fast car'] <= GOOD, because it's 1 string.
['Un chien', 'une voiture rapide', 'une maison rouge'] <= BAD, because the text query is NOT in English.

Note: Your response should be the response only and no extra text or data.
"""

STOP_WORDS = {
    "the", "a", "an", "and", "or", "but", "is", "are", "was", "were", "of", "to", "in",
    "on", "at", "for", "with", "as", "by", "it", "its", "we", "us", "our", "you", "your",
    "that", "this", "they", "them", "their", "have", "has", "had", "been", "be", "will",
    "would", "could", "should", "can", "just", "then", "than", "so", "if", "not", "no",
}


def fix_json(json_str: str) -> str:
    """Repair the quote styles an LLM commonly gets wrong."""
    json_str = json_str.replace("\u2019", "'")
    json_str = (
        json_str.replace("\u201c", '"')
        .replace("\u201d", '"')
        .replace("\u2018", '"')
        .replace("\u2019", '"')
    )
    json_str = json_str.replace('"you didn"t"', '"you didn\'t"')
    json_str = re.sub(r",\s*([}\]])", r"\1", json_str)
    return json_str


def _clean_llm_text(text: str) -> str:
    """Strip code fences and stray prefixes from a raw model reply."""
    text = re.sub(r"\s+", " ", text).strip()
    if text.startswith("```json"):
        text = text[7:]
    if text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    for prefix in ("content:", "content =", "content="):
        if text.startswith(prefix):
            text = text[len(prefix):].strip()
    if text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    return text.strip()


def call_llm(script: str, captions_timed, router: SmartLLMRouter) -> str:
    """Ask the model for timed keywords and return its raw text reply."""
    user_content = "Script: {}\nTimed Captions:{}\n".format(
        script, "".join(map(str, captions_timed))
    )
    raw = router.complete(
        user_content,
        system=PROMPT,
        temperature=1.0,
        max_tokens=8192,
    )
    text = _clean_llm_text(raw)

    try:
        json.loads(text)
        return text
    except json.JSONDecodeError:
        # Try to salvage a truncated array by cutting at the last closing bracket.
        last_bracket = text.rfind("]")
        if last_bracket > 0:
            trimmed = text[: last_bracket + 1]
            try:
                json.loads(trimmed)
                LOGGER.info("Trimmed the model output to %d characters of valid JSON.", len(trimmed))
                return trimmed
            except json.JSONDecodeError:
                pass
        return text


def get_video_search_queries_timed(
    script: str,
    captions_timed,
    router: Optional[SmartLLMRouter] = None,
    max_retries: int = 3,
) -> Optional[List[Any]]:
    """Return [[[t1, t2], [kw1, kw2, kw3]], ...] covering the whole timeline.

    Keeps asking until the last segment ends exactly at the end of the audio, so no
    part of the narration is left without footage, then falls back to keyword
    extraction from the captions.
    """
    if not captions_timed:
        return None
    router = router or get_router()
    end = captions_timed[-1][0][1]
    retry_count = 0

    try:
        out: List[Any] = [[[0, 0], ""]]
        while out[-1][0][1] != end:
            if retry_count >= max_retries:
                LOGGER.warning(
                    "Max retries (%d) reached for timed queries. Using the current result.",
                    max_retries,
                )
                if out == [[[0, 0], ""]]:
                    return local_fallback_queries(script, captions_timed, end)
                return out

            content = call_llm(script, captions_timed, router).replace("'", '"')
            try:
                out = json.loads(content)
            except Exception as exc:  # noqa: BLE001
                LOGGER.info("JSON parse error, attempting to fix: %s", exc)
                try:
                    content = fix_json(content.replace("```json", "").replace("```", ""))
                    out = json.loads(content)
                except Exception as exc2:  # noqa: BLE001
                    LOGGER.info("Failed to fix JSON: %s", exc2)
                    retry_count += 1
                    continue

            if not isinstance(out, list) or not out or not isinstance(out[0], list):
                retry_count += 1
                out = [[[0, 0], ""]]
                continue

            if out[-1][0][1] != end:
                retry_count += 1

        LOGGER.info("Timed search queries built: %d segments covering %.2fs.", len(out), end)
        return out
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("Timed query generation failed (%s); using local keyword extraction.", exc)
        return local_fallback_queries(script, captions_timed, end)


def local_fallback_queries(script: str, captions_timed, end: float) -> List[Any]:
    """Build queries from the words actually spoken in each 4 second window.

    Used when the model cannot be reached or keeps returning unusable output. The
    queries always come from the spoken words plus the main topic words of the script,
    so the visuals stay on topic instead of falling back to generic filler.
    """
    topic_words = [
        word
        for word in re.findall(r"[A-Za-z]{4,}", script)
        if word.lower() not in STOP_WORDS
    ]
    # The most frequent content words describe the overall subject of the video.
    frequency: dict[str, int] = {}
    for word in topic_words:
        key = word.lower()
        frequency[key] = frequency.get(key, 0) + 1
    main_topic = " ".join(
        w for w, _ in sorted(frequency.items(), key=lambda item: item[1], reverse=True)[:2]
    )

    fallback: List[Any] = []
    time_cursor = 0.0
    while time_cursor < end:
        next_time = min(time_cursor + 4.0, end)

        segment_words: List[str] = []
        for (word_start, _word_end), word in captions_timed:
            if time_cursor <= word_start < next_time:
                clean = re.sub(r"[^\w\s]", "", str(word).lower()).strip()
                if clean and clean not in STOP_WORDS and len(clean) > 2:
                    segment_words.append(clean)

        primary = " ".join(segment_words[:3]).strip()
        secondary = " ".join(segment_words[:2]).strip()
        queries = [q for q in (primary, secondary, main_topic) if q]
        if not queries:
            queries = [main_topic or "cinematic background"]

        fallback.append([[time_cursor, next_time], queries])
        time_cursor = next_time

    LOGGER.info("Local fallback produced %d timed segments from the spoken words.", len(fallback))
    return fallback


def merge_empty_intervals(segments):
    """Merge intervals that have no clip with the previous valid one.

    A skipped interval is not harmless. The renderer composites clips onto a black
    background, so any slot that reaches it without a clip shows as a black flash
    at the join between two shots. Every interval must leave this function owning
    a clip.

    The forward merge covers gaps that have a clip before them. A gap at the very
    start has nothing before it, so it is filled backwards from the first clip that
    does exist; otherwise the video opens on black.
    """
    if segments is None:
        LOGGER.warning("No background videos available to merge.")
        return None

    merged = []
    i = 0
    while i < len(segments):
        interval, url = segments[i]
        if url is None:
            j = i + 1
            while j < len(segments) and segments[j][1] is None:
                j += 1

            if i > 0 and merged:
                prev_interval, prev_url = merged[-1]
                if prev_url is not None and prev_interval[1] == interval[0]:
                    merged[-1] = [[prev_interval[0], segments[j - 1][0][1]], prev_url]
                else:
                    merged.append([interval, prev_url])
            else:
                # Leading gap. Keep the whole run as one interval so the backward
                # fill below covers it in a single piece: appending only the first
                # interval of the run used to drop the remainder of the timeline.
                merged.append([[interval[0], segments[j - 1][0][1]], None])
            i = j
        else:
            merged.append([interval, url])
            i += 1

    # Backward fill: borrow the first clip that exists for any leading gap.
    first_url = next((url for _interval, url in merged if url is not None), None)
    if first_url is not None:
        for index, (interval, url) in enumerate(merged):
            if url is not None:
                break
            merged[index] = [interval, first_url]

    return merged
