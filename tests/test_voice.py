"""VoiceCall against a fake Higgs voice server and fake audio devices (no sound card needed)."""
import asyncio
import base64
import json
import re

from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

from hangon.classifier import HeuristicClassifier
from hangon.config import Settings
from hangon.models import Outcome, Persona
from hangon.store import Store
from hangon.voice import VoiceCall


class FakeAudio:
    def __init__(self, playing=False):
        self.on_chunk = None
        self.played = []
        self.cleared = 0
        self.playing = playing
        self.stopped = False

    def start(self, on_mic_chunk):
        self.on_chunk = on_mic_chunk

    def play(self, pcm):
        self.played.append(pcm)

    def clear(self):
        self.cleared += 1
        self.playing = False

    def played_samples(self):
        return 24000  # one second heard

    def reset_counter(self):
        pass

    def is_playing(self):
        return self.playing

    def stop(self):
        self.stopped = True


def make_server(received, caller_lines, barge_in_at=None):
    """Speaks a line per response.create; after each reply, the 'caller' says the next scripted line."""
    lines = list(caller_lines)

    async def handler(ws):
        n = 0
        async for raw in ws:
            evt = json.loads(raw)
            received.append(evt)
            t = evt["type"]
            if t == "session.update":
                await ws.send(json.dumps({"type": "session.created"}))
            elif t == "response.create":
                n += 1
                instr = evt["response"]["instructions"]
                m = re.search(r'Say exactly this and nothing else: "([^"]+)"', instr)
                line = m.group(1) if m else f"Spoken line {n}."
                await ws.send(json.dumps({"type": "response.created", "response": {"id": f"r{n}"}}))
                await ws.send(json.dumps({"type": "response.output_item.added", "item": {"id": f"item{n}"}}))
                await ws.send(json.dumps({"type": "response.output_audio.delta",
                                          "delta": base64.b64encode(b"\x01\x00" * 240).decode()}))
                if barge_in_at == n:
                    await ws.send(json.dumps({"type": "input_audio_buffer.speech_started", "item_id": "u"}))
                await ws.send(json.dumps({"type": "response.output_audio_transcript.done", "transcript": line}))
                await ws.send(json.dumps({"type": "response.done", "response": {"status": "completed"}}))
                if lines:
                    nxt = lines.pop(0)
                    for i, said in enumerate(nxt if isinstance(nxt, list) else [nxt]):
                        await ws.send(json.dumps({"type": "input_audio_buffer.speech_stopped", "item_id": f"u{n}{i}"}))
                        await ws.send(json.dumps({"type": "conversation.item.input_audio_transcription.completed",
                                                  "item_id": f"u{n}{i}", "transcript": said}))
    return handler


class Capture:
    def __init__(self):
        self.sent = []

    def send(self, to, text, attachment=None):
        self.sent.append((to, text, attachment))


def run_voice(tmp_path, caller_lines, audio=None, barge_in_at=None, number="+16475550999", hang_up_after=None):
    received = []

    async def go():
        async with serve(make_server(received, caller_lines, barge_in_at), "127.0.0.1", 0) as srv:
            port = srv.sockets[0].getsockname()[1]
            settings = Settings(reports_dir=str(tmp_path / "r"), boson_api_key="k")

            async def fake_connect(s):
                return await connect(f"ws://127.0.0.1:{port}")

            call = VoiceCall(number, HeuristicClassifier(), Store(str(tmp_path / "t.db")), settings, Capture(),
                             audio or FakeAudio(), Persona(), connect=fake_connect)
            if hang_up_after is not None:
                asyncio.get_running_loop().call_later(hang_up_after, call.hang_up)
            session = await asyncio.wait_for(call.run(), timeout=10)
            return call, session
    call, session = asyncio.run(go())
    return call, session, received


CRA = ["Hello, this is Officer Davis from the Canada Revenue Agency.",
       "You owe back taxes and a warrant will be issued for your arrest within 24 hours.",
       "You must pay immediately with gift cards. Do not tell anyone about this call.",
       "Call me back at 647-555-0199 once you have the cards."]


def test_session_is_configured_for_voice_with_automatic_replies_off(tmp_path):
    _, _, received = run_voice(tmp_path, CRA)
    cfg = received[0]["session"]
    assert cfg["output_modalities"] == ["audio"]
    td = cfg["audio"]["input"]["turn_detection"]
    assert td["type"] == "server_vad" and td["create_response"] is False and td["interrupt_response"] is True
    assert cfg["audio"]["input"]["transcription"]["model"] == "higgs-stt-3.1"
    assert cfg["audio"]["input"]["format"] == {"type": "audio/pcm", "rate": 24000}


def test_scam_voice_call_stalls_collects_evidence_and_blocks(tmp_path):
    call, session, received = run_voice(tmp_path, CRA)
    assert session.outcome is Outcome.SCAM_ENDED and call.store.is_blocked(call.number)
    creates = [e for e in received if e["type"] == "response.create"]
    assert len(creates) == 5                                   # greeting + one reply per caller turn
    assert "Who's calling?" in creates[0]["response"]["instructions"]
    assert any("You are now Margaret" in c["response"]["instructions"] for c in creates)
    assert call.audio.played and call.audio.stopped
    assert [w for w, _ in session.transcript].count("caller") == 4


