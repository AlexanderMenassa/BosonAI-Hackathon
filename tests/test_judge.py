"""The judge: behaviours with quotes in, a verdict and a score out. All decided by code, so all testable."""
from pathlib import Path

import pytest

from hangon.classifier import (HARD_BEHAVIOURS, SOFT_BEHAVIOURS, SYSTEM_PROMPT, HeuristicClassifier, describe,
                               grounded_behaviours, heuristic_behaviours, is_grounded, make_result, verdict_from)
from hangon.config import Settings
from hangon.models import Evidence, Label

ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------- scoring: the only place a verdict is decided
def test_one_hard_behaviour_clears_the_default_scam_threshold_soft_ones_never_do():
    threshold = Settings().scam_enter
    label, score = verdict_from([("secrecy", "dont tell")], None)
    assert label is Label.SCAM and score >= threshold
    for soft_count in (1, 2, 3, 5):
        label, score = verdict_from([(k, "x") for k in list(SOFT_BEHAVIOURS)[:soft_count]], None)
        assert label is Label.UNSURE and score < threshold


def test_the_score_rises_step_by_step_as_behaviours_appear():
    seen, scores = [], []
    for step in (("impersonates_authority", "q"), ("urgency", "q"), ("threat", "q"), ("unusual_payment", "q"),
                 ("secrecy", "q")):
        seen.append(step)
        scores.append(verdict_from(list(seen), None)[1])
    assert scores == sorted(scores) and len(set(scores)) == len(scores)      # strictly rising
    assert scores[0] < 0.7 < 0.85 < scores[2] <= scores[-1] <= 0.99


def test_ordinary_purpose_is_legit_only_when_nothing_suspicious_was_seen():
    assert verdict_from([], "your prescription is ready") == (Label.LEGIT, 0.10)
    assert verdict_from([("urgency", "q")], "your prescription is ready")[0] is Label.UNSURE
    assert verdict_from([("threat", "q")], "your prescription is ready")[0] is Label.SCAM
    assert verdict_from([], None) == (Label.UNSURE, 0.50)


def test_every_behaviour_type_is_hard_or_soft_never_both():
    assert not set(HARD_BEHAVIOURS) & set(SOFT_BEHAVIOURS)
    for kind in {**HARD_BEHAVIOURS, **SOFT_BEHAVIOURS}:
        assert kind in SYSTEM_PROMPT                                          # the model is told about every type


def test_the_reason_quotes_the_caller_so_a_human_can_check_it():
    r = make_result([("unusual_payment", "gift cards"), ("secrecy", "dont tell anyone")], None, Evidence(), True,
                    "s", "t")
    assert r.reason == 'unusual payment: "gift cards"; secrecy: "dont tell anyone"'
    assert describe([], "the book is ready") == 'ordinary purpose: "the book is ready"'
    assert describe([], None) == "no scam behaviour so far"


# ---------------------------------------------------------------- grounding: no quote, no behaviour
def test_a_quote_is_grounded_when_every_meaningful_word_was_said():
    said = "Well you've already asked, and I'm looking for a whole helpless old person with gift cards"
    assert is_grounded("looking for a helpless old person with gift cards", said)
    assert is_grounded("You've already asked", said)                          # apostrophes do not matter
    assert not is_grounded("send the money by courier", said)
    assert not is_grounded("", said) and not is_grounded("the and for", said)


def test_ungrounded_unknown_and_malformed_behaviours_are_dropped_duplicates_collapse():
    said = "Pay with gift cards and do not tell anyone. Pay with gift cards."
    raw = [{"type": "unusual_payment", "quote": "Pay with gift cards"},
           {"type": "unusual_payment", "quote": "gift cards"},                  # same type again: first quote kept
           {"type": "secrecy", "quote": "do not tell anyone"},
           {"type": "threat", "quote": "you will be arrested"},                 # never said
           {"type": "made_up_type", "quote": "gift cards"},                     # not a real type
           {"type": ["unhashable"], "quote": "gift cards"}, {"quote": "gift cards"}, "gift cards", None]
    assert grounded_behaviours(raw, said) == [("unusual_payment", "Pay with gift cards"),
                                              ("secrecy", "do not tell anyone")]
    assert grounded_behaviours(None, said) == [] and grounded_behaviours("scam", said) == []


# ---------------------------------------------------------------- the keyword judge on the cases that mattered
def judge(*caller_lines):
    return HeuristicClassifier().classify([("caller", line) for line in caller_lines])


