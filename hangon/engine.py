"""The call state machine.

This is the decision authority. The conversational agent only speaks; it never decides to forward,
block, or hang up. Every routing decision below is plain code driven by the classifier's output, so
a caller cannot talk the agent into a transfer.

Lifecycle, as driven by whatever transport sits on top (simulator, phone line, ...):

    d = session.open()                      # blocklist / trusted check
    ... for each caller turn ...
    d = session.on_caller_turn(text)        # classify, decide
    if d.mode: say = agent.say(d.mode)      # agent speaks; session.record_agent(say)
    if d.action in (FORWARD, END): session.finish()
    ... or if the caller hangs up: session.on_hangup()

on_caller_turn() is synchronous: the agent waits for the classifier. Transports that must keep the conversation
flowing use the two halves it is made of instead (see pipeline.TurnPipeline, which does this for them):

    d0 = session.begin_turn(text)           # instant, no classifier: record the turn, regex-only flags
    ... the agent keeps talking (d0 is a provisional CONTINUE/SCREEN) while the classifier runs elsewhere ...
    d = session.apply_result(result, as_of_turn=n)   # verdict + routing decision, when the classifier is done
"""
from __future__ import annotations

import time
from dataclasses import replace
from typing import Callable

from .classifier import SCORE_NOTHING_YET, SCORE_ORDINARY, Classifier, _HARD_CUES
from .config import Settings
from .models import Action, ClassifierResult, Decision, Evidence, Label, Mode, Outcome, Route
from .rules import looks_like_injection, mentions_high_stakes
from .store import Store


