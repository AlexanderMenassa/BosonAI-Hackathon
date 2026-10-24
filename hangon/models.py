"""Shared data types. No logic that talks to the outside world lives here."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Label(str, Enum):
    LEGIT = "legit"
    UNSURE = "unsure"
    SCAM = "scam"


class Mode(str, Enum):
    """What the conversational agent should be doing on its next utterance."""
    GREET = "greet"                # opening line with the transcription notice
    SCREEN = "screen"              # unsure: ask who/which org/why
    STALL = "stall"                # scam: keep them talking with polite follow-up questions
    HANDOFF = "handoff"            # legit: "one moment" before forwarding
    HOLD = "hold"                  # legit but high-stakes: not forwarded
    TAKE_MESSAGE = "take_message"  # unsure at the cap: take a message, end
    GOODBYE = "goodbye"            # scam call finished: natural excuse to hang up


class Action(str, Enum):
    """What the *server* should do. The agent never decides this."""
    REJECT = "reject"                    # blocklisted: never rings
    FORWARD_TRUSTED = "forward_trusted"  # trusted contact: no screening
    ANSWER = "answer"                    # start screening
    CONTINUE = "continue"                # keep the call going
    FORWARD = "forward"                  # transfer to the protected person
    END = "end"                          # finish after the agent's last line


class Outcome(str, Enum):
    REJECTED = "rejected"
    FORWARDED_TRUSTED = "forwarded_trusted"
    FORWARDED = "forwarded"
    HELD_CALLBACK = "held_callback"      # legit but high-stakes
    MESSAGE_TAKEN = "message_taken"      # unsure at the cap
    SCAM_ENDED = "scam_ended"            # scam, evidence complete or cap hit
    SCAM_HANGUP = "scam_hangup"          # scam, caller hung up
    HANGUP_UNSURE = "hangup_unsure"      # caller left before a verdict


EVIDENCE_FIELDS = ("claimed_org", "payment_method", "callback_number", "urgency_tactic")


@dataclass
class Evidence:
    claimed_org: str | None = None
    payment_method: str | None = None
    callback_number: str | None = None
    urgency_tactic: str | None = None

    def merge(self, other: "Evidence") -> None:
        """Keep the first value seen for each field."""
        for f in EVIDENCE_FIELDS:
            if getattr(self, f) is None and getattr(other, f):
                setattr(self, f, getattr(other, f))

    @property
    def complete(self) -> bool:
        return all(getattr(self, f) for f in EVIDENCE_FIELDS)

    @property
    def count(self) -> int:
        return sum(1 for f in EVIDENCE_FIELDS if getattr(self, f))

    def to_dict(self) -> dict:
        return {f: getattr(self, f) for f in EVIDENCE_FIELDS}


@dataclass
class ClassifierResult:
    label: Label
    scam_score: float                 # 0 = surely legit, 1 = surely scam
    reason: str
    evidence: Evidence = field(default_factory=Evidence)
    high_stakes: bool = False         # bank, government, money
    caller_summary: str = ""          # one line for the text summary
    source: str = "unknown"           # which classifier produced this


@dataclass
class Persona:
    name: str = "Margaret"            # the protected person; the agent is always her assistant


@dataclass
class Decision:
    action: Action
    mode: Mode | None = None          # what the agent says next (None: nothing)
    outcome: Outcome | None = None    # set when the call is over
    note: str = ""                    # why (for logs)
