"""Offline gateway checks with synthetic callers and recording transports."""
import sqlite3

import pytest

from hangon.gateway import Gateway, GatewayError
from hangon.models import (ClassifierResult, ConnectionState as State, Evidence,
                           FailureReason, Label, Outcome, Route)
from hangon.store import Store


class Classifier:
    def __init__(self, label=Label.LEGIT, high_stakes=False):
        self.label = label
        self.high_stakes = high_stakes
        self.calls = 0

    def classify(self, transcript):
        self.calls += 1
        return ClassifierResult(self.label, 0.05 if self.label is Label.LEGIT else 0.5,
                                "synthetic", Evidence(), high_stakes=self.high_stakes)


class Transport:
    def __init__(self, ready=True):
        self.ops = []
        self.ready = ready

    def start(self, call_id):
        self.ops.append(("start", call_id))

    def pause(self, call_id):
        self.ops.append(("pause", call_id))

    def detach(self, call_id):
        self.ops.append(("detach", call_id))

    def connect_ready(self, call_id):
        self.ops.append(("connect_ready", call_id))
        return self.ready

    def close(self, call_id):
        self.ops.append(("close", call_id))


@pytest.fixture
def setup(tmp_path):
    store = Store(str(tmp_path / "calls.db"))
    screen, receiver = Transport(), Transport()
    gateway = Gateway(store, Classifier(), screen, receiver,
                      wall=lambda: 1000.0, clock=lambda: 0.0)
    return store, screen, receiver, gateway


@pytest.mark.parametrize("trusted,blocked,route,state,screen_starts,receiver_starts", [
    (False, False, Route.SCREEN, State.SCREENING, 1, 0),
    (True, False, Route.PASS, State.RINGING, 0, 1),
    (False, True, Route.REJECT, State.ENDED, 0, 0),
    (True, True, Route.REJECT, State.ENDED, 0, 0),
])
def test_admission(setup, trusted, blocked, route, state, screen_starts, receiver_starts):
    store, screen, receiver, gateway = setup
    if trusted:
        store.add_trusted("+14165550111")
    if blocked:
        store.block("+14165550111")
    gateway.arrive("request-1", "+14165550111")
    assert gateway.route is route and gateway.state is state
    assert sum(op == "start" for op, _ in screen.ops) == screen_starts
    assert sum(op == "start" for op, _ in receiver.ops) == receiver_starts
    assert [e.type for e in gateway.events[:2]] == ["call.arrived", "call.route"]
    assert all(e.call_id == gateway.incoming.call_id for e in gateway.events)
    assert store.call_by_request("request-1")["public_call_id"] == gateway.incoming.call_id


def test_screened_handoff_needs_accept_and_media_readiness(setup):
    store, screen, receiver, gateway = setup
    arrival = gateway.arrive("request-1", "+14165550111")
    assert gateway.arrive("request-1", "+14165550111") == arrival
    gateway.caller_turn("Riverside Pharmacy, prescription ready")
    assert gateway.state is State.RINGING
    screening_row = store.call_by_request("request-1")
    assert screening_row["outcome"] is None
    assert screening_row["screening_result"] == "approved"
    assert screening_row["screening_ended_at"] == 1000.0
    call_id = gateway.incoming.call_id
    accepted = gateway.command("receiver", "call.accept", call_id, "accept-1", gateway.revision)
    assert gateway.state is State.CONNECTING
    assert gateway.command("receiver", "call.accept", call_id, "accept-1", 0) == accepted
    with pytest.raises(GatewayError):
        gateway.command("caller", "call.accept", call_id, "accept-1", 0)
    assert store.call_by_request("request-1")["outcome"] is None
    assert screen.ops.index(("detach", call_id)) > screen.ops.index(("pause", call_id))
    gateway.media_ready(call_id)
    assert gateway.state is State.CONNECTED
    gateway.command("caller", "call.end", call_id, "end-1", gateway.revision)
    row = store.call_by_request("request-1")
    assert row["outcome"] == Outcome.FORWARDED.value and row["public_call_id"] == call_id
    assert row["screening_result"] == "approved"
    assert "Riverside Pharmacy" in row["transcript"]
    assert len(store.calls()) == 1


def test_wrong_role_stale_event_and_late_accept(setup):
    store, _, receiver, gateway = setup
    store.add_trusted("+14165550111")
    gateway.arrive("request-1", "+14165550111")
    call_id = gateway.incoming.call_id
    with pytest.raises(GatewayError):
        gateway.command("caller", "call.accept", call_id, "bad", gateway.revision)
    with pytest.raises(GatewayError):
        gateway.command("receiver", "call.accept", call_id, "old", 0)
    gateway.command("caller", "call.end", call_id, "end", gateway.revision)
    with pytest.raises(GatewayError):
        gateway.command("receiver", "call.accept", call_id, "late", gateway.revision)
    assert gateway.state is State.ENDED
    assert sum(op == "start" for op, _ in receiver.ops) == 1
    with pytest.raises(GatewayError):
        gateway.media_ready(call_id)


