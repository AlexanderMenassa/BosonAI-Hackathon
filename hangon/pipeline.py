"""Keeps the conversation flowing while the classifier thinks.

Before: caller finishes a sentence -> wait for the classifier (a network call to Gemini, or a local LLM) ->
agent answers. Every turn had a dead pause the length of the classifier call.

Now: each caller turn is recorded at once (CallSession.begin_turn) and the classifier runs on a worker thread.

* If the verdict is ready within `grace` seconds, it is used exactly as before (so a fast classifier, and the
  offline heuristic, change nothing).
* If not, submit() returns a PROVISIONAL decision (keep screening) and the agent answers right away. When the
  classifier finishes, the result is applied to the call state; if it calls for a forward or a hang-up, that
  decision is queued on `late` for the transport to carry out after the agent's current line.

Safety rules, because the agent is now talking before the verdict exists:

* The agent never forwards or ends the call by itself; only an applied verdict can, so the provisional reply can
  say nothing a caller could use. (A scam verdict is the only thing acted on while newer turns are unclassified;
  see CallSession.apply_result.)
* Results are applied in turn order, one at a time. If several turns pile up behind a slow classifier, only the
  newest is classified: a classifier sees the whole transcript, so the older ones add nothing.
* The classifier only ever sees a copy of the transcript, and the session is only touched from the event loop,
  so there is no locking.
* Once a forward / hang-up decision has been produced, later results are dropped.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Callable

from .engine import CallSession
from .models import Action, Decision

log = logging.getLogger("hangon.pipeline")

TERMINAL = (Action.END, Action.FORWARD)


class TurnPipeline:
    def __init__(self, session: CallSession, grace: float | None = None,
                 on_applied: Callable[[Decision, float], None] | None = None):
        self.session = session
        self.grace = session.cfg.classifier_grace if grace is None else grace
        self.on_applied = on_applied                  # called with (decision, seconds since the turn began)
        self.late: asyncio.Queue[Decision] = asyncio.Queue()   # forward / end decisions that missed the grace window
        self._tail: asyncio.Task | None = None       # the most recently submitted classification
        self._latest = 0                              # newest turn number submitted
        self._closed = False                          # a forward / end decision has been produced

    # ------------------------------------------------------------------ public
    async def submit(self, text: str) -> Decision:
        """Record a caller turn. Returns the decision to act on NOW: the real one if the classifier answers
        within the grace window, otherwise a provisional keep-screening decision (the real one arrives on `late`
        if it is a forward or hang-up)."""
        provisional = self.session.begin_turn(text)
        turn = self._latest = self.session.turns
        task = asyncio.create_task(self._classify(turn, self._tail, time.monotonic()))
        self._tail = task

        # The last allowed turn has nothing left to be provisional about: wait for the verdict.
        wait = None if self.session.capped else self.grace
        try:
            decision = await asyncio.wait_for(asyncio.shield(task), timeout=wait)
        except asyncio.TimeoutError:
            task.add_done_callback(self._deliver_late)
            return provisional
        except Exception:                             # classifier blew up: keep the call going on the provisional
            log.exception("classification failed")
            return provisional
        return decision or provisional

    async def settle(self) -> Decision | None:
        """Wait for the classification in flight and return the forward / end decision it produced, if any.
        For transports that take turns (the text simulator); voice reads `late` instead."""
        if self._tail is not None:
            try:
                await self._tail
            except Exception:
                pass
        try:
            return self.late.get_nowait()
        except asyncio.QueueEmpty:
            return None

    # ------------------------------------------------------------------ internals
    async def _classify(self, turn: int, previous: asyncio.Task | None, began: float) -> Decision | None:
        if previous is not None:                      # apply in turn order
            try:
                await previous
            except Exception:
                pass
        if self._closed or self.session.ended or turn < self._latest:
            return None                               # decided already, call over, or a newer turn covers this one
        transcript = list(self.session.transcript)    # a copy: the thread must not see the live list
        result = await asyncio.to_thread(self.session.classifier.classify, transcript)
        if self._closed or self.session.ended:        # hung up / decided while the classifier was thinking
            return None
        decision = self.session.apply_result(result, as_of_turn=turn)
        if decision.action in TERMINAL:
            self._closed = True
        if self.on_applied:
            self.on_applied(decision, time.monotonic() - began)
        return decision

    def _deliver_late(self, task: asyncio.Task) -> None:
        if task.cancelled():
            return
        if task.exception() is not None:
            log.error("classification failed", exc_info=task.exception())
            return
        decision = task.result()
        if decision is not None and decision.action in TERMINAL:
            self.late.put_nowait(decision)
