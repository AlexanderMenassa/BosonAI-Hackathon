"""Browser screening uses the selected classifier and engine-directed speech."""
import asyncio
import stat

from hangon.browser.server import Room, SafeBrowserClassifier
from hangon.browser.media import HiggsScreen, OutboundAudio
from hangon.models import ClassifierResult, Evidence, Label, Mode
from hangon.store import Store


class RecordingClassifier:
    def __init__(self, label=Label.UNSURE, source="teammate"):
        self.label, self.source = label, source
        self.transcripts = []

    def classify(self, transcript):
        self.transcripts.append(list(transcript))
        return ClassifierResult(self.label, 0.05 if self.label is Label.LEGIT else 0.5,
                                "test assessment", Evidence(), source=self.source)


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
