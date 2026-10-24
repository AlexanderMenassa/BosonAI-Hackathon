# Decision: browser gateway call identity and control contract

Date: 2026-10-23
Status: accepted
Related issue: #10

## Context

The terminal engine marks trusted calls complete at admission and creates a call row at finish. Browser calls need one identity at arrival and must remain active through ringing, acceptance, and media readiness.

## Decision

The gateway generates a UUID `call_id` and reserves a SQLite row before routing. The integer row `id` remains separate. An additive, nullable `public_call_id` column preserves existing CLI records; `request_id` makes retried arrivals identifiable. The engine's `admission_route()` owns blocked-before-trusted policy. The legacy `open()` method retains terminal behavior. Browser sessions persist by updating their reserved row.

The gateway owns the states `arrived`, `screening`, `ringing`, `connecting`, `connected`, `message_pending`, and `ended`. Engine approval starts ringing; receiver Accept starts connecting; `connect_ready` succeeds before connected is emitted. The screening transport is detached before human media is enabled. Receiver absence, decline, ring timeout, screening failure, and connection failure request a message. `message_taken` is saved only after caller text is received; otherwise caller departure remains an incomplete hang-up. A call ended in any state cannot be revived.

Each server event has `type`, `call_id`, `event_id`, `revision`, `state`, and optional `detail`. Current event names are `call.arrived`, `call.route`, `call.screening`, `call.screening_continues`, `call.screening_approved`, `call.ringing`, `call.connecting`, `call.connected`, `call.failed`, `call.message_requested`, and `call.ended`. `call.route` detail is `pass`, `reject`, or `screen`; `call.failed` and `call.message_requested` details name the reason. A browser command has `type`, `call_id`, `event_id`, and `expected_revision`. `call.accept` and `call.decline` are receiver-only; `call.end` is available to the caller and, after ringing, the receiver. The server alone creates approval, route, failure, and readiness events. Duplicate command IDs with the same role, type, and call return the original acknowledgment; stale revisions and mismatched IDs are rejected.

For example, a receiver sends `{"type":"call.accept","call_id":"<uuid>","event_id":"<client-id>","expected_revision":3}` and receives a `call.connecting` event. The browser adapter must bind the role and call ID to its authenticated room session; caller-supplied role strings are not authorization by themselves.

Injected screening and receiver transports implement `start`, `pause`, `detach`, `connect_ready`, and `close`. This contract is synchronous and uses control-only adapters for Phase 2A. Phase 2B supplies actual browser and media transports. A room serializes incoming commands and binds an opaque token to each caller/receiver WebSocket. Invite code and test profile do not grant command authority; the socket's assigned participant does. If a process restarts during an active call, arrival retry is rejected as unavailable rather than silently starting a second call; recovery or interrupted-call finalization needs a separate task.

## Alternatives and consequences

Reusing the SQLite integer row ID as the public event ID would expose storage identity and require changing CLI report references. Keeping the UUID separate avoids that change. The schema migration adds columns and indexes without deleting old rows. Gateway state is in memory for this phase, so durable recovery is not claimed.

## Evidence

`tests/test_gateway.py` covers admission, identity, handoff, command authorization, media failure, and migration with synthetic calls and temporary storage. `python scripts/verify.py` runs the offline suite and CLI scenarios. No browser audio or live provider behavior is established by these checks.
