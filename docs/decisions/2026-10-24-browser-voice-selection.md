# Decision: use the configured voice for browser screening

Date: 2026-10-24
Status: implemented; live voice check pending

The Walter persona and reference clip added in commit `34cf326` do not themselves select a Higgs voice. A reference clip must first be registered with Boson, yielding a voice ID. The terminal voice path already uses `HIGGS_VOICE`, while browser screening hardcoded `default`.

Pass the existing `HIGGS_VOICE` setting through the browser room and media bridge into the Higgs `session.update` output voice. Keep `default` when no voice is configured. This changes only screening speech; routing remains with the gateway. The browser never receives the voice ID or credentials.

Local fake-server tests check the configured session payload. A real browser call with a registered voice ID is still needed to confirm the heard voice.
