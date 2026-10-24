"""Browser screening uses the selected classifier and engine-directed speech."""
import asyncio
import json
import stat
import sys
import time

import pytest
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

from hangon.browser.server import Room, SafeBrowserClassifier
from hangon.browser.media import HiggsScreen, OutboundAudio
from hangon.config import Settings
from hangon.models import ClassifierResult, ConnectionState, Evidence, Label, Mode
from hangon.store import Store


class RecordingClassifier:
    def __init__(self, label=Label.UNSURE, source="teammate"):
        self.label, self.source = label, source
        self.transcripts = []

    def classify(self, transcript):
        self.transcripts.append(list(transcript))
        return ClassifierResult(self.label, 0.05 if self.label is Label.LEGIT else 0.5,
                                "test assessment", Evidence(), source=self.source)


def test_browser_room_passes_configured_voice_to_media(tmp_path):
    room = Room("demo", Store(str(tmp_path / "calls.db")), live_media=True,
                settings=Settings(higgs_voice="voice_walter_test"))
    assert room.media.voice == "voice_walter_test"

@pytest.mark.skipif(sys.platform == "win32", reason="Windows has no Unix file modes (0o600)")
def test_browser_call_writes_private_log_without_exposing_it_in_room_state(tmp_path):
    async def scenario():
        class Socket:
            def __init__(self):
                self.messages = []

            async def send(self, message):
                self.messages.append(message)

        room = Room("demo", Store(str(tmp_path / "calls.db")),
                    call_log_dir=tmp_path / "logs")
        socket = Socket()
        caller = room.bind("caller", socket)
        await room.command(caller, socket, {"type": "call.start", "token": caller.token,
                                            "number": "+14165550111", "request_id": "first"})
        path = room.call_log_path
        assert path.parent == tmp_path / "logs"
        assert "incoming call from +14165550111" in path.read_text()
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert all("call_log" not in message for message in socket.messages)

    asyncio.run(scenario())


def test_browser_uses_injected_classifier_and_turn_guidance(tmp_path):
    async def scenario():
        classifier = RecordingClassifier()
        room = Room("demo", Store(str(tmp_path / "calls.db")), classifier=classifier,
                    call_log_dir=tmp_path / "logs")
        room.gateway.arrive("request-1", "+14165550111")
        room._start_log(room.gateway.incoming.call_id)

        class Screen:
            def __init__(self):
                self.calls = []
                self.active = True

            async def speak(self, *args):
                self.calls.append(args)

        class Media:
            call_id = room.gateway.incoming.call_id
            screen = Screen()
            route = "screen"

        room.media = Media()
        await room._on_turn("My name is Sam, I have a question")
        assert classifier.transcripts == [[("caller", "My name is Sam, I have a question")]]
        mode, guidance, current, earlier = room.media.screen.calls[0]
        assert mode is Mode.SCREEN
        assert current == "My name is Sam, I have a question" and earlier == []
        assert "name" in guidance.lower()
        assert room.snapshot("caller")["classifier_source"] == "teammate"
        log = room.call_log_path.read_text()
        assert "caller> My name is Sam, I have a question" in log
        assert "verdict=UNSURE" in log and "classifier=teammate" in log
        assert "call_log" not in room.snapshot("caller")

    asyncio.run(scenario())


def test_higgs_reply_receives_previous_caller_turns():
    async def scenario():
        class Socket:
            def __init__(self):
                self.sent = []

            async def send(self, raw):
                import json
                self.sent.append(json.loads(raw))

        async def noop(_):
            pass

        screen = HiggsScreen(noop, noop, noop, "test-key", "wss://example.invalid", OutboundAudio())
        screen.ws = Socket()
        screen.active = True
        await screen.speak(Mode.SCREEN, "The caller gave their name: Sam.",
                           "Tomorrow afternoon works", ["I am Sam, calling about a visit"])
        instructions = screen.ws.sent[0]["response"]["instructions"]
        assert "I am Sam, calling about a visit" in instructions
        assert "Tomorrow afternoon works" in instructions
        assert "name: Sam" in instructions

    asyncio.run(scenario())


