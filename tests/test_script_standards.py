"""Tests for the 2026 script rules.

These protect findings that are easy to silently undo: the Shorts hook window,
the separation of content and visual pacing, the loop ending, and format
rotation across uploads.
"""

from __future__ import annotations


from utility.content.script_generator import PROMPT_TEMPLATE, SOURCE_PROMPT_TEMPLATE
from utility.content.script_standards import (
    SCRIPT_FORMATS,
    build_structure_block,
    choose_format,
    ending_spec,
    hook_spec,
    is_short,
    output_fields,
    pacing_spec,
    remember_format,
)
from utility.publishing.algorithmic_standards import (
    TARGETS,
    content_interrupt_target,
    hook_length_target,
)

SHORT = 30
LONG = 600


# ----------------------------------------------------------------------
# Hook window
# ----------------------------------------------------------------------
def test_shorts_hook_is_one_second_not_three() -> None:
    """The swipe decision lands at or before one second."""
    spec = hook_spec(SHORT)
    assert spec["seconds"] <= 1.0
    assert "FIRST FRAME" in spec["instruction"]


def test_longform_hook_has_ten_seconds_and_three_jobs() -> None:
    spec = hook_spec(LONG)
    assert spec["seconds"] == 10.0
    text = spec["instruction"].lower()
    for job in ("validate the click", "raise the stakes", "curiosity loop"):
        assert job in text


def test_hook_target_matches_the_lane() -> None:
    assert hook_length_target(SHORT) == 1.0
    assert hook_length_target(LONG) == 10.0


def test_no_prompt_tells_the_model_the_hook_is_three_seconds() -> None:
    """The stale three-second rule must not survive anywhere."""
    for duration in (SHORT, LONG):
        block = build_structure_block(duration, 100, "story")
        assert "first 3 seconds" not in block.lower()


# ----------------------------------------------------------------------
# Pacing: content vs visual are different things
# ----------------------------------------------------------------------
def test_content_and_visual_intervals_are_separate() -> None:
    longform = pacing_spec(LONG)
    assert longform["content_interval"] >= 60
    assert longform["visual_interval"] <= 5
    assert longform["content_interval"] != longform["visual_interval"]


def test_shorts_pace_faster_than_longform_on_both_axes() -> None:
    short, long_form = pacing_spec(SHORT), pacing_spec(LONG)
    assert short["content_interval"] < long_form["content_interval"]
    assert short["visual_interval"] <= long_form["visual_interval"]


def test_targets_expose_both_intervals_and_keep_the_legacy_key() -> None:
    assert TARGETS["visual_change_interval_seconds"] == 4.0
    assert content_interrupt_target(LONG) >= 60
    assert content_interrupt_target(SHORT) < 10
    # Older saved reports referenced this name.
    assert "pattern_interrupt_interval_seconds" in TARGETS


# ----------------------------------------------------------------------
# Endings
# ----------------------------------------------------------------------
def test_shorts_end_on_a_loop() -> None:
    spec = ending_spec(SHORT)
    assert spec["kind"] == "loop"
    assert "loop_line" in spec["instruction"]


def test_longform_ends_by_opening_a_door_and_stays_on_youtube() -> None:
    spec = ending_spec(LONG)
    assert spec["kind"] == "session"
    assert "next_hook" in spec["instruction"]
    assert "off YouTube" in spec["instruction"]


def test_output_shape_asks_for_the_right_ending_field() -> None:
    assert "loop_line" in output_fields(SHORT)
    assert "next_hook" not in output_fields(SHORT)
    assert "next_hook" in output_fields(LONG)
    assert "loop_line" not in output_fields(LONG)


# ----------------------------------------------------------------------
# Prompt integrity
# ----------------------------------------------------------------------
def test_json_shape_uses_single_braces_after_formatting() -> None:
    """The shape is injected after .format(), so doubled braces would reach the model."""
    for template, extra in (
        (PROMPT_TEMPLATE, {}),
        (SOURCE_PROMPT_TEMPLATE, {"source_material": "m", "site": "s"}),
    ):
        for duration in (SHORT, LONG):
            rendered = template.format(
                topic="t", style_name="s", tone="t", pacing="p", duration=duration,
                aspect_ratio="9:16", format_lane="lane", structure="[S]",
                output_shape=output_fields(duration), query_count=6,
                visual_keywords="a", **extra,
            )
            shape = rendered[rendered.index("{"):rendered.rindex("}") + 1]
            assert "{{" not in shape and "}}" not in shape


