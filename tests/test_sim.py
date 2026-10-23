"""Whole loop: engine + scripted agent + reports + notifications."""
import asyncio

from hangon.agent import ScriptedAgent
from hangon.classifier import HeuristicClassifier
from hangon.config import Settings
from hangon.models import Outcome, Persona
from hangon.sim import run_call, scripted_lines
from hangon.store import Store


class Capture:
    def __init__(self):
        self.sent = []

    def send(self, to, text, attachment=None):
        self.sent.append((to, text, attachment))


def call(tmp_path, scenario, number="+16475550111", settings=None):
    settings = settings or Settings(reports_dir=str(tmp_path / "reports"), protected_phone="+1PROTECTED",
                                    guardian_phone="+1GUARDIAN")
    store = Store(str(tmp_path / "t.db"))
    cap = Capture()
    s = asyncio.run(run_call(number, scripted_lines(f"scenarios/{scenario}.txt"), HeuristicClassifier(),
                             ScriptedAgent(Persona()), store, settings, cap, echo=False))
    return s, store, cap


def test_scam_call_blocks_notifies_both_people_and_writes_the_evidence_card(tmp_path):
    s, store, cap = call(tmp_path, "cra_scam")
    assert s.outcome is Outcome.SCAM_ENDED and store.is_blocked(s.number)
    recipients = [to for to, _, _ in cap.sent]
    assert recipients.count("+1PROTECTED") == 2 and recipients.count("+1GUARDIAN") == 1
    card = open(cap.sent[1][2]).read()
    assert "Canada Revenue Agency" in card and "gift card" in card and "647-555-0199" in card and "TRANSCRIPT" in card


def test_legit_call_sends_only_a_summary(tmp_path):
    s, store, cap = call(tmp_path, "pharmacy_legit")
    assert s.outcome is Outcome.FORWARDED and len(cap.sent) == 1
    assert "Riverside Pharmacy" in cap.sent[0][1] and ".." not in cap.sent[0][1]


def test_unsure_call_takes_a_message_and_does_not_block(tmp_path):
    s, store, cap = call(tmp_path, "vague_unsure")
    assert s.outcome is Outcome.MESSAGE_TAKEN and not store.is_blocked(s.number) and len(cap.sent) >= 2


def test_second_call_from_a_blocked_number_never_rings(tmp_path):
    call(tmp_path, "cra_scam")
    s, store, cap = call(tmp_path, "pharmacy_legit")  # same number, same db
    assert s.outcome is Outcome.REJECTED and cap.sent == []
