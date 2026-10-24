# Decision: receiver screening messages

Date: 2026-10-24
Status: implemented

The receiver's browser has a room-local screening inbox. When an unknown screened call ends as unsure, the receiver sees the caller's test number and the classifier's conversation summary, or captured caller words if no summary exists. When screening ends with a scam verdict, the receiver sees the number and the assessment reason, with up to two grounded caller quotes when available. The block list shows only phone numbers; the existing engine still decides whether to block, and only a final scam outcome adds a number.

Messages are kept in the room for the last 20 screened calls and are included only in receiver snapshots, including after reconnecting to the same room. They are browser messages, not SMS or carrier actions. Room recreation does not restore the inbox; call outcomes remain in SQLite.

The current call's screening message also appears on the receiver's main call screen when the call ends, so a scam result does not require opening or scrolling to the inbox.

Offline tests cover an unsure call, a scam call, receiver-only visibility, duplicate broadcast, and block-list state. Live browser and provider behavior remain separate acceptance checks.
