# Decision: make call interception an explicit gateway

Date: 2026-10-23
Status: accepted for roadmap
Related work: user clarification that every call must be intercepted before receiver delivery

## Context

The earlier roadmap included profile checks but its diagram connected browser audio directly to Higgs, obscuring the component responsible for deciding whether screening was necessary.

## Decision

Add an explicit gateway before both receiver and Higgs. It applies engine policy to choose pass, reject, or screen, then owns connection changes for the rest of the call. Preserve the separate conversation agent and classification model. Add Phase 2A before live browser audio (now Phase 2B).

## Consequences

Trusted and blocked test callers do not consume a Higgs session. Unknown callers cannot reach the receiver until screening policy approves them. Browser call arrival represents a phone call; receiver delivery is superseded by the real two-browser handoff in `2026-10-23-two-browser-demo.md`. Actual phone interception requires a future phone-provider adapter. Existing Phase 1 issues remain closed; this is additional roadmap scope.

## Validation

Reviewed the existing simulator and engine call-entry flow. Updated roadmap, architecture, and acceptance scenarios; no application behavior changed or live phone integration tested.
