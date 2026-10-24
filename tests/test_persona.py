from hangon.models import Evidence, Mode, Persona
from hangon.persona import base_prompt, build_instructions, exact_line, known_facts, mode_instructions, turn_guidance

P = Persona(name="Margaret")


def test_base_prompt_forbids_accusing_repeating_and_stock_openers():
    b = base_prompt(P)
    assert "never accuse the caller" in b and "scam" in b and "Never repeat" in b
    assert "Never start a reply with 'I understand' or 'I see'" in b


def test_the_assistant_handles_the_whole_call():
    for mode in (Mode.SCREEN, Mode.STALL):
        assert "You are Margaret's assistant" in mode_instructions(P, mode)
    assert "assistant" in exact_line(P, Mode.GREET) and "pass this on to Margaret" in exact_line(P, Mode.GOODBYE)
    assert "never suspicious" in mode_instructions(P, Mode.STALL)


def test_information_carrying_lines_are_exact():
    for m in (Mode.GREET, Mode.HANDOFF, Mode.HOLD, Mode.TAKE_MESSAGE, Mode.GOODBYE):
        assert exact_line(P, m) in mode_instructions(P, m)
    assert "put you through" in exact_line(P, Mode.HANDOFF)


def test_guidance_is_appended_to_the_instructions():
    assert build_instructions(P, Mode.STALL, "EXTRA").endswith(" EXTRA")


def test_fixed_lines_get_no_guidance():
    for m in (Mode.GREET, Mode.HANDOFF, Mode.HOLD, Mode.TAKE_MESSAGE, Mode.GOODBYE):
        assert turn_guidance(m, {}) == ""


# ---------------------------------------------------------------- what is known, what is not
def test_a_first_turn_lists_everything_as_unknown_and_lets_the_agent_choose():
    g = turn_guidance(Mode.SCREEN, {})
    for wanted in ("their name", "what the call is about", "which organization", "a number to call them back on"):
        assert wanted in g
    assert "already told you" not in g and "whichever fits most naturally" in g


def test_what_the_caller_already_said_is_never_asked_again():
    """Call #43: the agent asked for the name after Doug gave it, and the organization three times."""
    known = {"name": "Doug Dimmadome", "organization": "Iamnotascammer.com", "number": "4165551047"}
    g = turn_guidance(Mode.SCREEN, known)
    assert "name: Doug Dimmadome" in g and "Never ask for any of this again" in g
    unknown = g.split("Still unknown:")[1].split("Ask about ONE")[0]
    assert "what the call is about" in unknown
    for settled in ("their name", "which organization", "a number to call them back on"):
        assert settled not in unknown


def test_when_everything_basic_is_known_the_agent_asks_about_the_request_itself():
    known = {"name": "Doug", "purpose": "a payment", "organization": "ACME", "number": "4165551047"}
    g = turn_guidance(Mode.SCREEN, known)
    assert "Still unknown" not in g and "about the request itself" in g


def test_stalling_also_wants_to_know_how_the_money_moves_screening_does_not():
    assert "how the money or payment" not in turn_guidance(Mode.SCREEN, {})
    assert "how the money or payment" in turn_guidance(Mode.STALL, {"name": "Davis"})
    assert "how the money or payment" not in turn_guidance(Mode.STALL, {"payment": "gift cards"})


def test_guidance_asks_for_a_specific_reaction_and_bans_stock_openers_and_repeats():
    for mode in (Mode.SCREEN, Mode.STALL):
        g = turn_guidance(mode, {})
        assert "react in a few words to something specific" in g
        assert "Never start with 'I understand'" in g and "do not greet" in g and "do not repeat" in g


def test_known_facts_come_from_the_judge_and_the_running_evidence():
    class Judged:
        caller_name, purpose = "Doug", "looking for a helpless old person"
    ev = Evidence(claimed_org="Iamnotascammer.com", callback_number="4165551047", payment_method="gift cards")
    assert known_facts(Judged(), ev) == {"name": "Doug", "purpose": "looking for a helpless old person",
                                         "organization": "Iamnotascammer.com", "number": "4165551047",
                                         "payment": "gift cards"}


def test_known_facts_cope_with_a_result_that_has_no_judge_fields_or_no_result_yet():
    assert known_facts(None, Evidence()) == {}
    assert known_facts(object(), Evidence(claimed_org="CRA")) == {"organization": "CRA"}
