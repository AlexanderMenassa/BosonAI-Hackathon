"""Rules enforced in plain code. A caller (or a confused model) cannot talk past these."""
from __future__ import annotations

import re

# Anything touching money, accounts, or authority is "high-stakes": never auto-forwarded,
# even when the classifier thinks the caller is legit.
HIGH_STAKES_RE = re.compile(
    r"\b(bank|banking|credit card|debit card|account number|bank account|chequing|savings|"
    r"cra|c r a|r c a|canada revenue|revenue agency|irs|tax(es)?|service canada|social insurance|sin number|"
    r"police|rcmp|court|warrant|arrest|government|immigration|"
    r"gift cards?|wire transfer|e-?transfer|bitcoin|crypto(currency)?|western union|"
    r"payment|pay (us|me|now)|owe|debt|fine|refund|loan|mortgage|investment|lottery|prize)\b",
    re.IGNORECASE,
)

# Phrases a caller would use to try to steer the screening model. A real pharmacy never says these.
INJECTION_RE = re.compile(
    r"(ignore (all |any |your )?(previous|prior|above)? ?(instructions|rules)|"
    r"system prompt|you are (now )?(an? )?(ai|assistant|language model)|"
    r"classify (this|me|the call) as|mark (this|me) as (legit|safe)|"
    r"transfer me (now|immediately)|override|disregard (your|the) )",
    re.IGNORECASE,
)


def mentions_high_stakes(text: str) -> bool:
    return bool(HIGH_STAKES_RE.search(text))


def looks_like_injection(text: str) -> bool:
    return bool(INJECTION_RE.search(text))
