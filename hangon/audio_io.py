"""Microphone in, speaker out, at the 24 kHz 16-bit mono PCM that Higgs Realtime uses.

The voice call talks to an AudioIO object, so tests can swap in a fake one with no sound card.

Windows note: there is no echo cancellation here (browsers have it built in; plain Python does not).
Without headphones the microphone hears the agent through the speakers and the server thinks the caller
is interrupting. Use headphones, or run with --no-barge-in to mute the mic while the agent speaks.
"""
from __future__ import annotations

import threading
from typing import Callable, Protocol

RATE = 24000          # Higgs Realtime PCM rate, both directions
BYTES_PER_SAMPLE = 2  # 16-bit


class AudioIO(Protocol):
    def start(self, on_mic_chunk: Callable[[bytes], None]) -> None: ...
    def play(self, pcm24k: bytes) -> None: ...
    def clear(self) -> None: ...                 # drop everything not yet played (barge-in)
    def played_samples(self) -> int: ...         # 24 kHz samples played since the last reset_counter()
    def reset_counter(self) -> None: ...
    def is_playing(self) -> bool: ...
    def stop(self) -> None: ...


def resample(pcm: bytes, src_rate: int, dst_rate: int) -> bytes:
    """Linear-interpolation resampler with a crude moving-average low-pass when downsampling.
    Good enough for speech; avoids a scipy dependency."""
    if src_rate == dst_rate or not pcm:
        return pcm
    import numpy as np
    x = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
    if dst_rate < src_rate:
        k = max(1, round(src_rate / dst_rate))
        if k > 1:
            x = np.convolve(x, np.ones(k, dtype=np.float32) / k, mode="same")
    n_out = max(1, int(len(x) * dst_rate / src_rate))
    t_out = np.linspace(0, len(x) - 1, n_out)
    y = np.interp(t_out, np.arange(len(x)), x)
    return np.clip(y, -32768, 32767).astype(np.int16).tobytes()


class PlaybackBuffer:
    """Thread-safe byte queue drained by the speaker callback. Counts what actually reached the speaker."""

    def __init__(self, ratio_to_24k: float = 1.0):
        self._buf = bytearray()
        self._lock = threading.Lock()
        self._played_device_samples = 0
        self._ratio = ratio_to_24k  # device samples -> 24 kHz samples

    def push(self, pcm: bytes) -> None:
        with self._lock:
            self._buf.extend(pcm)

    def pull(self, nbytes: int) -> bytes:
        with self._lock:
            take = bytes(self._buf[:nbytes])
            del self._buf[:nbytes]
            self._played_device_samples += len(take) // BYTES_PER_SAMPLE
        return take + b"\x00" * (nbytes - len(take))

    def clear(self) -> None:
        with self._lock:
            self._buf.clear()

    def pending(self) -> int:
        with self._lock:
            return len(self._buf)

    def played_24k(self) -> int:
        with self._lock:
            return int(self._played_device_samples * self._ratio)

    def reset_counter(self) -> None:
        with self._lock:
            self._played_device_samples = 0


class SoundDeviceIO:
    """Real microphone and speakers via the `sounddevice` library (PortAudio; works on Windows)."""

    def __init__(self, input_device: int | None = None, output_device: int | None = None,
                 mute_mic_while_playing: bool = False):
        import sounddevice as sd
        self.sd = sd
        self.input_device, self.output_device = input_device, output_device
        self.mute_mic_while_playing = mute_mic_while_playing
        self.in_rate = self._pick_rate(input_device, "input")
        self.out_rate = self._pick_rate(output_device, "output")
        self.buffer = PlaybackBuffer(ratio_to_24k=RATE / self.out_rate)
        self._in = self._out = None

    def _pick_rate(self, device, kind: str) -> int:
        """Use 24 kHz if the device accepts it, otherwise its own default rate (we resample)."""
        try:
            check = self.sd.check_input_settings if kind == "input" else self.sd.check_output_settings
            check(device=device, samplerate=RATE, channels=1, dtype="int16")
            return RATE
        except Exception:
            return int(self.sd.query_devices(device, kind)["default_samplerate"])

    def start(self, on_mic_chunk: Callable[[bytes], None]) -> None:
        def mic_cb(indata, _frames, _time_info, _status):  # signature required by sounddevice
            if self.mute_mic_while_playing and self.is_playing():
                return
            on_mic_chunk(resample(bytes(indata), self.in_rate, RATE))

        def spk_cb(outdata, _frames, _time_info, _status):
            outdata[:] = self.buffer.pull(len(outdata))

        self._in = self.sd.RawInputStream(samplerate=self.in_rate, channels=1, dtype="int16",
                                          blocksize=int(self.in_rate * 0.1), device=self.input_device,
                                          callback=mic_cb)
        self._out = self.sd.RawOutputStream(samplerate=self.out_rate, channels=1, dtype="int16",
                                            device=self.output_device, callback=spk_cb)
        self._out.start()
        self._in.start()

    def play(self, pcm24k: bytes) -> None:
        self.buffer.push(resample(pcm24k, RATE, self.out_rate))

    def clear(self) -> None:
        self.buffer.clear()

    def played_samples(self) -> int:
        return self.buffer.played_24k()

    def reset_counter(self) -> None:
        self.buffer.reset_counter()

    def is_playing(self) -> bool:
        return self.buffer.pending() > 0

    def stop(self) -> None:
        for s in (self._in, self._out):
            if s is not None:
                try:
                    s.stop()
                    s.close()
                except Exception:
                    pass


def list_devices() -> str:
    import sounddevice as sd
    return str(sd.query_devices())
