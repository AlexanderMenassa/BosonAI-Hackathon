"""Per-room WebRTC audio bridge and server-side Higgs screening session."""
from __future__ import annotations

import asyncio
import base64
from contextlib import suppress
import json
import time
from fractions import Fraction
from typing import Callable

import av
from aiortc import (MediaStreamTrack, RTCConfiguration, RTCIceServer,
                    RTCPeerConnection, RTCSessionDescription)

from .audio_io import RATE
from .models import Mode, Persona
from .persona import base_prompt, build_instructions


class OutboundAudio(MediaStreamTrack):
    kind = "audio"

    def __init__(self):
        super().__init__()
        self.queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=200)
        self.buffer = bytearray()
        self.samples = 0
        self.delivered = 0
        self.enabled = False
        self.started_at: float | None = None
        self.queued_bytes = 0
        self.max_buffer_ms = 10000

    def push(self, pcm: bytes) -> None:
        if not self.enabled or not pcm:
            return
        limit = self.max_buffer_ms * RATE * 2 // 1000
        if len(pcm) > limit:
            pcm = pcm[-limit:]
        while self.queued_bytes + len(self.buffer) + len(pcm) > limit and not self.queue.empty():
            self.queued_bytes -= len(self.queue.get_nowait())
        if self.queued_bytes + len(self.buffer) + len(pcm) > limit:
            del self.buffer[:self.queued_bytes + len(self.buffer) + len(pcm) - limit]
        if self.queue.full():
            self.queued_bytes -= len(self.queue.get_nowait())
        self.queue.put_nowait(pcm)
        self.queued_bytes += len(pcm)

    def clear(self) -> None:
        self.buffer.clear()
        while not self.queue.empty():
            self.queue.get_nowait()
        self.queued_bytes = 0

    async def recv(self):
        # A fixed 20 ms cadence keeps the WebRTC sender alive while the route is silent.
        if self.started_at is None:
            self.started_at = time.monotonic()
        delay = self.started_at + self.samples / RATE - time.monotonic()
        if delay > 0:
            await asyncio.sleep(delay)
        while len(self.buffer) < 960:
            try:
                chunk = self.queue.get_nowait()
                self.queued_bytes -= len(chunk)
                self.buffer.extend(chunk)
            except asyncio.QueueEmpty:
                break
        pcm = bytes(self.buffer[:960])
        del self.buffer[:960]
        self.delivered += len(pcm) // 2
        pcm += bytes(960 - len(pcm))
        frame = av.AudioFrame(format="s16", layout="mono", samples=480)
        frame.sample_rate = RATE
        frame.pts = self.samples
        frame.time_base = Fraction(1, RATE)
        frame.planes[0].update(pcm)
        self.samples += 480
        return frame


