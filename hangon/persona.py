"""Prompts for the conversational agent: the protected person's assistant, for the whole call.

Two layers:
* mode_instructions(): the role for this moment (screening, stalling, fixed information lines).
* turn_guidance(): a short, fresh directive for THIS turn, built from what the judge has learned so far.

The questions are not a fixed list. Each turn the agent is told what the caller has already said (name, reason for
calling, organization, number) so it never asks for it again, and what is still unknown, and it chooses the one that
fits best after what the caller just said. It reacts to something specific first, like a calm receptionist would.

Boson's own tutorial found that long behavioural prompts made the model ignore tools, so each layer is kept
short. The engine picks the mode; the agent only speaks.
"""
from __future__ import annotations

from .models import Evidence, Mode, Persona

GREETING = "Hello? This is {name}'s assistant, calls may be transcribed. Who's calling?"

# What a careful receptionist wants to know, in the order they would naturally ask. The first four are asked while
# screening; the last two only once the caller is being stalled (asking about payment too early would be odd).
_ASK_FOR = {
    "name": "who they are (their name)",
    "purpose": "what the call is about",
    "organization": "which organization they are calling from, if any",
    "number": "a number to call them back on",
    "payment": "exactly how the money or payment is supposed to be handled",
    "urgency": "what happens if it is not done today",
}
_TOLD = {"name": "name", "purpose": "reason for calling", "organization": "organization", "number": "callback number",
         "payment": "payment method", "urgency": "deadline or pressure"}
_SCREENING_FACTS = ("name", "purpose", "organization", "number")


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
        "a scam, and never state facts about any organization. Never repeat a sentence you already said. "
        "Never start a reply with \'I understand\' or \'I see\'."
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
        return (f"You are {n}'s assistant, taking down the details so {n} can follow up. Respond directly to what "
                "they just said, then ask exactly one natural follow-up question. Stay warm and cooperative, never "
                "suspicious or accusing. Never give real information, never agree to pay, and never end the call "
                "yourself.")
    raise ValueError(mode)


def known_facts(result, evidence: Evidence) -> dict[str, str]:
    """What the caller has told us so far, from the judge's latest result and the running evidence."""
    facts = {"name": getattr(result, "caller_name", None), "purpose": getattr(result, "purpose", None),
             "organization": evidence.claimed_org, "number": evidence.callback_number,
             "payment": evidence.payment_method, "urgency": evidence.urgency_tactic}
    return {k: v for k, v in facts.items() if v}


def turn_guidance(mode: Mode, known: dict[str, str]) -> str:
    """A short directive for this one turn: what is already known, what is not, and how to sound."""
    if mode not in (Mode.SCREEN, Mode.STALL):
        return ""
    parts: list[str] = []
    told = "; ".join(f"{_TOLD[k]}: {v}" for k, v in known.items())
    if told:
        parts.append(f"What the caller has already told you: {told}. Never ask for any of this again.")
    wanted = _SCREENING_FACTS if mode is Mode.SCREEN else tuple(_ASK_FOR)
    unknown = [_ASK_FOR[k] for k in wanted if k not in known]
    if unknown:
        parts.append("Still unknown: " + "; ".join(unknown) + ". Ask about ONE of these, whichever fits most "
                     "naturally after what they just said.")
    else:
        parts.append("You already know the basics, so ask one natural question about the request itself: how exactly "
                     "it would work, who would be involved, by when, or what happens next. Pick whichever fits what "
                     "they just said.")
    parts.append("Sound like a calm, friendly receptionist: first react in a few words to something specific they "
                 "just said, then ask your one question. Never start with 'I understand' or 'I see', and vary how "
                 "you begin.")
    # Deliberately NOT quoting the previous line: quoting it seemed to prime the model to copy it.
    parts.append("You are mid-conversation: do not greet or introduce yourself again, and do not repeat or "
                 "rephrase anything you have already said.")
    return " ".join(parts)


def build_instructions(p: Persona, mode: Mode, guidance: str = "") -> str:
    text = base_prompt(p) + " " + mode_instructions(p, mode)
    return text + (" " + guidance if guidance else "")
