"""Prompts for the conversational agent.

Two layers:
* mode_instructions(): the role for this moment (assistant screening, Margaret stalling, ...).
* turn_guidance(): a short, fresh directive for THIS turn: what evidence is still missing and which
  stalling tactic to use.

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
_SCREEN_ASKS = {  # used only while that evidence is missing
    "claimed_org": "Ask which organization they are calling from.",
    "callback_number": "Ask for a number where they can be reached.",
}
_SCREEN_ALWAYS = ("Ask what the call is about.", "Ask for their name.")

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


def exact_line(p: Persona, mode: Mode) -> str | None:
    """Lines that carry information the caller must hear are fixed, not improvised (Higgs still speaks them)."""
    n = p.name
    return {
        Mode.GREET: greeting(p),
        Mode.HANDOFF: f"Thank you. One moment, I'll put you through to {n}.",
        Mode.HOLD: (f"Thank you. {n} can't come to the phone right now, but I'll let them know you called, "
                    "and they'll call you back on your organization's official number."),
        Mode.TAKE_MESSAGE: f"I'm sorry, I couldn't quite catch what this is about. I'll let {n} know you called. Goodbye.",
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
        return (f"You are now {n}, age {p.age}, {p.backstory}. You have taken the phone from your assistant. "
                f"Be warm, a bit confused, and slow to understand. {_CHATTINESS.get(p.chattiness, '')} "
                "Keep them talking. Use each excuse (your hearing, finding a pen, your glasses, the cat) at most once "
                "in the whole call. Never give real information, never agree to pay, and never end the call yourself.")
    if mode is Mode.GOODBYE:
        return (f"You are {n}. Make a warm, natural excuse to end the call, and end with the word goodbye. "
                "Stay friendly and fully in character. Two short sentences.")
    raise ValueError(mode)


def turn_guidance(p: Persona, mode: Mode, evidence: Evidence, turn: int, prev_mode: Mode | None = None) -> str:
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
        asks = [_SCREEN_ASKS[f] for f in missing if f in _SCREEN_ASKS] + list(_SCREEN_ALWAYS)
        parts.append("This turn: " + asks[turn % len(asks)])  # rotate so an evasive caller gets varied questions
    elif mode is Mode.GOODBYE:
        parts.append(f"Your excuse: {GOODBYE_EXCUSES[turn % len(GOODBYE_EXCUSES)]}.")

    if mode in (Mode.SCREEN, Mode.STALL, Mode.GOODBYE):
        if not (mode is Mode.STALL and prev_mode is not Mode.STALL):
            parts.append("You are mid-conversation: do not open with a greeting like 'hello there' "
                         "and do not introduce yourself again.")
        # Deliberately NOT quoting the previous line: quoting it seemed to prime the model to copy it.
        parts.append("Say something new: do not repeat or rephrase anything you have already said.")
    return " ".join(parts)


def build_instructions(p: Persona, mode: Mode, guidance: str = "") -> str:
    text = base_prompt(p) + " " + mode_instructions(p, mode)
    return text + (" " + guidance if guidance else "")