class HiggsScreen:
    def __init__(self, on_turn: Callable[[str], object], on_caption: Callable[[str], object],
                 on_error: Callable[[str], object], key: str, url: str, output: OutboundAudio,
                 connect=None, on_interrupt=None):
        self.on_turn, self.on_caption, self.on_error = on_turn, on_caption, on_error
        self.key, self.url, self.output, self.connect = key, url, output, connect
        self.on_interrupt = on_interrupt
        self.ws = None
        self.task: asyncio.Task | None = None
        self.input: asyncio.Queue[bytes] = asyncio.Queue(maxsize=50)
        self.active = False
        self.paused = False
        self.responding = False
        self.awaiting_response = False
        self.item: str | None = None
        self.generation = 0
        self.suppress_output = False
        self.cancelled_response: str | None = None
        self.current_response: str | None = None
        self.seen_items: set[str] = set()
        self.persona = Persona()

    async def start(self) -> None:
        if not self.key:
            raise RuntimeError("BOSON_API_KEY is not configured")
        from websockets.asyncio.client import connect
        opener = self.connect or connect
        self.ws = await asyncio.wait_for(opener(self.url, additional_headers={
            "Authorization": f"Bearer {self.key}"}, max_size=None), 15)
        config = {"type": "session.update", "session": {
            "model": "higgs-realtime", "instructions": base_prompt(self.persona),
            "output_modalities": ["audio"], "audio": {
                "input": {"format": {"type": "audio/pcm", "rate": RATE},
                          "turn_detection": {"type": "server_vad", "create_response": False,
                                             "interrupt_response": True},
                          "transcription": {"model": "higgs-stt-3.1"}},
                "output": {"format": {"type": "audio/pcm", "rate": RATE}, "voice": "default"}}}}
        await self.ws.send(json.dumps(config))
        while True:
            event = json.loads(await asyncio.wait_for(self.ws.recv(), 15))
            if event.get("type") in ("session.created", "session.updated"):
                break
            if event.get("type") == "error":
                raise RuntimeError("Higgs rejected audio session configuration")
        self.active = True
        self.output.enabled = True
        self.task = asyncio.create_task(self._run())
        await self.speak(Mode.GREET)

    async def speak(self, mode: Mode) -> None:
        if self.active and not self.paused and self.ws:
            self.responding = True
            self.awaiting_response = True
            await self.ws.send(json.dumps({"type": "response.create", "response": {
                "instructions": build_instructions(self.persona, mode)}}))

    def feed(self, pcm: bytes) -> None:
        if self.active and not self.paused:
            try:
                self.input.put_nowait(pcm)
            except asyncio.QueueFull:
                asyncio.create_task(self.on_error("screening_audio_overflow"))

    async def _run(self) -> None:
        sender = asyncio.create_task(self._send_audio())
        try:
            async for raw in self.ws:
                event = json.loads(raw)
                kind = event.get("type")
                if not self.active or self.paused:
                    continue
                if kind == "input_audio_buffer.speech_started":
                    self.generation += 1
                    self.suppress_output = True
                    self.cancelled_response = self.current_response
                    self.awaiting_response = False
                    was_playing = self.responding or bool(self.output.buffer) or not self.output.queue.empty()
                    self.output.clear()
                    if self.on_interrupt:
                        await self.on_interrupt()
                    if self.item and was_playing:
                        await self.ws.send(json.dumps({"type": "conversation.item.truncate",
                            "item_id": self.item, "content_index": 0,
                            "audio_end_ms": int(self.output.delivered * 1000 / RATE)}))
                    self.item = None
                elif kind == "conversation.item.input_audio_transcription.completed":
                    item = event.get("item_id")
                    line = (event.get("transcript") or "").strip()
                    if line and item not in self.seen_items:
                        if item:
                            self.seen_items.add(item)
                        await self.on_turn(line)
                elif kind == "response.output_item.added":
                    self.item = (event.get("item") or {}).get("id")
                elif kind == "response.created":
                    self.current_response = (event.get("response") or {}).get("id")
                    if self.awaiting_response:
                        self.suppress_output = False
                        self.awaiting_response = False
                        self.cancelled_response = None
                        self.output.delivered = 0
                elif kind == "response.output_audio.delta":
                    if not self.suppress_output and event.get("response_id") != self.cancelled_response:
                        self.output.push(base64.b64decode(event.get("delta", "")))
                elif kind == "response.output_audio_transcript.done":
                    line = (event.get("transcript") or "").strip()
                    if line and not self.suppress_output:
                        await self.on_caption(line)
                elif kind == "response.done":
                    self.responding = False
                elif kind in ("error", "session.idle_timeout", "session.max_duration_reached"):
                    raise RuntimeError("Higgs audio session ended")
        except asyncio.CancelledError:
            raise
        except Exception:
            if self.active:
                asyncio.create_task(self.on_error("screening_unavailable"))
        finally:
            sender.cancel()
            with suppress(asyncio.CancelledError):
                await sender

    async def _send_audio(self) -> None:
        try:
            while self.active:
                pcm = await self.input.get()
                if self.active and not self.paused:
                    await self.ws.send(json.dumps({"type": "input_audio_buffer.append",
                                                  "audio": base64.b64encode(pcm).decode()}))
        except asyncio.CancelledError:
            raise
        except Exception:
            if self.active:
                asyncio.create_task(self.on_error("screening_unavailable"))

    def pause(self) -> None:
        self.paused = True
        self.output.clear()
        self.output.enabled = False

    async def close(self) -> None:
        self.active = False
        self.pause()
        if self.task and self.task is not asyncio.current_task():
            self.task.cancel()
        if self.ws:
            await self.ws.close()
        if self.task and self.task is not asyncio.current_task():
            with suppress(asyncio.CancelledError):
                await self.task


