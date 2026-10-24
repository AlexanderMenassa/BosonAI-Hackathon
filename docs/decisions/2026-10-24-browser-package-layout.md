# Decision: keep the gateway in core and group browser adapters

Date: 2026-10-24
Status: accepted

The gateway coordinates call state and side effects while the engine remains the source of routing and ending decisions. It is a core application boundary, so `hangon/gateway.py` stays beside the engine and shared types. Browser rooms, signaling, static serving, WebRTC, and Higgs audio are transport-specific and now live under `hangon/browser/`.

The existing `python -m hangon.server` command and `hangon.server` / `hangon.media` imports continue through small compatibility modules. This groups browser implementation without changing call behavior or forcing callers to migrate at once. The original module paths can be removed in a separate breaking-change decision if all consumers move.

Verification is the offline suite and isolated CLI scenarios. Live Higgs and two-device audio require separate acceptance checks.
