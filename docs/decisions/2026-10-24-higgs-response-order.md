# Decision: serialize Higgs screening replies

Date: 2026-10-24
Status: implemented; live retest pending

The browser screen asks Higgs to greet the caller, then requests another reply after each finalized caller turn. A caller can speak before the greeting's `response.done` event. Sending the next `response.create` while the greeting is still active can conflict with that response. Keep one pending reply mode and send it only after `response.done`. Also wait for `session.updated` before the first greeting so its audio and turn settings have been applied.

A fake Higgs server checks this event order. The user's live report included hearing only the beginning of the greeting; the screenshot shows a full generated caption, but does not reveal whether an interruption event or audio playback loss caused the cut-off. A live call with headphones must verify the greeting and a subsequent caller/reply turn.