def test_media_failure_never_records_connected(setup):
    store, _, receiver, gateway = setup
    store.add_trusted("+14165550111")
    receiver.ready = False
    gateway.arrive("request-1", "+14165550111")
    call_id = gateway.incoming.call_id
    gateway.command("receiver", "call.accept", call_id, "accept", gateway.revision)
    gateway.media_ready(call_id)
    assert gateway.state is State.MESSAGE_PENDING
    assert not any(e.type == "call.connected" for e in gateway.events)
    assert store.call_by_request("request-1")["outcome"] is None
    gateway.record_message(call_id, "message-1", gateway.revision, "Please call me back")
    assert store.call_by_request("request-1")["outcome"] == Outcome.MESSAGE_TAKEN.value
    assert store.call_by_request("request-1")["reason"] == "media_unavailable"


def test_screening_failure_offers_message_without_ringing(setup):
    store, _, receiver, gateway = setup
    gateway.arrive("request-1", "+14165550111")
    gateway.fail(gateway.incoming.call_id, FailureReason.SCREENING_UNAVAILABLE)
    assert gateway.state is State.MESSAGE_PENDING
    assert not any(op == "start" for op, _ in receiver.ops)
    assert [e.type for e in gateway.events[-2:]] == ["call.failed", "call.message_requested"]
    assert store.call_by_request("request-1")["outcome"] is None
    gateway.command("caller", "call.end", gateway.incoming.call_id, "end", gateway.revision)
    assert store.call_by_request("request-1")["outcome"] == Outcome.HANGUP_UNSURE.value
    assert store.call_by_request("request-1")["reason"] == "screening_unavailable"


def test_offline_receiver_and_ring_timeout_do_not_claim_message_taken(tmp_path):
    store = Store(str(tmp_path / "calls.db"))
    store.add_trusted("+14165550111")
    now = [0.0]
    screen, receiver = Transport(), Transport()
    available = [False]
    gateway = Gateway(store, Classifier(), screen, receiver, clock=lambda: now[0],
                      wall=lambda: 1000.0, receiver_available=lambda: available[0],
                      ring_timeout=5)
    gateway.arrive("offline", "+14165550111")
    assert gateway.state is State.MESSAGE_PENDING
    assert store.call_by_request("offline")["outcome"] is None
    gateway.command("caller", "call.end", gateway.incoming.call_id, "end", gateway.revision)
    assert store.call_by_request("offline")["outcome"] == Outcome.HANGUP_UNSURE.value
    available[0] = True
    gateway.arrive("timeout", "+14165550111")
    assert gateway.state is State.RINGING
    now[0] = 6.0
    gateway.tick()
    assert gateway.state is State.MESSAGE_PENDING
    assert gateway.pending_reason == "ring_timeout"
    with pytest.raises(GatewayError):
        gateway.command("receiver", "call.accept", gateway.incoming.call_id, "late", gateway.revision)


def test_high_stakes_and_receiver_end_authority(tmp_path):
    store = Store(str(tmp_path / "calls.db"))
    screen, receiver = Transport(), Transport()
    gateway = Gateway(store, Classifier(high_stakes=True), screen, receiver)
    gateway.arrive("high-stakes", "+14165550111")
    with pytest.raises(GatewayError):
        gateway.command("receiver", "call.end", gateway.incoming.call_id, "bad", gateway.revision)
    gateway.caller_turn("This is your bank about an urgent account issue")
    assert gateway.state is State.ENDED
    assert not any(op == "start" for op, _ in receiver.ops)
    assert store.call_by_request("high-stakes")["outcome"] == Outcome.HELD_CALLBACK.value


def test_close_error_does_not_prevent_cleanup_or_next_call(tmp_path):
    class BrokenClose(Transport):
        def close(self, call_id):
            super().close(call_id)
            raise RuntimeError("synthetic close failure")

    store = Store(str(tmp_path / "calls.db"))
    screen, receiver = BrokenClose(), Transport()
    gateway = Gateway(store, Classifier(), screen, receiver)
    gateway.arrive("first", "+14165550111")
    gateway.fail(gateway.incoming.call_id, FailureReason.SCREENING_UNAVAILABLE)
    assert sum(op == "close" for op, _ in receiver.ops) == 1
    gateway.command("caller", "call.end", gateway.incoming.call_id, "end", gateway.revision)
    assert sum(op == "close" for op, _ in screen.ops) == 1
    gateway.arrive("second", "+14165550112")
    assert gateway.state is State.SCREENING
    assert store.call_by_request("first")["outcome"] == Outcome.HANGUP_UNSURE.value


def test_additive_migration_keeps_old_calls(tmp_path):
    path = tmp_path / "old.db"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE calls (id INTEGER PRIMARY KEY AUTOINCREMENT, number TEXT, "
                   "started_at REAL, ended_at REAL, outcome TEXT, verdict TEXT, "
                   "evidence TEXT, transcript TEXT, reason TEXT)")
        db.execute("INSERT INTO calls(number) VALUES ('+14165550000')")
    store = Store(str(path))
    assert store.calls()[0]["number"] == "+14165550000"
    assert store.calls()[0]["public_call_id"] is None
    store.begin_call("public-1", "request-1", "+14165550111", 100.0)
    first = store.complete_call("public-1", 110.0, "rejected", "unsure", {}, [], "")
    assert store.complete_call("public-1", 120.0, "forwarded", "legit", {}, [], "") == first
    assert Store(str(path)).call_by_request("request-1")["outcome"] == "rejected"