def test_fallback_assessment_cannot_approve_or_block():
    classifier = RecordingClassifier(Label.LEGIT, "heuristic (fallback after: TimeoutError)")
    result = SafeBrowserClassifier(classifier).classify([("caller", "Riverside Pharmacy")])
    assert result.label is Label.UNSURE and result.source == "unavailable"
    assert result.reason == "classifier unavailable (TimeoutError)"


def test_browser_replies_before_slow_verdict_then_ends_scam(tmp_path):
    class SlowScam:
        def classify(self, transcript):
            time.sleep(0.15)
            return ClassifierResult(Label.SCAM, 0.99, "gift card demand",
                                    Evidence(payment_method="gift cards"), source="test")

    async def scenario():
        class Socket:
            def __init__(self):
                self.messages = []

            async def send(self, message):
                self.messages.append(json.loads(message))

        store = Store(str(tmp_path / "calls.db"))
        room = Room("demo", store, classifier=SlowScam())
        receiver_socket = Socket()
        room.bind("receiver", receiver_socket)
        room.gateway.arrive("slow", "+14165550111")
        room.gateway.session.cfg.classifier_grace = 0.01
        class Screen:
            active = True

            def __init__(self):
                self.modes = []

            async def speak(self, mode, *args):
                self.modes.append(mode)

            async def finish_reply(self):
                pass

        class Media:
            call_id = room.gateway.incoming.call_id
            screen = Screen()
            route = "screen"

            async def close(self):
                pass

        room.media = Media()
        started = time.monotonic()
        await room._on_turn("Buy gift cards now")
        assert time.monotonic() - started < 0.12
        assert room.gateway.state is ConnectionState.SCREENING
        assert room.gateway.last_decision.mode is Mode.SCREEN
        assert room.media.screen.modes == [Mode.SCREEN]
        async def ended():
            while room.gateway.state is not ConnectionState.ENDED:
                await asyncio.sleep(0.01)
        await asyncio.wait_for(ended(), 2)
        assert store.is_blocked("+14165550111")
        assert room.media.screen.modes == [Mode.SCREEN, Mode.GOODBYE]
        assert sum(e.type == "call.ended" for e in room.gateway.events) == 1
        ended_states = [message for message in receiver_socket.messages if message["state"] == "ended"]
        assert ended_states
        assert ended_states[-1]["receiver_messages"][-1]["kind"] == "scam"
        assert ended_states[-1]["receiver_messages"][-1]["number"] == "+14165550111"
        assert ended_states[-1]["receiver_messages"][-1]["blocked"]

    asyncio.run(scenario())


def test_browser_requests_higgs_reply_before_slow_classifier_finishes(tmp_path):
    class SlowUnsure:
        def classify(self, transcript):
            time.sleep(0.4)
            return ClassifierResult(Label.UNSURE, 0.5, "needs more detail", Evidence(), source="test")

    async def scenario():
        requested = asyncio.Event()
        times = {}

        async def provider(ws):
            replies = 0
            async for raw in ws:
                event = json.loads(raw)
                if event["type"] == "session.update":
                    await ws.send('{"type":"session.created"}')
                elif event["type"] == "response.create":
                    replies += 1
                    if replies == 1:
                        await ws.send('{"type":"response.created","response":{"id":"greeting"}}')
                        await ws.send('{"type":"response.done"}')
                        times["turn"] = time.monotonic()
                        await ws.send('{"type":"conversation.item.input_audio_transcription.completed",'
                                      '"item_id":"caller-1","transcript":"I am calling about an appointment"}')
                    else:
                        times["reply"] = time.monotonic()
                        requested.set()

        async with serve(provider, "127.0.0.1", 0) as server:
            url = f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}"
            async def opener(target, **kwargs):
                return await connect(target)

            room = Room("demo", Store(str(tmp_path / "calls.db")), classifier=SlowUnsure())
            room.gateway.arrive("slow-request", "+14165550111")
            room.gateway.session.cfg.classifier_grace = 0.02
            output = OutboundAudio()
            screen = HiggsScreen(room._on_turn, room._on_caption, room._on_media_failure,
                                 "test-key", url, output, opener)

            class Media:
                call_id = room.gateway.incoming.call_id
                route = "screen"

                async def close(self):
                    await screen.close()

            media = Media()
            media.screen = screen
            room.media = media
            try:
                await screen.start()
                await asyncio.wait_for(requested.wait(), 2)
                assert times["reply"] - times["turn"] < 0.2
                assert room.gateway.session.last_result is None
            finally:
                await screen.close()
                output.stop()

    asyncio.run(scenario())


