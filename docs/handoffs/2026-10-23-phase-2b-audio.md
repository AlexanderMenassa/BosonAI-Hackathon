# Handoff: Phase 2B browser audio and Higgs handoff

Date: 2026-10-23
Branch and revision: current working tree; Phase 2A files are uncommitted alongside pre-existing documentation/harness changes.

## Goal and boundaries

Replace the Phase 2A control adapters with real browser microphone capture, Higgs screening audio, and accepted two-way human audio. Keep the gateway's engine-owned route and state contract. Phase 2A proves control behavior only.

## Completed

`hangon/server.py` serves two role-bound browser control views under one invite-code room. `hangon/gateway.py` owns admission, ringing, acceptance, message/failure paths, timers, cleanup and final persistence. `hangon/store.py` reserves a public call ID at arrival. `web/` shows route, state, summary and synthetic controls. The control adapters never report media readiness, so the browser cannot show `connected` without Phase 2B's bridge. See `docs/decisions/2026-10-23-gateway-contract.md`.

Phase 2B code now adds `hangon/media.py` with aiortc browser peers, a server-side Higgs audio WebSocket, bounded audio queues, finalized transcript delivery, interruption queue clearing, and one screening/human route. `hangon/server.py` binds media offers to the room role and call ID, reports `connected` only after two connected media peers and gateway acceptance, and supports HTTPS certificates and short-lived TURN REST credentials. `hangon/store.py` records screening completion separately from final call completion. Browser controls request microphone access and release tracks on end. See `docs/decisions/2026-10-23-phase-2b-media-bridge.md`.

## Verification

Run `.venv/bin/python scripts/verify.py` after Phase 2B changes. Phase 2A focused tests use temporary SQLite and local fake transports; `tests/test_server.py` exercises two independent WebSocket sessions. Manual browser control check used two local browser tabs: trusted rang; blocked rejected; unknown screened until two synthetic pharmacy lines led to engine approval; receiver decline requested a message; receiver rejoined after reload. These checks did not exercise microphone audio, Higgs, WebRTC, HTTPS/WSS, TURN, or two physical devices.

After the new code, `.venv/bin/python scripts/verify.py` passed 89 tests and all isolated CLI scenarios. Local aiortc peers exchanged audio through the backend; a fake Higgs server exercised session setup, audio output, interruption clearing, and finalized transcript deduplication. An integrated WebSocket/WebRTC test confirmed a trusted call only becomes `connected` after receiver acceptance and both peers connect. Two local browser tabs joined one room, but microphone permission was not exercised. One explicit live Higgs check stopped before connecting because `BOSON_API_KEY` was not configured. No live provider, HTTPS, TURN, or two-physical-device success is claimed.

## Remaining work

Finish Phase 2B acceptance on a shared HTTPS URL with a configured Boson key and TURN service: test live Higgs greeting and caller transcription, approved handoff, trusted bypass, interruption, both-party hang-up, media errors, and relay-only connectivity on two physical devices. Record actual observations and fix any failures. The control-only `call.line` input is disabled in live media rooms. Restore or close interrupted calls after server restart in a separate lifecycle task if durable recovery is required. Phase 3 still owns structured asynchronous classification and richer speech modes.

## Coordination

One writer should own `hangon/gateway.py` and `hangon/store.py` changes while audio integration is underway. Preserve existing uncommitted work; do not copy live caller data or credentials into tests.