def test_both_prompts_carry_the_same_core_rules() -> None:
    """A rule fixed in one path must not be missing from the other."""
    topic = PROMPT_TEMPLATE.format(
        topic="t", style_name="s", tone="t", pacing="p", duration=SHORT,
        aspect_ratio="9:16", format_lane="l",
        structure=build_structure_block(SHORT, 70, "story"),
        output_shape=output_fields(SHORT), query_count=6, visual_keywords="a",
    )
    sourced = SOURCE_PROMPT_TEMPLATE.format(
        source_material="m", site="s", style_name="s", tone="t", pacing="p",
        duration=SHORT, aspect_ratio="9:16", format_lane="l",
        structure=build_structure_block(SHORT, 70, "story", sourced=True),
        output_shape=output_fields(SHORT, sourced=True), query_count=6,
        visual_keywords="a",
    )
    for rule in ("FIRST FRAME", "END ON A LOOP", "WRITE FOR THE EAR",
                 "WRITING ORDER", "ORIGINALITY", "Treat this as a ceiling"):
        assert rule in topic, f"missing from topic prompt: {rule}"
        assert rule in sourced, f"missing from source prompt: {rule}"


def test_source_prompt_keeps_its_anti_invention_rules() -> None:
    sourced = SOURCE_PROMPT_TEMPLATE.format(
        source_material="m", site="s", style_name="s", tone="t", pacing="p",
        duration=LONG, aspect_ratio="16:9", format_lane="l",
        structure=build_structure_block(LONG, 100, "story", sourced=True),
        output_shape=output_fields(LONG, sourced=True), query_count=6,
        visual_keywords="a",
    )
    assert "Never invent statistics" in sourced
    assert "Never pad" in sourced
    assert "write a shorter script" in sourced


def test_length_rule_frames_the_count_as_a_ceiling() -> None:
    block = build_structure_block(LONG, 1400, "story")
    assert "ceiling, not a quota" in block
    assert "finish early" in block


# ----------------------------------------------------------------------
# Format rotation
# ----------------------------------------------------------------------
def test_every_format_declares_the_fields_the_prompt_uses() -> None:
    for key, fmt in SCRIPT_FORMATS.items():
        for field in ("label", "retention", "weight", "spine", "hook_formula",
                      "body_rule", "tip"):
            assert fmt.get(field), f"{key} is missing {field}"


def test_style_hints_steer_the_format() -> None:
    assert choose_format("Tutorial", LONG, None) in {"tutorial", "explainer"}
    assert choose_format("True Crime", LONG, None) in {"investigation", "story"}
    assert choose_format("Countdown", LONG, None) == "listicle"


def test_explicit_format_wins() -> None:
    assert choose_format("Tutorial", LONG, "listicle") == "listicle"


def test_rotation_avoids_immediate_repeats(tmp_path, monkeypatch) -> None:
    import utility.content.script_standards as standards

    monkeypatch.setattr(standards, "FORMAT_HISTORY_FILE", str(tmp_path / "h.json"))
    sequence = []
    for _ in range(40):
        chosen = standards.choose_format("Documentary", LONG)
        standards.remember_format(chosen)
        sequence.append(chosen)

    repeats = sum(1 for a, b in zip(sequence, sequence[1:]) if a == b)
    # Selection is deliberately stochastic, so this is a distribution bound, not a
    # hard rule. Measured over 300 trials of 40 picks: mean 3.1, p99 8, max 9.
    # Pure chance over three candidate formats would give about 13.
    assert repeats <= 10, f"rotation is not damping repeats: {repeats} of 39"
    assert len(set(sequence)) >= 2


def test_rotation_is_not_a_fixed_cycle(tmp_path, monkeypatch) -> None:
    """A perfect A-B-C-A-B-C rotation is just a slower template."""
    import utility.content.script_standards as standards

    monkeypatch.setattr(standards, "FORMAT_HISTORY_FILE", str(tmp_path / "h.json"))
    sequence = []
    for _ in range(30):
        chosen = standards.choose_format("Documentary", LONG)
        standards.remember_format(chosen)
        sequence.append(chosen)

    distinct = len(set(sequence))
    cyclic = distinct > 1 and all(
        sequence[i] == sequence[i + distinct] for i in range(len(sequence) - distinct)
    )
    assert not cyclic, f"format choice is perfectly periodic: {sequence[:9]}"


def test_history_failure_never_breaks_generation(monkeypatch) -> None:
    import utility.content.script_standards as standards

    monkeypatch.setattr(standards, "FORMAT_HISTORY_FILE", "/nonexistent/dir/h.json")
    remember_format("story")           # must not raise
    assert choose_format("Cinematic", LONG) in SCRIPT_FORMATS


# ----------------------------------------------------------------------
# Lane detection
# ----------------------------------------------------------------------
def test_lane_boundary() -> None:
    assert is_short(119) and not is_short(120)


def test_shorts_block_front_loads_and_longform_uses_a_retention_trap() -> None:
    short_block = build_structure_block(SHORT, 70, "story")
    long_block = build_structure_block(LONG, 1400, "story")
    assert "FRONT-LOAD THE PAYOFF" in short_block
    assert "SOUND-OFF READABILITY" in short_block
    assert "RETENTION TRAP" in long_block
    assert "INTERNAL TEASERS" in long_block
