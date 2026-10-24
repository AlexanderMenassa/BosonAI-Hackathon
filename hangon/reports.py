"""Text summaries, the evidence card, and who gets told what."""
from __future__ import annotations

import datetime as dt
from pathlib import Path

from .config import Settings
from .engine import CallSession
from .models import Outcome
from .notify import Notifier

_SCAM = (Outcome.SCAM_ENDED, Outcome.SCAM_HANGUP)


def summary_text(s: CallSession) -> str:
    who = (s.caller_summary or "A caller").rstrip(" .")
    o = s.outcome
    if o is Outcome.FORWARDED:
        return f"{who}. Call forwarded to you."
    if o is Outcome.HELD_CALLBACK:
        return (f"{who}. This involves money or an official body, so it was NOT forwarded. "
                "If it matters, call them back on a number you look up yourself, not one the caller gave.")
    if o is Outcome.MESSAGE_TAKEN:
        return f"{who}. The caller never made clear who they were or why they called. Message taken. Number not blocked."
    if o is Outcome.HANGUP_UNSURE:
        return f"{who}. The caller hung up before explaining. Number not blocked."
    if o in _SCAM:
        ev = s.evidence
        bits = [f"claimed to be {ev.claimed_org}" if ev.claimed_org else None,
                f"asked for {ev.payment_method}" if ev.payment_method else None]
        detail = ", ".join(b for b in bits if b)
        return f"Likely scam stopped and number blocked ({detail or 'see report'}). You did not need to do anything."
    return who


def evidence_card(s: CallSession) -> str:
    r = s.last_result
    started = dt.datetime.fromtimestamp(s.started_at).strftime("%Y-%m-%d %H:%M:%S")
    ev = s.evidence
    flags = [f for f, on in (("high-stakes claim", s.high_stakes), ("tried to steer the screening", s.injection_attempt)) if on]
    lines = [
        f"EVIDENCE CARD (call #{s.call_id})",
        f"Date:                 {started}  ({s.duration:.0f}s)",
        f"Caller number:        {s.number}",
        f"Verdict:              {s.verdict.value.upper()}" + (f" (score {r.scam_score:.2f})" if r else ""),
        f"Outcome:              {s.outcome.value if s.outcome else '-'}",
        f"Reason:               {r.reason if r else '-'}",
        "",
        f"Claimed organization: {ev.claimed_org or '-'}",
        f"Payment requested:    {ev.payment_method or '-'}",
        f"Callback number:      {ev.callback_number or '-'}",
        f"Urgency tactic:       {ev.urgency_tactic or '-'}",
        f"Flags:                {', '.join(flags) or '-'}",
        f"Classifier:           {r.source if r else '-'}",
        "",
        "Reporting: scams can be reported to the Canadian Anti-Fraud Centre. This card has the details they ask for.",
        "",
        "TRANSCRIPT",
    ]
    for who, text in s.transcript:
        lines.append(f"  {who:>6}: {text}")
    return "\n".join(lines) + "\n"


def write_report(s: CallSession, settings: Settings) -> Path:
    d = Path(settings.reports_dir)
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"call_{s.call_id}.txt"
    path.write_text(evidence_card(s), encoding="utf-8")
    return path


def notify_after_call(s: CallSession, settings: Settings, notifier: Notifier) -> Path | None:
    """Tell the right people. Returns the report path if one was written.

    * Rejected / trusted calls: nobody is bothered.
    * Everything else: a text summary to the protected person.
    * Forwarded calls: summary only. All other screened calls also get the transcript + evidence card,
      sent to the protected person and the optional guardian.
    """
    if s.outcome in (Outcome.REJECTED, Outcome.FORWARDED_TRUSTED, None):
        return None
    notifier.send(settings.protected_phone, summary_text(s))
    if s.outcome is Outcome.FORWARDED:
        return None
    path = write_report(s, settings)
    notifier.send(settings.protected_phone, "Transcript and evidence card for the call above.", str(path))
    if settings.guardian_phone:
        notifier.send(settings.guardian_phone, f"Guardian copy: {summary_text(s)}", str(path))
    return path