def test_receiver_gets_unsure_summary_and_scam_reason_with_number_only_blocked(tmp_path):
    async def scenario():
        class ScamClassifier:
            def classify(self, transcript):
                return ClassifierResult(Label.SCAM, 0.99, "requested gift cards under threat",
                                        Evidence(payment_method="gift cards"), source="test")

        store = Store(str(tmp_path / "calls.db"))
        room = Room("demo", store, classifier=RecordingClassifier())
        room.gateway.arrive("unsure-1", "+14165550111")
        room.gateway.caller_turn("I am calling about tomorrow's appointment")
        g = room.gateway
        g.command("caller", "call.end", g.incoming.call_id, "end-unsure", g.revision)
        await room.broadcast()
        unsure = room.snapshot("receiver")["receiver_messages"]
        assert len(unsure) == 1
        assert unsure[0]["kind"] == "unsure" and unsure[0]["number"] == "+14165550111"
        assert "tomorrow's appointment" in unsure[0]["detail"]
        assert not store.is_blocked("+14165550111")
        assert room.snapshot("caller")["receiver_messages"] == []
        await room.broadcast()
        assert len(room.snapshot("receiver")["receiver_messages"]) == 1

        room.gateway.classifier = SafeBrowserClassifier(ScamClassifier())
        room.gateway.arrive("scam-1", "+14165550222")
        room.logged_outcome = False
        room.gateway.caller_turn("Buy gift cards now or I will have you arrested")
        await room.broadcast()
        scam = room.snapshot("receiver")["receiver_messages"][-1]
        assert scam["kind"] == "scam" and scam["number"] == "+14165550222"
        assert "gift cards" in scam["detail"] and scam["blocked"]
        assert [entry["number"] for entry in store.blocklist()] == ["+14165550222"]
        assert store.is_blocked("+14165550222")

    asyncio.run(scenario())


def test_browser_remembers_facts_and_spoken_reply_when_next_assessment_fails(tmp_path):
    async def scenario():
        class IntermittentClassifier:
            def __init__(self):
                self.transcripts = []

            def classify(self, transcript):
                self.transcripts.append(list(transcript))
                if len(self.transcripts) == 1:
                    from hangon.classifier import JudgeResult
                    return JudgeResult(Label.UNSURE, 0.5, "needs clarification", Evidence(),
                                       source="gemini", caller_name="Sam", purpose="a visit")
                raise TimeoutError("provider timed out")

        classifier = IntermittentClassifier()
        room = Room("demo", Store(str(tmp_path / "calls.db")), classifier=classifier,
                    call_log_dir=tmp_path / "logs")
        room.gateway.arrive("request-1", "+14165550111")
        room._start_log(room.gateway.incoming.call_id)

        class Screen:
            active = True

            def __init__(self):
                self.calls = []

            async def speak(self, *args):
                self.calls.append(args)

        class Media:
            call_id = room.gateway.incoming.call_id
            screen = Screen()
            route = "screen"

        room.media = Media()
        await room._on_turn("I am Sam, calling about a visit")
        await room._on_caption("When would you like to visit?")
        await room._on_turn("Tomorrow afternoon")
        assert ("agent", "When would you like to visit?") in classifier.transcripts[1]
        mode, guidance, current, earlier = room.media.screen.calls[1]
        assert mode is Mode.SCREEN
        assert "Sam" in guidance and "a visit" in guidance
        assert earlier == ["I am Sam, calling about a visit"]
        assert room.snapshot("caller")["classifier_source"] == "unavailable"
        assert "agent> When would you like to visit?" in room.call_log_path.read_text()

    asyncio.run(scenario())
