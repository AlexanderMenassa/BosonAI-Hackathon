"""Synthetic audio and route checks; these do not contact Higgs."""
import asyncio
import base64
import json
from fractions import Fraction

import av
from aiortc import MediaStreamTrack, RTCPeerConnection, RTCSessionDescription
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

from hangon.media import HiggsScreen, MediaBridge, OutboundAudio


class ToneTrack(MediaStreamTrack):
    kind = "audio"

    def __init__(self):
        super().__init__()
        self.samples = 0

    async def recv(self):
        frame = av.AudioFrame(format="s16", layout="mono", samples=480)
        frame.sample_rate = 24000
        frame.pts = self.samples
        frame.time_base = Fraction(1, 24000)
        frame.planes[0].update(b"\x01\x40" * 480)
        self.samples += 480
        await asyncio.sleep(0.02)
        return frame


def test_outbound_audio_paces_and_clears_queued_speech():
    async def scenario():
        output = OutboundAudio()
        output.enabled = True
        output.push(b"\x01\x00" * 960)
        first = await output.recv()
        assert bytes(first.planes[0])[:960] == b"\x01\x00" * 480
        output.clear()
        second = await output.recv()
        assert bytes(second.planes[0])[:960] == b"\x00" * 960
        assert second.pts == first.pts + 480
        output.stop()
    asyncio.run(scenario())


def test_human_route_requires_both_connected_peers_and_closes_screen():
    class Peer:
        def __init__(self, state):
            self.connectionState = state

    class Screen:
        def __init__(self):
            self.closed = False

        async def close(self):
            self.closed = True

    async def noop(_):
        pass

    async def scenario():
        media = MediaBridge(noop, noop, noop, "fake", "wss://example.invalid")
        media.call_id = "call-1"
        media.peers = {"caller": Peer("connected"), "receiver": Peer("connecting")}
        media.outputs = {"caller": OutboundAudio(), "receiver": OutboundAudio()}
        screen = Screen()
        media.screen = screen
        assert not await media.human("call-1")
        assert not screen.closed
        media.peers["receiver"].connectionState = "connected"
        assert await media.human("call-1")
        assert screen.closed and media.screen is None and media.route == "human"
        assert all(output.enabled for output in media.outputs.values())
        assert await media.human("call-1")
        for output in media.outputs.values():
            output.stop()
    asyncio.run(scenario())


def test_webrtc_human_audio_crosses_backend_after_handoff():
    async def noop(_):
        pass

    async def scenario():
        bridge = MediaBridge(noop, noop, noop, "fake", "wss://example.invalid")
        clients = []
        heard = asyncio.Event()
        try:
            for role in ("caller", "receiver"):
                pc = RTCPeerConnection()
                clients.append(pc)
                pc.addTrack(ToneTrack())

                @pc.on("track")
                def on_track(track, role=role):
                    if role == "receiver":
                        async def listen():
                            for _ in range(100):
                                frame = await track.recv()
                                if any(bytes(frame.planes[0])):
                                    heard.set()
                                    return
                        asyncio.create_task(listen())

                await pc.setLocalDescription(await pc.createOffer())
                answer = await bridge.offer(role, "test-call", pc.localDescription.sdp)
                await pc.setRemoteDescription(RTCSessionDescription(sdp=answer, type="answer"))

            async def ready():
                while not await bridge.human("test-call"):
                    await asyncio.sleep(0.05)
            await asyncio.wait_for(ready(), 10)
            await asyncio.wait_for(heard.wait(), 10)
        finally:
            await bridge.close()
            for pc in clients:
                await pc.close()
    asyncio.run(scenario())


def test_higgs_screen_configures_audio_and_deduplicates_final_transcript():
    async def scenario():
        received = []
        turns = []
        captions = []
        errors = []

        async def provider(ws):
            async for raw in ws:
                event = json.loads(raw)
                received.append(event)
                if event["type"] == "session.update":
                    await ws.send(json.dumps({"type": "session.updated"}))
                elif event["type"] == "response.create":
                    await ws.send(json.dumps({"type": "response.created", "response": {"id": "r1"}}))
                    await ws.send(json.dumps({"type": "response.output_audio.delta",
                                               "response_id": "r1", "delta": base64.b64encode(b"\x01\x00" * 480).decode()}))
                    for _ in range(2):
                        await ws.send(json.dumps({"type": "conversation.item.input_audio_transcription.completed",
                                                   "item_id": "turn-1", "transcript": "Pharmacy pickup"}))

        async def on_turn(line):
            turns.append(line)

        async def on_caption(line):
            captions.append(line)

        async def on_error(reason):
            errors.append(reason)

        async with serve(provider, "127.0.0.1", 0) as server:
            url = f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}"

            async def opener(target, **kwargs):
                return await connect(target)

            output = OutboundAudio()
            screen = HiggsScreen(on_turn, on_caption, on_error, "test-key", url, output, opener)
            try:
                await screen.start()
                screen.feed(b"\x00\x01" * 480)
                async def complete():
                    while not turns or not any(e["type"] == "input_audio_buffer.append" for e in received):
                        await asyncio.sleep(0.01)
                await asyncio.wait_for(complete(), 2)
                await asyncio.sleep(0.05)
                assert turns == ["Pharmacy pickup"] and not errors
                cfg = received[0]["session"]
                assert cfg["output_modalities"] == ["audio"]
                assert cfg["audio"]["input"]["transcription"]["model"] == "higgs-stt-3.1"
                assert output.queue.qsize() == 1
            finally:
                await screen.close()
                output.stop()
    asyncio.run(scenario())


def test_higgs_speech_start_clears_playback_and_notifies_browser():
    async def scenario():
        interrupted = asyncio.Event()

        async def provider(ws):
            async for raw in ws:
                event = json.loads(raw)
                if event["type"] == "session.update":
                    await ws.send(json.dumps({"type": "session.updated"}))
                elif event["type"] == "response.create":
                    await ws.send(json.dumps({"type": "response.created", "response": {"id": "r1"}}))
                    await ws.send(json.dumps({"type": "input_audio_buffer.speech_started"}))

        async def noop(_):
            pass

        async with serve(provider, "127.0.0.1", 0) as server:
            url = f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}"

            async def opener(target, **kwargs):
                return await connect(target)

            async def on_interrupt():
                interrupted.set()

            output = OutboundAudio()
            output.enabled = True
            output.push(b"\x01\x00" * 480)
            screen = HiggsScreen(noop, noop, noop, "test-key", url, output, opener,
                                 on_interrupt=on_interrupt)
            try:
                await screen.start()
                await asyncio.wait_for(interrupted.wait(), 2)
                assert output.queue.empty()
                assert screen.suppress_output
            finally:
                await screen.close()
                output.stop()
    asyncio.run(scenario())
