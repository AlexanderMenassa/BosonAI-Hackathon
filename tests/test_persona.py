from hangon.models import Evidence, Mode, Persona
from hangon.persona import STALL_TACTICS, base_prompt, build_instructions, turn_guidance

P = Persona(name="Margaret")


def test_base_prompt_forbids_accusing_and_repeating():
    b = base_prompt(P)
    assert "never accuse the caller" in b and "scam" in b and "Never repeat" in b


def test_first_stall_line_introduces_the_persona_later_ones_do_not():
    first = turn_guidance(P, Mode.STALL, Evidence(), 2, prev_mode=Mode.SCREEN)
    later = turn_guidance(P, Mode.STALL, Evidence(), 3, prev_mode=Mode.STALL)
    assert "first line as Margaret" in first and "first line" not in later


def test_stall_asks_for_the_first_missing_evidence():
    g = turn_guidance(P, Mode.STALL, Evidence(claimed_org="CRA"), 3, Mode.STALL)
    assert "how exactly they want you to pay" in g
    g = turn_guidance(P, Mode.STALL, Evidence(claimed_org="CRA", payment_method="gift cards"), 3, Mode.STALL)
    assert "number to call them back" in g


def test_stall_with_complete_evidence_just_stalls():
    full = Evidence("CRA", "gift cards", "647-555-0199", "arrest")
    assert "Then:" not in turn_guidance(P, Mode.STALL, full, 3, Mode.STALL)


def test_consecutive_stall_turns_use_different_tactics():
    a = turn_guidance(P, Mode.STALL, Evidence(), 3, Mode.STALL)
    b = turn_guidance(P, Mode.STALL, Evidence(), 4, Mode.STALL)
    assert a != b and any(t in a for t in STALL_TACTICS)


def test_previous_line_is_not_quoted_back_but_repeating_is_forbidden():
    g = turn_guidance(P, Mode.STALL, Evidence(), 3, Mode.STALL, last_line="Could you repeat that?")
    assert "Could you repeat that?" not in g and "do not repeat" in g


def test_screen_asks_for_the_organization_first():
    assert "which organization" in turn_guidance(P, Mode.SCREEN, Evidence(), 1, Mode.GREET)


def test_goodbye_gets_a_concrete_excuse():
    assert "Your excuse:" in turn_guidance(P, Mode.GOODBYE, Evidence(), 4, Mode.STALL)


def test_guidance_is_appended_to_the_instructions():
    assert build_instructions(P, Mode.STALL, "EXTRA").endswith(" EXTRA")
