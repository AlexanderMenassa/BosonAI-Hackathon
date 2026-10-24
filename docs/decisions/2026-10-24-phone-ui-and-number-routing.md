# Decision: editable demo numbers and receiver-owned call lists

Date: 2026-10-24
Status: implemented

The browser previously exposed route labels as caller-selectable profiles. The phone-style UI now asks the caller for a simulated North American number. The receiver owns the trust and block lists in expandable settings; only the receiver's room socket may change them. The backend normalizes 10-digit numbers to `+1` form and validates each list edit and call arrival. The existing engine still decides routing from the stored lists, with blocking before trust. List changes affect new calls, not a call already in progress.

The caller number is a test identity, not verified caller ID. No carrier call or SMS is placed. List entries use the existing SQLite store and persist only as long as the selected demo database. The browser presents the route and events in a details panel; it cannot override the gateway's decision.

Offline WebSocket tests cover list editing, caller authorization, formatted numbers, and subsequent pass, reject, and screen routes. A live two-device browser check remains separate from these tests.
