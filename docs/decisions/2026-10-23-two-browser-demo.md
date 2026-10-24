# Decision: real audio handoff between two browsers

Date: 2026-10-23
Status: accepted for roadmap

## Context

The user wants the demo to work between their browser and their teammate's browser on separate devices. A simulated transfer card is insufficient.

## Decision

Use one shared backend with role-bound demo rooms. Both browsers connect audio to a backend media bridge. The gateway initially rejects, rings the receiver, or routes the caller to Higgs. After screening approval and receiver acceptance, detach Higgs and bridge the two people's audio. Distinguish screening completion from human-call completion.

## Consequences

Add receiver presence, ringing, Accept/Decline, shared HTTPS access, TURN connectivity, and two-device acceptance checks. Keep existing tests as a passing baseline and Higgs functionality as a planning assumption. Only phone-network entry, test phone identities, and SMS notifications are simulated. This supersedes simulated receiver delivery in the initial gateway decision.

## Validation

Documentation change only. Reviewed roadmap references for conflicting simulated-transfer instructions. Actual browser calling and deployment remain implementation tasks.
