# Decision: pace playback by its sample clock and bound queued audio

Date: 2026-10-24
Status: implemented; live listening check pending

The first browser audio bridge slept 20 ms after preparing each 20 ms frame. Encoding and event loop work added to that delay, so playback could drift behind incoming audio. Its queue also discarded all speech when 50 provider chunks accumulated. Either behavior could create audible gaps.

The outbound track now schedules frames against one monotonic sample clock. Buffered speech is limited by audio duration: up to 10 seconds while Higgs speaks, then 300 ms for the human route, where lower delay matters more than replaying old speech. When full, it drops the oldest queued audio. Incoming resampled frames contribute only their actual samples, excluding any plane padding.

Synthetic audio tests cover pacing continuity, overflow behavior, and two-way routing. The listener should compare Higgs speech and the accepted human call on two devices; these tests do not establish live sound quality.
