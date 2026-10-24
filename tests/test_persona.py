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


def test_repeating_is_forbidden_every_conversational_turn():
    for mode in (Mode.SCREEN, Mode.STALL, Mode.GOODBYE):
        assert "do not repeat" in turn_guidance(P, mode, Evidence(), 3, Mode.STALL)


def test_screening_questions_rotate_and_skip_what_is_known():
    asks = [turn_guidance(P, Mode.SCREEN, Evidence(), t, Mode.SCREEN) for t in range(4)]
    assert len({a.split(" You are")[0] for a in asks}) == 4          # four different questions
    known = [turn_guidance(P, Mode.SCREEN, Evidence(claimed_org="CRA"), t, Mode.SCREEN) for t in range(3)]
    assert not any("which organization" in k for k in known)        # never re-ask what they said


def test_no_greeting_mid_call_but_first_margaret_line_may_introduce_her():
    assert "do not open with a greeting" in turn_guidance(P, Mode.STALL, Evidence(), 4, Mode.STALL)
    assert "do not open with a greeting" not in turn_guidance(P, Mode.STALL, Evidence(), 2, Mode.SCREEN)


def test_information_carrying_lines_are_exact():
    from hangon.persona import exact_line, mode_instructions
    for m in (Mode.HANDOFF, Mode.HOLD, Mode.TAKE_MESSAGE):
        assert exact_line(P, m) in mode_instructions(P, m)
    assert "put you through" in exact_line(P, Mode.HANDOFF)


def test_goodbye_gets_a_concrete_excuse():
    assert "Your excuse:" in turn_guidance(P, Mode.GOODBYE, Evidence(), 4, Mode.STALL)


def test_guidance_is_appended_to_the_instructions():
    assert build_instructions(P, Mode.STALL, "EXTRA").endswith(" EXTRA")
