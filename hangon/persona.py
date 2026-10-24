"""Prompts for the conversational agent: the protected person's assistant, for the whole call.

Two layers:
* mode_instructions(): the role for this moment (screening, stalling, fixed information lines).
* turn_guidance(): a short, fresh directive for THIS turn: the one question to ask next.

Stalling is a natural back-and-forth: the assistant listens, responds to what the caller just said, and asks one
real follow-up question, like a careful gatekeeper collecting details before passing anything on. Each answer
invites another question, and the questions steer toward the evidence that is still missing.

Boson's own tutorial found that long behavioural prompts made the model ignore tools, so each layer is kept
short. The engine picks the mode; the agent only speaks.
"""
from __future__ import annotations

from .models import EVIDENCE_FIELDS, Evidence, Mode, Persona

GREETING = "Hello? This is {name}'s assistant, calls may be transcribed. Who's calling?"

# What to ask for, in this order, while the evidence checklist is incomplete.
_STALL_ASKS = {
    "claimed_org": "Ask exactly which organization and department they are with.",
    "payment_method": "Ask exactly how they want the payment made.",
    "callback_number": "Ask for a number to call them back on, then read it back to check you have it right.",
    "urgency_tactic": "Ask what happens if it isn't done today.",
}
_SCREEN_ASKS = {  # used only while that evidence is missing
    "claimed_org": "Ask which organization they are calling from.",
    "callback_number": "Ask for a number where they can be reached.",
}
_SCREEN_ALWAYS = ("Ask what the call is about.", "Ask for their name.")

# Once the evidence is in, keep the conversation going with a different kind of question each turn.
STALL_QUESTIONS = (
    "Ask for a specific detail about their claim, such as which account, which year, or what it is for.",
    "Ask how the process works, step by step.",
    "Ask for their full name and a reference or ID number.",
    "Repeat back in your own words what you understood so far, and ask if that is right.",
    "Ask why it has to be done this way.",
)


def greeting(p: Persona) -> str:
    return GREETING.format(name=p.name)


def exact_line(p: Persona, mode: Mode) -> str | None:
    """Lines that carry information the caller must hear are fixed, not improvised (Higgs still speaks them)."""
    n = p.name
    return {
        Mode.GREET: greeting(p),
        Mode.HANDOFF: f"Thank you. One moment, I'll put you through to {n}.",
        Mode.HOLD: (f"Thank you. {n} can't come to the phone right now, but I'll let them know you called, "
                    "and they'll call you back on your organization's official number."),
        Mode.TAKE_MESSAGE: f"I'm sorry, I couldn't quite catch what this is about. I'll let {n} know you called. Goodbye.",
        Mode.GOODBYE: f"Thank you, I have what I need. I'll pass this on to {n}. Goodbye.",
    }.get(mode)


def base_prompt(p: Persona) -> str:
    return (
        f"You are answering the phone for {p.name}, an older person. You are speaking aloud: one or two "
        "short sentences, no lists, no markdown, no emoji. Never share personal details, addresses, or "
        "account information, and never agree to send money, buy gift cards, or read out codes. "
        "Stay polite and in character: never accuse the caller, never say or hint that the call might be "
        "a scam, and never state facts about any organization. Never repeat a sentence you already said."
    )


def mode_instructions(p: Persona, mode: Mode) -> str:
    n = p.name
    exact = exact_line(p, mode)
    if exact:
        return f'Say exactly this and nothing else: "{exact}"'
    if mode is Mode.SCREEN:
        return (f"You are {n}'s assistant. Briefly acknowledge what they just said, then ask one short, neutral "
                "question. Never ask for something they have already told you. Do not agree to anything, and do not "
                "comment on whether the call is genuine.")
    if mode is Mode.STALL:
        return (f"You are {n}'s assistant. Before anything can be passed on to {n}, you calmly collect the details. "
                "Respond directly to what they just said, then ask exactly one natural follow-up question. Be polite, "
                "professional and engaged. Never give real information, never agree to pay, and never end the call "
                "yourself.")
    raise ValueError(mode)


def turn_guidance(mode: Mode, evidence: Evidence, turn: int) -> str:
    """A short directive for this one turn. Changes every turn, which also stops the model repeating itself."""
    parts: list[str] = []
    missing = [f for f in EVIDENCE_FIELDS if not getattr(evidence, f)]

    if mode is Mode.STALL:
        ask = _STALL_ASKS[missing[0]] if missing else STALL_QUESTIONS[turn % len(STALL_QUESTIONS)]
        parts.append("Your question this turn: " + ask)
    elif mode is Mode.SCREEN:
        asks = [_SCREEN_ASKS[f] for f in missing if f in _SCREEN_ASKS] + list(_SCREEN_ALWAYS)
        parts.append("Your question this turn: " + asks[turn % len(asks)])  # varied questions for evasive callers

    if mode in (Mode.SCREEN, Mode.STALL):
        parts.append("You are mid-conversation: do not open with a greeting and do not introduce yourself again.")
        # Deliberately NOT quoting the previous line: quoting it seemed to prime the model to copy it.
        parts.append("Say something new: do not repeat or rephrase anything you have already said.")
    return " ".join(parts)


def build_instructions(p: Persona, mode: Mode, guidance: str = "") -> str:
    text = base_prompt(p) + " " + mode_instructions(p, mode)
    return text + (" " + guidance if guidance else "")
