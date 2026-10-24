# Decision: connect browser screening to the classifier and directed voice

Date: 2026-10-24
Status: accepted

## Context

Browser calls sent caller audio to Higgs and classified finalized transcripts, but the
browser selected only the heuristic classifier and always requested a generic SCREEN
reply. The terminal voice path already sent the engine's mode and caller context.

## Decision

Browser startup selects heuristic, Ollama, or Gemini with `HANGON_CLASSIFIER`.
Each room gets its own classifier. A failed LLM assessment becomes `unsure` for
browser calls, preventing automatic transfer or blocking. The gateway retains the
engine's latest speaking decision, and the browser supplies that mode, known facts,
and caller history to each Higgs reply. The engine and gateway remain the only
route and end authorities. Higgs speaks as the protected person, and a scam
verdict ends screening immediately.

Known caller facts persist across turns, including when a later Gemini assessment
times out. Completed Higgs captions enter the screening transcript before another
caller turn is assessed. The default Gemini read timeout is 30 seconds and can
be adjusted with `GEMINI_TIMEOUT`.

The browser server writes a separate private file for each call under
`artifacts/call-logs/`. It contains caller turns, generated Higgs captions,
assessment source and verdict, and final outcome. It is not sent to browser
clients. Raw provider errors and credentials are excluded.

## Consequences

Provider-backed classification requires its own configuration and live validation.
The room waits a bounded time for final Higgs speech before closing or ringing.
Unfinished or interrupted speech may still be absent from the stored transcript.

## Evidence

`tests/test_browser_screening.py` checks classifier injection and directed reply
context. `scripts/verify.py` covers offline regression behavior; it does not
establish live provider or two-device audio success.
