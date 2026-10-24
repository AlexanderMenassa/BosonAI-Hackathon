import numpy as np

from hangon.audio_io import PlaybackBuffer, resample


def tone(rate, seconds=0.1, hz=440):
    t = np.arange(int(rate * seconds)) / rate
    return (np.sin(2 * np.pi * hz * t) * 10000).astype(np.int16).tobytes()


def test_resample_changes_length_by_the_rate_ratio():
    assert len(resample(tone(48000), 48000, 24000)) // 2 == 2400
    assert len(resample(tone(24000), 24000, 44100)) // 2 == 4410


def test_resample_same_rate_is_untouched():
    pcm = tone(24000)
    assert resample(pcm, 24000, 24000) is pcm


def test_resampled_speech_keeps_its_energy():
    out = np.frombuffer(resample(tone(48000), 48000, 24000), dtype=np.int16)
    assert 6000 < np.abs(out).max() <= 10000


def test_playback_buffer_counts_only_what_was_played():
    b = PlaybackBuffer(ratio_to_24k=0.5)          # e.g. a 48 kHz speaker
    b.push(b"\x01\x00" * 1000)
    assert b.pull(800) == b"\x01\x00" * 400       # 400 device samples -> 200 at 24 kHz
    assert b.played_24k() == 200 and b.pending() == 1200
    b.clear()
    assert b.pending() == 0 and b.pull(4) == b"\x00" * 4   # silence when empty
    b.reset_counter()
    assert b.played_24k() == 0