def test_legit_voice_call_is_forwarded(tmp_path):
    _, session, _ = run_voice(tmp_path, ["Hi, this is Riverside Pharmacy calling.",
                                         "Margaret's prescription is ready for pickup."], number="+16475550998")
    assert session.outcome is Outcome.FORWARDED


def test_caller_talking_over_margaret_truncates_her_reply(tmp_path):
    audio = FakeAudio(playing=True)
    call, _, received = run_voice(tmp_path, CRA[:1], audio=audio, barge_in_at=1, hang_up_after=1.0,
                                  number="+16475550997")
    truncates = [e for e in received if e["type"] == "conversation.item.truncate"]
    assert truncates and truncates[0]["item_id"] == "item1" and truncates[0]["audio_end_ms"] == 1000
    assert audio.cleared >= 1


def test_hanging_up_mid_call_ends_cleanly(tmp_path):
    _, session, _ = run_voice(tmp_path, ["Hello?"], hang_up_after=0.5, number="+16475550996")
    assert session.ended and session.outcome is Outcome.HANGUP_UNSURE


def test_blocked_number_is_rejected_without_connecting(tmp_path):
    Store(str(tmp_path / "t.db")).block("+16475550995")
    called = []

    async def go():
        async def fake_connect(s):
            called.append(1)
        call = VoiceCall("+16475550995", HeuristicClassifier(), Store(str(tmp_path / "t.db")),
                         Settings(reports_dir=str(tmp_path / "r")), Capture(), FakeAudio(), connect=fake_connect)
        return await call.run()
    session = asyncio.run(go())
    assert session.outcome is Outcome.REJECTED and not called


def test_mic_audio_is_streamed_to_higgs(tmp_path):
    audio = FakeAudio()

    async def go():
        received = []
        async with serve(make_server(received, []), "127.0.0.1", 0) as srv:
            port = srv.sockets[0].getsockname()[1]

            async def fake_connect(s):
                return await connect(f"ws://127.0.0.1:{port}")
            call = VoiceCall("+16475550994", HeuristicClassifier(), Store(str(tmp_path / "t.db")),
                             Settings(reports_dir=str(tmp_path / "r")), Capture(), audio, connect=fake_connect)
            loop = asyncio.get_running_loop()
            loop.call_later(0.3, lambda: audio.on_chunk(b"\x00\x01" * 2400))
            loop.call_later(0.8, call.hang_up)
            await call.run()
        return received
    received = asyncio.run(go())
    appends = [e for e in received if e["type"] == "input_audio_buffer.append"]
    assert appends and base64.b64decode(appends[0]["audio"]) == b"\x00\x01" * 2400


def test_session_uses_a_stricter_speech_detector(tmp_path):
    _, _, received = run_voice(tmp_path, CRA, number="+16475550993")
    td = received[0]["session"]["audio"]["input"]["turn_detection"]
    assert td["threshold"] == 0.8 and td["silence_duration_ms"] == 600


def test_one_word_noise_that_cut_margaret_off_is_ignored_and_she_carries_on(tmp_path):
    audio = FakeAudio(playing=True)
    call, session, received = run_voice(
        tmp_path, ["This is the CRA, you owe back taxes and a warrant is out for your arrest.", "E."],
        audio=audio, barge_in_at=2, hang_up_after=1.5, number="+16475550992")
    creates = [e["response"]["instructions"] for e in received if e["type"] == "response.create"]
    assert len(creates) == 3 and "cut off by a small noise" in creates[2]
    assert [w for w, _ in session.transcript].count("caller") == 1     # the noise is not a turn


def test_bits_said_back_to_back_are_merged_into_one_turn(tmp_path):
    _, session, _ = run_voice(tmp_path, [["Hi, this is Riverside", "Pharmacy, the prescription is ready for pickup."]],
                              hang_up_after=1.5, number="+16475550991")
    callers = [t for w, t in session.transcript if w == "caller"]
    assert callers == ["Hi, this is Riverside Pharmacy, the prescription is ready for pickup."]
    assert session.outcome is Outcome.FORWARDED


def test_model_name_comes_from_settings(tmp_path):
    from hangon.config import Settings as S
    s = S(higgs_model="higgs-realtime-1.5")
    call = VoiceCall("+1", HeuristicClassifier(), Store(str(tmp_path / "t.db")), s, Capture(), FakeAudio())
    assert call.session_config()["session"]["model"] == "higgs-realtime-1.5"


def test_model_list_parsing_handles_openai_shape():
    from hangon.list_models import model_ids
    assert model_ids({"object": "list", "data": [{"id": "higgs-realtime"}, {"id": "higgs-realtime-1.5"}]}) == \
        ["higgs-realtime", "higgs-realtime-1.5"]
    assert model_ids({"models": [{"name": "x"}]}) == ["x"]
