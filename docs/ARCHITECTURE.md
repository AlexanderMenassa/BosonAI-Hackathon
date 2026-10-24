# Current architecture and change ownership

The terminal entry point is `python -m hangon.sim`. The browser entry point remains `python -m hangon.server`, which forwards to `hangon/browser/server.py`. The browser package owns role-bound rooms and the WebRTC audio bridge. During screening, caller audio reaches a server-side Higgs session. After accepted handoff, audio travels between the two browser peers. The engine still owns routing and end decisions. SQLite persists test contacts and calls; notifications are simulated.

| Concern | Canonical location | Checks to consider |
| --- | --- | --- |
| Call lifecycle and routing | `hangon/engine.py` | `tests/test_engine.py`, `tests/test_sim.py` |
| Call gateway and state changes | `hangon/gateway.py` | `tests/test_gateway.py` |
| Browser rooms, signaling, and static pages | `hangon/browser/server.py` | `tests/test_server.py` |
| WebRTC and Higgs audio | `hangon/browser/media.py` | `tests/test_media.py`; separate live check |
| High-stakes and injection checks | `hangon/rules.py` | `tests/test_engine.py` |
| Shared types | `hangon/models.py` | All consumers and full suite |
| Assessments and fallback | `hangon/classifier.py` | `tests/test_classifier.py` |
| Provider protocol and persona | `hangon/agent.py`, `hangon/persona.py` | `tests/test_agent.py`; separate live check |
| SQLite schema and writes | `hangon/store.py` | Engine/simulator persistence checks |
| Configuration | `hangon/config.py` | Defaults and isolated CLI scenario runs |
| Reports and simulated delivery | `hangon/reports.py`, `hangon/notify.py` | `tests/test_sim.py` |

Keep model adapters separate from decision authority. Changes to persistence must preserve existing data or provide an explicit migration/reset procedure; never silently delete a user's database. There is no migration framework yet.

`hangon/gateway.py` belongs in the core package because it coordinates call state across transports while relying on `engine.py` for policy. The browser-only server and media bridge live in `hangon/browser/`. Small `hangon/server.py` and `hangon/media.py` modules preserve the existing launch command and imports.

## Known gaps relative to the roadmap

The WebRTC bridge and Higgs audio adapter have offline coverage, including a synthetic audio round trip and fake Higgs server. Live Higgs, browser microphone/playback, two physical devices, and TURN relay remain unverified. Dashboard events, quote-backed assessment contracts, asynchronous stale-result handling, and an independent idle timer are planned work. The browser selects heuristic, Ollama, or Gemini classification for finalized caller transcripts. Quote-backed asynchronous assessment contracts and live provider evaluation remain planned work.

The harness verifies the current offline baseline. It does not certify production scam detection, caller identity, live model reliability, or the full planned product.

Unknown callers enter Higgs screening. The engine classifies each finalized caller turn, ends screening immediately on a scam verdict. The caller never reaches the receiver on a scam verdict.

## Planned call gateway

Every incoming call must enter a gateway before receiver delivery or Higgs connection. The gateway adapts the existing `CallSession.open()` decisions into pass, reject, or screen connection actions; it must not become a second policy engine. Blocked profiles take precedence over trusted profiles. Unknown profiles enter screening and remain disconnected from the receiver until backend approval.

The simulator and browser gateway check profiles before starting a model session. The browser arrival adapter, call identity, duplicate-event handling, and receiver control contract are implemented. Real telephone interception still requires a phone-provider entry point; browser audio does not intercept an existing mobile number.

## Two-browser demo target

The caller and receiver use separate devices connected to one shared application. A server-owned demo room binds their roles and call IDs. WebSocket controls handle arrival, ringing, acceptance, decline, and hang-up. Each browser uses WebRTC to a backend media bridge; the gateway chooses whether caller audio connects to Higgs or, after receiver acceptance, to the other browser.

Passing a call means permission to ring, not proof of connection. Stop Higgs input/output before enabling human audio. Keep the human call alive after screening ends; late classifications and late accepts cannot revive ended calls. Handle offline/busy receivers, ringing timeout, media failures, and hang-up from either side. Screening reports do not include the subsequent human conversation.

The browser server supports HTTPS/WSS certificate options, WebRTC audio, and TURN REST credentials. Their actual two-device connectivity and live handoff remain Phase 2B acceptance checks. Phone identities, telephone-network entry, and SMS notifications remain simulated.