def test_call_43_is_flagged_on_the_first_turn():
    """'Looking for a helpless old person with a credit card' stayed UNSURE for six turns."""
    r = judge("Hello, my name's Doug Dimmadome, and I'm looking for a helpless old person with a credit card.")
    assert r.label is Label.SCAM and r.scam_score >= Settings().scam_enter
    assert r.behaviours[0][0] == "targets_vulnerable" and "helpless old" in r.behaviours[0][1]
    assert r.caller_name == "Doug Dimmadome"


def test_naming_an_agency_is_only_suspicion_the_threat_makes_it_a_scam():
    assert judge("Hello, this is Officer Davis from the Canada Revenue Agency.").label is Label.UNSURE
    assert judge("Hello, this is Officer Davis from the Canada Revenue Agency.",
                 "A warrant will be issued for your arrest within 24 hours.").label is Label.SCAM


def test_a_grandchild_is_unsure_until_money_or_secrecy_appears():
    assert judge("Hi Grandma? It's me.", "I have a cold, my voice sounds different.").label is Label.UNSURE
    r = judge("Hi Grandma? It's me.", "They're saying I need to pay to get out tonight.")
    assert r.label is Label.SCAM and ("moves_money", "pay to get out") in r.behaviours
    assert judge("Please don't tell Mom and Dad, they'll be so angry.").label is Label.SCAM


def test_a_real_alert_that_says_what_it_will_never_ask_is_not_flagged():
    r = judge("This is a fraud alert from Scotiabank. We will never ask for your PIN, passwords, or one-time "
              "codes, and we never ask you to buy gift cards. Please call the number on the back of your card.")
    assert r.label is Label.UNSURE and r.behaviours == []


def test_the_same_words_are_flagged_when_it_is_the_caller_asking():
    assert judge("Please read me the six-digit code we just texted you.").label is Label.SCAM
    assert judge("You need to pay with store cards from the pharmacy.").label is Label.SCAM


def test_an_ordinary_call_needs_two_ordinary_cues_before_it_is_legit():
    assert judge("Hi, this is Riverside Pharmacy calling.").label is Label.UNSURE
    r = judge("Hi, this is Riverside Pharmacy calling.", "Margaret's prescription is ready for pickup.")
    assert r.label is Label.LEGIT and r.scam_score == 0.10
    assert judge("A small payment is due when she picks up her prescription at the pharmacy.").label is Label.LEGIT


def test_only_the_callers_words_count():
    r = HeuristicClassifier().classify([("agent", "I will never buy gift cards, and I can't read you a code."),
                                        ("caller", "Hello?")])
    assert r.label is Label.UNSURE and r.behaviours == []


@pytest.mark.parametrize("scenario,first_scam_turn", [
    ("cra_scam", 2), ("grandchild_scam", 5), ("bank_safe_account_scam", 5), ("real_bank_alert", None),
    ("pharmacy_legit", None), ("vague_unsure", None),
])
def test_scenarios_turn_scam_when_the_caller_asks_for_something_not_before(scenario, first_scam_turn):
    lines = [l.strip() for l in (ROOT / "scenarios" / f"{scenario}.txt").read_text(encoding="utf-8").splitlines()
             if l.strip() and not l.startswith("#")]
    labels = [judge(*lines[:i + 1]).label for i in range(len(lines))]
    first = next((i + 1 for i, label in enumerate(labels) if label is Label.SCAM), None)
    assert first == first_scam_turn


def test_every_hard_cue_has_a_matching_behaviour_name():
    assert {k for k, _ in heuristic_behaviours(
        "gift cards. arrested. do not tell anyone. remote access. send the money. looking for a helpless old person")
    } == set(HARD_BEHAVIOURS)


def test_call_43_through_the_engine_is_a_scam_on_turn_one_and_the_number_is_blocked(tmp_path):
    from hangon.engine import CallSession
    from hangon.store import Store
    store = Store(str(tmp_path / "t.db"))
    session = CallSession("+14165551047", HeuristicClassifier(), store, Settings())
    session.open()
    session.on_caller_turn("Hello, my name's Doug Dimmadome, and I'm looking for a helpless old person with a "
                           "credit card.")
    assert session.verdict is Label.SCAM and session.turns == 1
    session.finish()
    assert store.is_blocked("+14165551047")
    assert "targets vulnerable" in session.last_result.reason          # the evidence card says why, in his words
