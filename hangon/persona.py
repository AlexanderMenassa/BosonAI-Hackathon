"""Prompts for the conversational agent.

Two layers:
* mode_instructions(): the role for this moment (assistant screening, Margaret stalling, ...).
* turn_guidance(): a short, fresh directive for THIS turn: what evidence is still missing, which
  stalling tactic to use, and the agent's previous line so it does not repeat itself.

Boson's own tutorial found that long behavioural prompts made the model ignore tools, so each layer is
kept short. The engine picks the mode; the agent only speaks.
"""
from __future__ import annotations

from .models import EVIDENCE_FIELDS, Evidence, Mode, Persona

GREETING = "Hello? This is {name}'s assistant, calls may be transcribed. Who's calling?"

_CHATTINESS = {
    "low": "You keep your little stories to a sentence.",
    "medium": "You add a short story or aside now and then.",
    "high": "You love to ramble with friendly little stories from your life.",
}

# What to ask for, in this order, while the evidence checklist is incomplete.
_STALL_ASKS = {
    "claimed_org": "Ask, confused, which organization they are with, and ask them to spell it.",
    "payment_method": "Ask, confused, how exactly they want you to pay.",
    "callback_number": "Ask for a number to call them back, and get them to repeat it slowly.",
    "urgency_tactic": "Ask, worried, what happens if you can't do it today.",
}
_SCREEN_ASKS = {
    "claimed_org": "Ask which organization they are calling from.",
    "callback_number": "Ask for a number where they can be reached.",
}

# Rotated so consecutive turns never use the same stalling move.
STALL_TACTICS = (
    "Ask them to spell their name slowly, and get one letter wrong.",
    "Drift into a short story from your life, then ask what they were saying.",
    "Say you need to find a pen and paper, then ask them to start again.",
    "Mishear one word they just said and ask about it.",
    "Ask a slow, confused question about how this works.",
)
GOODBYE_EXCUSES = (
    "someone is at the door",
    "the kettle is whistling",
    "your neighbour has just come over",
    "you need to go and lie down",
)


def greeting(p: Persona) -> str:
    return GREETING.format(name=p.name)


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
    if mode is Mode.GREET:
        return f'Say exactly this and nothing else: "{greeting(p)}"'
    if mode is Mode.SCREEN:
        return (f"You are {n}'s assistant. Politely ask one short, neutral question to find out who is calling, "
                "which organization they are with, or what it is about. Do not agree to anything, and do not "
                "comment on whether the call is genuine.")
    if mode is Mode.STALL:
        return (f"You are now {n}, age {p.age}, {p.backstory}. You have taken the phone from your assistant. "
                f"Be warm, a bit confused, and slow to understand. {_CHATTINESS.get(p.chattiness, '')} "
                "Keep them talking. Never give real information, never agree to pay, and never end the call yourself.")
    if mode is Mode.HANDOFF:
        return 'Say one short sentence such as "Oh, one moment, I\'ll put you through." Nothing else.'
    if mode is Mode.HOLD:
        return (f"Politely say {n} can't come to the phone right now, you will pass on that they called, "
                "and that they will call back using the organization's official number. One or two short sentences.")
    if mode is Mode.TAKE_MESSAGE:
        return (f"Politely say you couldn't quite catch what this is about, that you will pass a message to {n}, "
                "and say goodbye. Two short sentences.")
    if mode is Mode.GOODBYE:
        return (f"You are {n}. Make a warm, natural excuse to end the call and say goodbye. Stay friendly and "
                "fully in character. Two short sentences.")
    raise ValueError(mode)


def turn_guidance(p: Persona, mode: Mode, evidence: Evidence, turn: int,
                  prev_mode: Mode | None = None, last_line: str = "") -> str:
    """A short directive for this one turn. Changes every turn, which also stops the model repeating itself."""
    parts: list[str] = []
    missing = [f for f in EVIDENCE_FIELDS if not getattr(evidence, f)]

    if mode is Mode.STALL:
        if prev_mode is not Mode.STALL:
            parts.append(f"This is your first line as {p.name}: say you've just come to the phone.")
        parts.append("This turn: " + STALL_TACTICS[turn % len(STALL_TACTICS)])
        if missing:
            parts.append("Then: " + _STALL_ASKS[missing[0]])
    elif mode is Mode.SCREEN:
        ask = next((_SCREEN_ASKS[f] for f in missing if f in _SCREEN_ASKS), "Ask what the call is about.")
        parts.append("This turn: " + ask)
    elif mode is Mode.GOODBYE:
        parts.append(f"Your excuse: {GOODBYE_EXCUSES[turn % len(GOODBYE_EXCUSES)]}.")

    if last_line and mode is not Mode.GREET:
        # Deliberately NOT quoting the previous line: quoting it seemed to prime the model to copy it.
        parts.append("Say something new: do not repeat or rephrase anything you have already said.")
    return " ".join(parts)


def build_instructions(p: Persona, mode: Mode, guidance: str = "") -> str:
    text = base_prompt(p) + " " + mode_instructions(p, mode)
    return text + (" " + guidance if guidance else "")
