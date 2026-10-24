# Decision: server-terminated WebRTC with one audio route per call

Date: 2026-10-23
Status: accepted for implementation; live two-device validation pending
Related issues: #5-#9, #15-#17

## Context

Phase 2A proved room authorization and gateway state changes, but its transports never carried audio. Phase 2B requires the caller to talk to Higgs before any approved human handoff, and requires the accepted caller and receiver to hear each other without Higgs seeing that conversation.

## Decision

Each browser negotiates one WebRTC audio peer with the backend through its role-bound room WebSocket. The backend owns two outbound audio tracks and changes a single route from screening to human audio. During screening, only caller audio enters the server-side Higgs WebSocket, and only Higgs audio reaches the caller. Receiver audio is ignored until the gateway has approved ringing, the receiver accepts, and both media peers report connected. Handoff pauses Higgs, clears queued playback, sends a browser clear signal, waits briefly for old media to drain, closes Higgs, then routes the two people to each other. The gateway still owns routing and `connected` transitions.

The browser sends a complete SDP offer after ICE gathering; the backend returns a complete answer. This avoids a second candidate event contract. HTTPS/WSS is supported through server certificate options. A configured TURN REST shared secret produces hour-limited browser and backend relay credentials; the shared secret itself never goes to the browser.

The existing text and terminal voice adapters stay separate. Live browser calls use Higgs audio configuration from the existing terminal voice path, and finalized caller transcripts enter the gateway. Phase 3 remains responsible for quote-backed asynchronous classification and fuller conversation modes.

## Consequences

`aiortc` and its media dependencies are required for the browser server. A reachable HTTPS endpoint and TURN service must be supplied for cross-network use; the code cannot create those external services. The application keeps one call per room. The browser sends real microphone audio only after the user starts or readies a call. Human speech is not sent to Higgs or appended to screening transcripts.

## Evidence and limits

Synthetic aiortc peers exchanged nonzero audio through the backend, and a fake Higgs WebSocket exercised audio configuration, output, and finalized transcript events. These are local tests. No claim is made yet about a live Boson call, two physical devices, browser permissions, or a TURN-relayed venue-network connection.
