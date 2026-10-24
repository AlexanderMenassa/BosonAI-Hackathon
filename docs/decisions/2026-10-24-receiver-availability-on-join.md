# Decision: receiver is available after joining a room

Date: 2026-10-24
Status: implemented

Joining as the receiver marks that browser ready for incoming calls. An approved unknown caller can ring the receiver without a separate readiness step. The receiver can still choose Go offline, which prevents later calls from ringing until they choose Ready for calls. Rejoining preserves that choice. The gateway continues to require receiver acceptance and working media before reporting a connected call.

The browser asks for microphone permission when an incoming call rings. A permission or media failure cannot create a connected call.

An offline WebSocket test covers the approved unknown-caller handoff after joining. Live two-device audio still needs a separate check.