class MediaBridge:
    def __init__(self, turn, caption, failure, key: str, url: str, ice_servers: list[dict] | None = None,
                 interrupt=None):
        self.turn, self.caption, self.failure = turn, caption, failure
        self.key, self.url = key, url
        self.interrupt = interrupt
        self.configuration = RTCConfiguration(iceServers=[RTCIceServer(**item) for item in (ice_servers or [])])
        self.peers: dict[str, RTCPeerConnection] = {}
        self.outputs: dict[str, OutboundAudio] = {}
        self.pumps: dict[str, asyncio.Task] = {}
        self.screen: HiggsScreen | None = None
        self.call_id: str | None = None
        self.route = "silent"
        self.screen_paused_at = 0.0

    async def offer(self, role: str, call_id: str, sdp: str) -> str:
        if role not in ("caller", "receiver") or not sdp or len(sdp) > 65536:
            raise ValueError("invalid media offer")
        if self.call_id and self.call_id != call_id:
            await self.close()
        self.call_id = call_id
        await self.close_peer(role)
        pc = RTCPeerConnection(self.configuration)
        output = OutboundAudio()
        self.peers[role], self.outputs[role] = pc, output
        pc.addTrack(output)

        @pc.on("track")
        def track_received(track):
            if track.kind == "audio":
                self.pumps[role] = asyncio.create_task(self._pump(role, call_id, track))

        @pc.on("connectionstatechange")
        async def changed():
            if (pc.connectionState in ("failed", "closed") and self.call_id == call_id
                    and self.peers.get(role) is pc):
                asyncio.create_task(self.failure("media_unavailable"))

        try:
            await pc.setRemoteDescription(RTCSessionDescription(sdp=sdp, type="offer"))
            await pc.setLocalDescription(await pc.createAnswer())
            return pc.localDescription.sdp
        except Exception:
            await self.close_peer(role)
            raise

    async def _pump(self, role: str, call_id: str, track) -> None:
        resampler = av.AudioResampler(format="s16", layout="mono", rate=RATE)
        try:
            while self.call_id == call_id:
                frame = await track.recv()
                pcm = b"".join(bytes(f.planes[0])[:f.samples * 2]
                               for f in resampler.resample(frame))
                if role == "caller" and self.route == "screen" and self.screen:
                    self.screen.feed(pcm)
                elif self.route == "human":
                    target = "receiver" if role == "caller" else "caller"
                    if target in self.outputs:
                        self.outputs[target].push(pcm)
        except asyncio.CancelledError:
            raise
        except Exception:
            if self.call_id == call_id:
                asyncio.create_task(self.failure("media_unavailable"))

    async def start_screen(self, call_id: str) -> None:
        if self.call_id != call_id or "caller" not in self.outputs:
            raise RuntimeError("caller audio is not ready")
        self.route = "screen"
        self.screen = HiggsScreen(self.turn, self.caption, self.failure, self.key,
                                 self.url, self.outputs["caller"], on_interrupt=self.interrupt)
        try:
            await self.screen.start()
        except Exception:
            await self.screen.close()
            self.screen = None
            self.route = "silent"
            raise

    def pause_screen(self) -> None:
        if self.screen:
            self.screen.pause()
            self.screen_paused_at = time.monotonic()

    async def human(self, call_id: str) -> bool:
        if self.call_id != call_id or set(self.peers) != {"caller", "receiver"}:
            return False
        if any(pc.connectionState != "connected" for pc in self.peers.values()):
            return False
        if self.screen and time.monotonic() - self.screen_paused_at < 0.4:
            return False
        if self.route == "human":
            return True
        if self.screen:
            await self.screen.close()
            self.screen = None
        for output in self.outputs.values():
            output.clear()
            output.max_buffer_ms = 300
            output.enabled = True
        self.route = "human"
        return True

    async def close_peer(self, role: str) -> None:
        task = self.pumps.pop(role, None)
        if task:
            task.cancel()
        output = self.outputs.pop(role, None)
        if output:
            output.stop()
        pc = self.peers.pop(role, None)
        if pc:
            await pc.close()
        if task and task is not asyncio.current_task():
            with suppress(asyncio.CancelledError):
                await task

    async def close(self) -> None:
        self.route = "silent"
        self.call_id = None
        if self.screen:
            await self.screen.close()
            self.screen = None
        for role in list(self.peers):
            await self.close_peer(role)
