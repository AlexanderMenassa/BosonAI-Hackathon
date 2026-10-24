"""Browser call control. The engine decides; this module executes permitted routes."""
from __future__ import annotations

import time
from typing import Callable, Protocol
from uuid import uuid4

from .classifier import Classifier
from .config import Settings
from .engine import CallSession
from .models import (Action, ConnectionState as State, Decision, EndReason, FailureReason,
                     GatewayEvent, IncomingCall, Outcome, Route)
from .store import Store, normalize_number


class Transport(Protocol):
    def start(self, call_id: str) -> None: ...
    def pause(self, call_id: str) -> None: ...
    def detach(self, call_id: str) -> None: ...
    def connect_ready(self, call_id: str) -> bool: ...
    def close(self, call_id: str) -> None: ...


class GatewayError(ValueError):
    pass


class Gateway:
    """One demo room, one active call. Commands are role-bound and idempotent."""

    def __init__(self, store: Store, classifier: Classifier, screening: Transport,
                 receiver: Transport, wall: Callable[[], float] = time.time,
                 clock: Callable[[], float] = time.monotonic,
                 receiver_available: Callable[[], bool] = lambda: True,
                 ring_timeout: float = 30.0, connect_timeout: float = 30.0,
                 settings: Settings | None = None):
        self.store, self.classifier = store, classifier
        self.screening, self.receiver = screening, receiver
        self.wall = wall
        self.clock = clock
        self.receiver_available = receiver_available
        self.ring_timeout = ring_timeout
        self.connect_timeout = connect_timeout
        self.settings = settings
        self.ring_started: float | None = None
        self.connect_started: float | None = None
        self.pending_reason = ""
        self.incoming: IncomingCall | None = None
        self.session: CallSession | None = None
        self.route: Route | None = None
        self.state = State.ENDED
        self.revision = 0
        self.events: list[GatewayEvent] = []
        self._acks: dict[str, tuple[str, str, str, GatewayEvent]] = {}
        self._closed: set[int] = set()
        self.last_decision: Decision | None = None

    def _emit(self, kind: str, detail: str = "") -> GatewayEvent:
        self.revision += 1
        event = GatewayEvent(kind, self.incoming.call_id, str(uuid4()), self.revision,
                             self.state, detail)  # type: ignore[union-attr]
        self.events.append(event)
        return event

    def arrive(self, request_id: str, number: str) -> GatewayEvent:
        if not request_id or not number:
            raise GatewayError("request_id and number are required")
        old = self.store.call_by_request(request_id)
        if old:
            if old["number"] != normalize_number(number):
                raise GatewayError("request_id already used for another caller")
            if self.incoming and old["public_call_id"] == self.incoming.call_id:
                return self.events[0]
            raise GatewayError("arrival already recorded; call is unavailable in this room")
        if self.incoming and self.state is not State.ENDED:
            raise GatewayError("room is busy")
        self.incoming = IncomingCall(str(uuid4()), request_id, number)
        self.session = CallSession(number, self.classifier, self.store, self.settings,
                                   browser_call_id=self.incoming.call_id, wall=self.wall,
                                   clock=self.clock)
        self.store.begin_call(self.incoming.call_id, request_id, number, self.session.started_at)
        self.route = self.session.admission_route()
        self.state, self.revision, self.events, self._acks = State.ARRIVED, 0, [], {}
        self._closed = set()
        self.last_decision = None
        self.ring_started, self.connect_started, self.pending_reason = None, None, ""
        arrival = self._emit("call.arrived")
        self._emit("call.route", self.route.value)
        try:
            if self.route is Route.REJECT:
                self._end(Outcome.REJECTED, EndReason.REJECTED)
            elif self.route is Route.PASS:
                self._ring()
            else:
                self.screening.start(self.incoming.call_id)
                self.state = State.SCREENING
                self._emit("call.screening")
        except Exception:
            self._fail(FailureReason.SCREENING_UNAVAILABLE if self.route is Route.SCREEN
                       else FailureReason.RECEIVER_UNAVAILABLE)
        return arrival

    def caller_turn(self, text: str) -> GatewayEvent:
        if self.state is not State.SCREENING:
            raise GatewayError("call is not screening")
        try:
            decision = self.session.on_caller_turn(text)  # type: ignore[union-attr]
        except Exception:
            self.last_decision = None
            return self._fail(FailureReason.SCREENING_UNAVAILABLE)
        return self.apply_screening_decision(decision)

    def apply_screening_decision(self, decision: Decision) -> GatewayEvent:
        """Apply an engine decision, including one produced by background classification."""
        if self.state is not State.SCREENING:
            raise GatewayError("call is not screening")
        self.last_decision = decision
        if decision.action is Action.FORWARD:
            try:
                self.screening.pause(self.incoming.call_id)  # type: ignore[union-attr]
                self.store.complete_screening(self.incoming.call_id, self.wall(), "approved")  # type: ignore[union-attr]
                self._emit("call.screening_approved")
                return self._ring()
            except Exception:
                return self._fail(FailureReason.RECEIVER_UNAVAILABLE)
        if decision.action is Action.END:
            return self._end(decision.outcome or Outcome.MESSAGE_TAKEN, EndReason.COMPLETED)
        return self._emit("call.screening_continues")

    def _ring(self) -> GatewayEvent:
        if not self.receiver_available():
            return self._message_pending("receiver_unavailable")
        self.receiver.start(self.incoming.call_id)  # type: ignore[union-attr]
        self.state = State.RINGING
        self.ring_started = self.clock()
        return self._emit("call.ringing", self.route.value)  # type: ignore[union-attr]

    def _message_pending(self, reason: str) -> GatewayEvent:
        self.pending_reason = reason
        self.ring_started = None
        self.state = State.MESSAGE_PENDING
        self._close_transports()
        return self._emit("call.message_requested", reason)

    def _close_transports(self) -> None:
        call_id = self.incoming.call_id  # type: ignore[union-attr]
        for transport in (self.screening, self.receiver):
            key = id(transport)
            if key in self._closed:
                continue
            self._closed.add(key)
            try:
                transport.close(call_id)
            except Exception:
                pass

    def tick(self) -> GatewayEvent | None:
        if (self.state is State.RINGING and self.ring_started is not None and
                self.clock() - self.ring_started >= self.ring_timeout):
            return self._message_pending(FailureReason.RING_TIMEOUT.value)
        if (self.state is State.CONNECTING and self.connect_started is not None and
                self.clock() - self.connect_started >= self.connect_timeout):
            return self._fail(FailureReason.MEDIA_UNAVAILABLE)
        return None

    def command(self, role: str, kind: str, call_id: str, event_id: str,
                expected_revision: int) -> GatewayEvent:
        if not event_id:
            raise GatewayError("event_id is required")
        if event_id in self._acks:
            saved_role, saved_kind, saved_call, result = self._acks[event_id]
            if (role, kind, call_id) != (saved_role, saved_kind, saved_call):
                raise GatewayError("event_id already used for another command")
            return result
        if not self.incoming or call_id != self.incoming.call_id:
            raise GatewayError("wrong call_id")
        if expected_revision != self.revision:
            raise GatewayError("stale revision")
        if kind == "call.accept" and role == "receiver" and self.state is State.RINGING:
            try:
                if self.route is Route.SCREEN:
                    self.screening.detach(call_id)
            except Exception:
                result = self._fail(FailureReason.MEDIA_UNAVAILABLE)
                self._acks[event_id] = (role, kind, call_id, result)
                return result
            self.state = State.CONNECTING
            self.ring_started = None
            self.connect_started = self.clock()
            result = self._emit("call.connecting")
        elif kind == "call.decline" and role == "receiver" and self.state is State.RINGING:
            result = self._message_pending("receiver_declined")
        elif kind == "call.message" and role == "caller" and self.state is State.MESSAGE_PENDING:
            raise GatewayError("use record_message with nonempty caller text")
        elif (kind == "call.end" and self.state is not State.ENDED and
              (role == "caller" or (role == "receiver" and self.state in
               (State.RINGING, State.CONNECTING, State.CONNECTED)))):
            outcome = (Outcome.FORWARDED_TRUSTED if self.route is Route.PASS else Outcome.FORWARDED)
            if self.state is not State.CONNECTED:
                outcome = Outcome.HANGUP_UNSURE
            result = self._end(outcome, EndReason.CALLER_HANGUP if role == "caller"
                               else EndReason.RECEIVER_HANGUP)
        else:
            raise GatewayError("unauthorized or invalid command for current state")
        self._acks[event_id] = (role, kind, call_id, result)
        return result

    def record_message(self, call_id: str, event_id: str, expected_revision: int,
                       text: str) -> GatewayEvent:
        if event_id in self._acks:
            role, kind, saved_call, result = self._acks[event_id]
            if (role, kind, saved_call) != ("caller", "call.message", call_id):
                raise GatewayError("event_id already used for another command")
            return result
        if (not self.incoming or call_id != self.incoming.call_id or
                expected_revision != self.revision or self.state is not State.MESSAGE_PENDING):
            raise GatewayError("message is stale or call is not awaiting one")
        if not text.strip():
            raise GatewayError("message cannot be empty")
        self.session.transcript.append(("caller", text.strip()))  # type: ignore[union-attr]
        result = self._end(Outcome.MESSAGE_TAKEN, EndReason.COMPLETED, self.pending_reason)
        self._acks[event_id] = ("caller", "call.message", call_id, result)
        return result

    def media_ready(self, call_id: str) -> GatewayEvent:
        if not self.incoming or call_id != self.incoming.call_id or self.state is not State.CONNECTING:
            raise GatewayError("call is not connecting")
        try:
            if not self.receiver.connect_ready(call_id):
                return self._fail(FailureReason.MEDIA_UNAVAILABLE)
        except Exception:
            return self._fail(FailureReason.MEDIA_UNAVAILABLE)
        self.state = State.CONNECTED
        self.connect_started = None
        return self._emit("call.connected")

    def fail(self, call_id: str, reason: FailureReason) -> GatewayEvent:
        if not self.incoming or call_id != self.incoming.call_id or self.state is State.ENDED:
            raise GatewayError("call is not active")
        return self._fail(reason)

    def _fail(self, reason: FailureReason) -> GatewayEvent:
        self._emit("call.failed", reason.value)
        return self._message_pending(reason.value)

    def _end(self, outcome: Outcome, reason: EndReason, detail: str = "") -> GatewayEvent:
        if self.state is State.ENDED:
            return self.events[-1]
        self.state = State.ENDED
        self._close_transports()
        self.session.finish(outcome, detail or self.pending_reason)  # type: ignore[union-attr]
        return self._emit("call.ended", detail or reason.value)