class CallSession:
    def __init__(self, number: str, classifier: Classifier, store: Store, settings: Settings | None = None,
                 clock: Callable[[], float] = time.monotonic, wall: Callable[[], float] = time.time,
                 browser_call_id: str | None = None):
        self.number = number
        self.classifier = classifier
        self.store = store
        self.cfg = settings or Settings()
        self._clock = clock
        self._wall = wall
        self.started = clock()

        self.transcript: list[tuple[str, str]] = []
        self.verdict: Label = Label.UNSURE
        self.evidence = Evidence()
        self.turns = 0
        self.high_stakes = False
        self.injection_attempt = False
        self.last_result: ClassifierResult | None = None
        self.caller_summary = ""
        self.outcome: Outcome | None = None
        self.ended = False
        self.call_id: int | None = None
        self.browser_call_id = browser_call_id
        self._low_streak = 0
        self._clean_turns = 0
        self.duration: float = 0.0
        self.started_at: float = wall()

    # ------------------------------------------------------------------ properties
    @property
    def elapsed(self) -> float:
        return self._clock() - self.started

    @property
    def capped(self) -> bool:
        return self.turns >= self.cfg.max_turns or self.elapsed >= self.cfg.max_seconds

    def status_line(self) -> str:
        r = self.last_result
        score = f"{r.scam_score:.2f}" if r else "-"
        flags = []
        if self.high_stakes:
            flags.append("HIGH-STAKES")
        if self.injection_attempt:
            flags.append("INJECTION-ATTEMPT")
        return (f"verdict={self.verdict.value.upper()} score={score} evidence={self.evidence.count}/4 "
                f"turn={self.turns}/{self.cfg.max_turns}" + (f" [{' '.join(flags)}]" if flags else ""))

    # ------------------------------------------------------------------ lifecycle
    def admission_route(self) -> Route:
        """Policy-only entry point for browser calls; no connection or finishing effects."""
        if self.store.is_blocked(self.number):
            return Route.REJECT
        if self.store.is_trusted(self.number):
            return Route.PASS
        return Route.SCREEN

    def open(self) -> Decision:
        route = self.admission_route()
        if route is Route.REJECT:
            self.outcome = Outcome.REJECTED
            self.finish()
            return Decision(Action.REJECT, outcome=Outcome.REJECTED, note="number is on the blocklist")
        if route is Route.PASS:
            self.outcome = Outcome.FORWARDED_TRUSTED
            self.finish()
            return Decision(Action.FORWARD_TRUSTED, outcome=Outcome.FORWARDED_TRUSTED,
                            note="trusted contact, forwarded without screening")
        return Decision(Action.ANSWER, mode=Mode.GREET, note="answering")

    def record_agent(self, text: str) -> None:
        self.transcript.append(("agent", text))

    def _apply_benign_decay(self, r: ClassifierResult) -> ClassifierResult:
        """Lower the score while a call stays clean or lacks hard scam indicators."""
        n = self.cfg.benign_turns
        if r.source.startswith("heuristic"):
            return r

        # Allow decay if there are NO hard behaviours, no high-stakes, and no injection attempt.
        # This way, soft tags like a relative introduction can fade if the conversation stays ordinary.
        has_hard = any(t in _HARD_CUES for t, _ in getattr(r, "behaviours", []))
        clean = (not has_hard and not r.high_stakes and not self.high_stakes and not self.injection_attempt)

        self._clean_turns = self._clean_turns + 1 if clean else 0
        if n <= 0 or not clean:
            return r
        if self._clean_turns >= n:
            return replace(r, label=Label.LEGIT, scam_score=SCORE_ORDINARY,
                           reason=f"no scam behaviour in {self._clean_turns} turns of normal conversation")

        step = (r.scam_score - SCORE_ORDINARY) / max(1, (n - self._clean_turns + 1))
        return replace(r, scam_score=round(max(SCORE_ORDINARY, r.scam_score - step), 2),
                       reason=f"no scam behaviour so far ({self._clean_turns}/{n} normal turns)")

    def begin_turn(self, text: str) -> Decision:
        """First half of a caller turn: everything that needs no classifier. Instant.

        Records the turn and runs the regex-only checks (injection attempt, high-stakes words). Returns a
        PROVISIONAL decision (keep screening) that is safe to act on while the real verdict is still being worked
        out: it never forwards, ends, or blocks anything."""
        if self.ended:
            raise RuntimeError("call already finished")
        self.transcript.append(("caller", text))
        self.turns += 1
        if looks_like_injection(text):
            self.injection_attempt = True
        self.high_stakes = self.high_stakes or mentions_high_stakes(text)
        return Decision(Action.CONTINUE, Mode.SCREEN, note="classifying in the background")

    def apply_result(self, result: ClassifierResult, as_of_turn: int | None = None) -> Decision:
        """Second half of a caller turn: fold a classifier result into the call state and decide.

        `as_of_turn` is the turn count the result was computed for. When newer turns have been heard since (the
        result is STALE), anything that would let the caller through or close the call on a non-scam verdict is
        deferred: the newer turn may say something the stale result never saw, and the next result decides. A
        SCAM verdict is always acted on, since evidence of a scam does not go away."""
        if self.ended:
            raise RuntimeError("call already finished")
        stale = as_of_turn is not None and as_of_turn < self.turns
        self.last_result = result
        self.evidence.merge(result.evidence)
        if result.caller_summary:
            self.caller_summary = result.caller_summary
        self.high_stakes = self.high_stakes or result.high_stakes
        result = self._apply_benign_decay(result)
        self.last_result = result
        self._update_verdict(result)
        return self._decide(stale)

    def on_caller_turn(self, text: str) -> Decision:
        """Both halves in one blocking call: classify, then decide."""
        self.begin_turn(text)
        return self.apply_result(self.classifier.classify(list(self.transcript)), as_of_turn=self.turns)

    def _decide(self, stale: bool = False) -> Decision:
        if self.verdict is Label.SCAM:
            # End the call immediately upon a confirmed scam verdict,
            # cutting off evasive or uncooperative callers right away.
            return self._end(Mode.GOODBYE, Outcome.SCAM_ENDED, "scam detected: ending call immediately")

        if stale:
            return Decision(Action.CONTINUE, Mode.SCREEN, note="verdict deferred: a newer turn is still being classified")

        if self.verdict is Label.LEGIT:
            if self.high_stakes:
                return self._end(Mode.HOLD, Outcome.HELD_CALLBACK,
                                 "legit but high-stakes: not forwarded")
            if self.browser_call_id is None:
                self.outcome = Outcome.FORWARDED
            return Decision(Action.FORWARD, Mode.HANDOFF, outcome=self.outcome,
                            note="legit: forwarding")

        # UNSURE
        if self.capped:
            return self._end(Mode.TAKE_MESSAGE, Outcome.MESSAGE_TAKEN, "cap reached while unsure")
        return Decision(Action.CONTINUE, Mode.SCREEN, note="unsure: asking more")

    def on_hangup(self) -> Decision:
        """The caller hung up before the system ended the call."""
        if not self.ended and self.outcome is None:
            self.outcome = Outcome.SCAM_HANGUP if self.verdict is Label.SCAM else Outcome.HANGUP_UNSURE
        self.finish()
        return Decision(Action.END, outcome=self.outcome, note="caller hung up")

    def finish(self, outcome: Outcome | None = None, reason_override: str = "") -> Outcome:
        """Persist the call. Idempotent. Blocklists the number only on a scam verdict."""
        if self.ended:
            return self.outcome  # type: ignore[return-value]
        if outcome is not None:
            self.outcome = outcome
        self.ended = True
        self.duration = self.elapsed
        self.started_at = self._wall() - self.duration
        if self.outcome is None:
            self.outcome = Outcome.HANGUP_UNSURE
        reason = reason_override or (self.last_result.reason if self.last_result else "")
        if self.outcome in (Outcome.SCAM_ENDED, Outcome.SCAM_HANGUP):
            self.store.block(self.number, reason=f"scam: {reason}")
        if self.browser_call_id is not None:
            self.call_id = self.store.complete_call(
                self.browser_call_id, self.started_at + self.duration, self.outcome.value,
                self.verdict.value, self.evidence.to_dict(), self.transcript, reason)
        else:
            self.call_id = self.store.save_call(
                self.number, self.started_at, self.started_at + self.duration, self.outcome.value,
                self.verdict.value, self.evidence.to_dict(), self.transcript, reason)
        return self.outcome

    # ------------------------------------------------------------------ internals
    def _end(self, mode: Mode, outcome: Outcome, note: str) -> Decision:
        self.outcome = outcome
        return Decision(Action.END, mode, outcome, note=note)

    def _update_verdict(self, r: ClassifierResult) -> None:
        """Turn a raw classifier result into a stable verdict.

        Hysteresis: it takes a strong signal to enter SCAM, and a sustained low score to leave it,
        so one lucky-sounding sentence from a scammer does not flip the call to safe.
        """
        s = r.scam_score
        if self.verdict is Label.SCAM:
            if s < self.cfg.scam_exit and r.label is not Label.SCAM:
                self._low_streak += 1
            else:
                self._low_streak = 0
            if self._low_streak >= self.cfg.scam_exit_turns:
                self.verdict = Label.UNSURE
                self._low_streak = 0
        else:
            if r.label is Label.SCAM and s >= self.cfg.scam_enter:
                self.verdict = Label.SCAM
            elif r.label is Label.LEGIT and s <= self.cfg.legit_enter:
                self.verdict = Label.LEGIT
            else:
                self.verdict = Label.UNSURE
        # A caller trying to steer the screening is never auto-forwarded.
        if self.injection_attempt and self.verdict is Label.LEGIT:
            self.verdict = Label.UNSURE
