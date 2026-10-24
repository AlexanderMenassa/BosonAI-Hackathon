# Decision: use the turn pipeline in browser screening

Date: 2026-10-24
Status: accepted

Browser screening now uses the same `TurnPipeline` as terminal voice. A caller turn is recorded immediately. The classifier has the configured grace period to finish; otherwise Higgs receives a provisional screening reply while classification continues. Late forward or end decisions pass through the gateway, which remains responsible for routing and persistence. A late closing line waits for the current reply to finish before the browser pauses or closes screening.

The pipeline belongs to one call. Ending a call or starting another prevents an old verdict from changing the new call. `HANGON_CLASSIFIER_GRACE` applies to browser calls through the shared settings. Offline tests cover a slow scam verdict arriving after a provisional reply. Live Higgs timing and two-device audio still require a separate browser check.
