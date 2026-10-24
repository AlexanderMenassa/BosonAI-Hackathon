from hangon.models import Evidence, Mode, Persona
from hangon.persona import STALL_QUESTIONS, base_prompt, build_instructions, exact_line, mode_instructions, turn_guidance

P = Persona(name="Margaret")


def test_base_prompt_forbids_accusing_and_repeating():
    b = base_prompt(P)
    assert "never accuse the caller" in b and "scam" in b and "Never repeat" in b


def test_the_assistant_handles_the_whole_call():
    for mode in (Mode.SCREEN, Mode.STALL):
        assert "You are Margaret's assistant" in mode_instructions(P, mode)
    assert "assistant" in exact_line(P, Mode.GREET) and "pass this on to Margaret" in exact_line(P, Mode.GOODBYE)


def test_stalling_is_engaged_back_and_forth():
    m = mode_instructions(P, Mode.STALL)
    assert "respond directly to what they just said" in m.lower() and "one natural follow-up question" in m


def test_stall_asks_for_the_first_missing_evidence():
    assert "how they want the payment made" in turn_guidance(Mode.STALL, Evidence(claimed_org="CRA"), 3)
    ev = Evidence(claimed_org="CRA", payment_method="gift cards")
    assert "number to call them back" in turn_guidance(Mode.STALL, ev, 3)


def test_one_question_per_turn():
    assert turn_guidance(Mode.STALL, Evidence(), 3).count("Your question this turn:") == 1


def test_with_evidence_complete_the_questions_keep_varying():
    full = Evidence("CRA", "gift cards", "647-555-0199", "arrest")
    a, b = turn_guidance(Mode.STALL, full, 3), turn_guidance(Mode.STALL, full, 4)
    assert a != b and any(q in a for q in STALL_QUESTIONS)


def test_screening_questions_rotate_and_skip_what_is_known():
    asks = [turn_guidance(Mode.SCREEN, Evidence(), t).split(" You are")[0] for t in range(4)]
    assert len(set(asks)) == 4
    known = [turn_guidance(Mode.SCREEN, Evidence(claimed_org="CRA"), t) for t in range(3)]
    assert not any("which organization" in k for k in known)


def test_conversational_turns_never_greet_again_or_repeat():
    for mode in (Mode.SCREEN, Mode.STALL):
        g = turn_guidance(mode, Evidence(), 3)
        assert "do not open with a greeting" in g and "do not repeat" in g


def test_information_carrying_lines_are_exact():
    for m in (Mode.GREET, Mode.HANDOFF, Mode.HOLD, Mode.TAKE_MESSAGE, Mode.GOODBYE):
        assert exact_line(P, m) in mode_instructions(P, m)
    assert "put you through" in exact_line(P, Mode.HANDOFF)


def test_guidance_is_appended_to_the_instructions():
    assert build_instructions(P, Mode.STALL, "EXTRA").endswith(" EXTRA")
