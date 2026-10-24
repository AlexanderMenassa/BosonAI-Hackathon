"""Voice calls: your microphone is the caller, Higgs speaks as the protected person's assistant.

    python -m hangon.voice                         # headphones recommended
    python -m hangon.voice --no-barge-in           # no headphones: mic is muted while the agent speaks
    python -m hangon.voice --classifier ollama     # local LLM (slow on CPU: long pauses)
    python -m hangon.voice --classifier gemini     # hosted Gemini (fast; needs GEMINI_API_KEY)
    python -m hangon.voice --list-devices          # pick --input-device / --output-device

Press Enter at any time to hang up.

How a turn works (same brain as the text simulator):

    caller speaks -> Higgs server VAD hears the end of the turn
      -> Higgs transcribes it (conversation.item.input_audio_transcription.completed)
      -> engine: classify + decide the mode           (automatic replies are OFF: create_response=false)
      -> response.create with that turn's role and guidance (per-reply instructions, the method that passed
         the probe)
      -> Higgs speaks; audio plays through the speakers
    caller talks over the agent -> server stops generating (interrupt_response=true), we flush the speaker
      and truncate its stored reply to what the caller actually heard. A short noise during the agent's reply is
      not a turn: the agent carries on. Any utterance on the caller's own turn counts, even one word.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import sys
import threading
import time

from .audio_io import RATE, AudioIO
from .classifier import Classifier
from .config import Settings
from .engine import CallSession
from .models import Action, Mode, Persona
from .notify import ConsoleNotifier, Notifier
from .persona import base_prompt, build_instructions, turn_guidance
from .reports import notify_after_call
from .sim import CLASSIFIERS
from .store import Store


class VoiceError(RuntimeError):
    pass


class VoiceCall:
    def __init__(self, number: str, classifier: Classifier, store: Store, settings: Settings, notifier: Notifier,
                 audio: AudioIO, persona: Persona | None = None, voice: str = "default", stt_language: str = "en",
                 debug: bool = False, connect=None):
        self.number, self.store, self.settings, self.notifier = number, store, settings, notifier
        self.audio, self.persona, self.voice, self.stt_language, self.debug = audio, persona or Persona(), voice, stt_language, debug
        self.session = CallSession(number, classifier, store, settings)
        self._connect = connect  # injectable for tests
        self.ws = None
        self.pending_action: Action | None = None   # FORWARD / END waiting for the last line to finish playing
        self.assistant_item: str | None = None      # the reply currently being spoken (for truncation)
        self.responding = False
        self.interrupted = False                    # the agent was cut off by the caller (or a noise)
        self.mode: Mode | None = None               # the mode of the reply most recently requested
        self.done = asyncio.Event()
        self.turns: asyncio.Queue[str] = asyncio.Queue()
        self.seen_items: set[str] = set()           # transcript item ids already turned into caller turns
        self.loop: asyncio.AbstractEventLoop | None = None

    # ------------------------------------------------------------------ output helpers
    def log(self, msg: str) -> None:
        print(msg, flush=True)

    def dbg(self, msg: str) -> None:
        if self.debug:
            print(f"    [higgs] {msg}", flush=True)

    async def send(self, evt: dict) -> None:
        if evt["type"] != "input_audio_buffer.append":
            self.dbg(f"-> {evt['type']}")
        await self.ws.send(json.dumps(evt))

    # ------------------------------------------------------------------ setup
    def session_config(self) -> dict:
        transcription = {"model": "higgs-stt-3.1"}
        if self.stt_language:
            transcription["language"] = self.stt_language
        return {"type": "session.update", "session": {
            "model": self.settings.higgs_model,
            "instructions": base_prompt(self.persona),
            "output_modalities": ["audio"],
            "audio": {
                "input": {
                    "format": {"type": "audio/pcm", "rate": RATE},
                    # Server detects end of turn, but does NOT reply on its own: the engine decides first.
                    "turn_detection": {"type": "server_vad", "threshold": self.settings.vad_threshold,
                                       "silence_duration_ms": 600, "prefix_padding_ms": 300,
                                       "create_response": False, "interrupt_response": True},
                    "transcription": transcription,
                },
                "output": {"format": {"type": "audio/pcm", "rate": RATE}, "voice": self.voice},
            },
        }}

    async def speak(self, mode: Mode, guidance: str = "") -> None:
        instructions = build_instructions(self.persona, mode, guidance)
        self.dbg(f"mode={mode.value} instructions: {instructions}")
        # One reply at a time: wait for the previous one to finish (or be cancelled by an interruption).
        for _ in range(100):
            if not self.responding:
                break
            await asyncio.sleep(0.1)
        self.responding = True
        self.mode = mode
        await self.send({"type": "response.create", "response": {"instructions": instructions}})

    # ------------------------------------------------------------------ main flow
    async def run(self) -> CallSession:
        self.loop = asyncio.get_running_loop()
        d = self.session.open()
        self.log(f"\n=== incoming call from {self.number} ===")
        if d.action in (Action.REJECT, Action.FORWARD_TRUSTED):
            self.log(f"[{d.action.value}] {d.note}")
            notify_after_call(self.session, self.settings, self.notifier)
            return self.session

        connect = self._connect or _default_connect
        try:
            self.ws = await connect(self.settings)
        except Exception as e:
            raise VoiceError(f"could not connect to Higgs ({type(e).__name__}: {e})") from e

        tasks = []
        try:
            await self.send(self.session_config())
            await self._wait_for_session()
            self.audio.start(self._on_mic_chunk)
            tasks = [asyncio.create_task(self._receiver()), asyncio.create_task(self._turn_worker())]
            await self.speak(Mode.GREET)
            await self.done.wait()
        finally:
            for t in tasks:
                t.cancel()
            self.audio.stop()
            try:
                await asyncio.wait_for(self.ws.close(), timeout=2)  # never hang on a slow close handshake
            except Exception:
                pass
        if not self.session.ended:
            self.session.on_hangup()
        self.log(f"\n=== call over: outcome={self.session.outcome.value}, number "
                 f"{'BLOCKED' if self.store.is_blocked(self.number) else 'not blocked'} ===")
        notify_after_call(self.session, self.settings, self.notifier)
        return self.session

    def hang_up(self) -> None:
        """The caller hung up (Enter pressed)."""
        if not self.done.is_set():
            self.log("[caller hung up]")
            self.session.on_hangup()
            self.done.set()

    async def _wait_for_session(self) -> None:
        while True:
            evt = json.loads(await asyncio.wait_for(self.ws.recv(), timeout=30))
            t = evt.get("type")
            self.dbg(f"<- {t}")
            if t in ("session.created", "session.updated"):
                td = (((evt.get("session") or {}).get("audio") or {}).get("input") or {}).get("turn_detection")
                self.log(f"higgs confirmed turn detection: {json.dumps(td) if td else '(not echoed)'}")
                return
            if t == "error":
                raise VoiceError(f"session.update rejected: {evt}")

    def _on_mic_chunk(self, pcm: bytes) -> None:
        """Called from the audio thread. Hand the chunk to the event loop to send."""
        if self.loop is None or self.done.is_set():
            return
        evt = {"type": "input_audio_buffer.append", "audio": base64.b64encode(pcm).decode()}
        asyncio.run_coroutine_threadsafe(self._safe_send(evt), self.loop)

    async def _safe_send(self, evt: dict) -> None:
        try:
            await self.send(evt)
        except Exception:
            pass  # socket closing at the end of the call

    # ------------------------------------------------------------------ events from Higgs
    async def _receiver(self) -> None:
        async for raw in self.ws:
            evt = json.loads(raw)
            t = evt.get("type", "")
            if not t.endswith(".delta"):
                self.dbg(f"<- {t}")
            if t == "input_audio_buffer.speech_started":
                await self._on_barge_in()
            elif t == "conversation.item.input_audio_transcription.completed":
                text = (evt.get("transcript") or "").strip()
                item = evt.get("item_id") or ""
                self.dbg(f"   transcript for item {item!r}: {text!r}")
                if item and item in self.seen_items:
                    self.dbg("   (same speech transcribed again: ignored)")
                elif text:
                    if item:
                        self.seen_items.add(item)
                    await self.turns.put(text)
            elif t == "conversation.item.input_audio_transcription.failed":
                self.log("  [couldn't transcribe that turn]")
            elif t == "response.created":
                self.audio.reset_counter()
            elif t == "response.output_item.added":
                self.assistant_item = (evt.get("item") or {}).get("id")
            elif t == "response.output_audio.delta":
                self.audio.play(base64.b64decode(evt.get("delta", "")))
            elif t == "response.output_audio_transcript.done":
                line = (evt.get("transcript") or "").strip()
                if line:
                    self.session.record_agent(line)
                    self.log(f"agent> {line}")
            elif t == "response.done":
                self.responding = False
                if self.pending_action:
                    asyncio.create_task(self._finish_after_playback())
            elif t == "error":
                self.log(f"  [higgs error] {evt.get('error', evt)}")
            elif t in ("session.idle_timeout", "session.max_duration_reached"):
                self.log(f"  [higgs] {t}")
                self.hang_up()
                return

    async def _on_barge_in(self) -> None:
        """Caller started talking. If the agent is mid-sentence, stop it where the caller heard it."""
        if self.audio.is_playing() and self.assistant_item and not self.pending_action:
            played_ms = int(self.audio.played_samples() * 1000 / RATE)
            self.audio.clear()
            self.interrupted = True
            self.responding = False  # the server cancels this reply (interrupt_response); don't wait on it
            self.log("  [caller interrupted]")
            await self.send({"type": "conversation.item.truncate", "item_id": self.assistant_item,
                             "content_index": 0, "audio_end_ms": played_ms})

    # ------------------------------------------------------------------ one caller turn at a time
    async def _turn_worker(self) -> None:
        while not self.done.is_set():
            text = await self.turns.get()
            while not self.turns.empty():          # caller said several bits in a row: one turn
                text += " " + self.turns.get_nowait()
            if self.session.ended or self.pending_action:
                continue
            was_interrupted, self.interrupted = self.interrupted, False
            if was_interrupted and len(text.split()) < self.settings.min_interrupt_words:
                # A cough or "mm" while the agent was talking: not a turn. Let the agent carry on.
                self.log(f"  [noise during the agent's reply, ignored: {text!r}]")
                if self.mode in (Mode.SCREEN, Mode.STALL):
                    await self.speak(self.mode, "You were cut off by a small noise. Carry on naturally from where "
                                                "you were, without repeating yourself or greeting them again.")
                continue
            last = next((t for w, t in reversed(self.session.transcript) if w == "caller"), "")
            if last and (text == last or text.startswith(last.rstrip(".!? "))) and self.session.transcript[-1][0] == "caller":
                self.log(f"  [repeat of the last caller turn, ignored: {text!r}]")
                continue
            self.log(f"caller> {text}")
            t0 = time.monotonic()
            d = await asyncio.to_thread(self.session.on_caller_turn, text)
            self.log(f"  [{self.session.status_line()}]  {self.session.last_result.reason}"
                     f"  ({time.monotonic() - t0:.1f}s)")
            guidance = turn_guidance(d.mode, self.session.evidence, self.session.turns, is_personal=self.session.is_personal)
            if d.action in (Action.END, Action.FORWARD):
                self.pending_action = d.action
                self.log(f"[{d.action.value}] {d.note}")
            await self.speak(d.mode, guidance)

    async def _finish_after_playback(self) -> None:
        """Let the last line finish playing, then forward or end the call."""
        while self.audio.is_playing():
            await asyncio.sleep(0.1)
        await asyncio.sleep(0.3)
        if self.pending_action is Action.FORWARD:
            self.log("[forwarding the call to the protected person]")
        self.session.finish()
        self.done.set()


async def _default_connect(settings: Settings):
    from websockets.asyncio.client import connect
    return await connect(settings.boson_url, additional_headers={"Authorization": f"Bearer {settings.boson_api_key}"},
                         max_size=None)


async def _main_async(args, settings: Settings) -> None:
    from .audio_io import SoundDeviceIO
    from .sim import make_classifier
    store = Store(settings.db_path)
    audio = SoundDeviceIO(args.input_device, args.output_device, mute_mic_while_playing=args.no_barge_in)
    print(f"model: {settings.higgs_model}")
    print(f"audio: mic {audio.in_rate} Hz, speaker {audio.out_rate} Hz"
          f"{'  (mic muted while the agent speaks)' if args.no_barge_in else '  (use headphones)'}")
    call = VoiceCall(args.number, make_classifier(args.classifier, settings), store, settings,
                     ConsoleNotifier(settings.reports_dir), audio, Persona(name=args.persona_name),
                     voice=args.voice, stt_language=args.language, debug=args.debug)

    loop = asyncio.get_running_loop()

    def wait_for_enter():  # daemon thread, so it never keeps the program alive after the call
        try:
            input("Press Enter to hang up.\n")
        except EOFError:
            pass
        try:
            loop.call_soon_threadsafe(call.hang_up)
        except RuntimeError:
            pass  # the call (and its event loop) already ended

    threading.Thread(target=wait_for_enter, daemon=True).start()
    await call.run()


def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--number", default="+14165550900", help="caller's phone number (pretend)")
    ap.add_argument("--classifier", choices=CLASSIFIERS, default="heuristic")
    ap.add_argument("--persona-name", default="Margaret")
    ap.add_argument("--voice", default="default", help="Higgs voice preset or registered voice id")
    ap.add_argument("--language", default="en", help="transcription language; '' to let Higgs detect it")
    ap.add_argument("--no-barge-in", action="store_true", help="mute the mic while the agent speaks (no headphones)")
    ap.add_argument("--input-device", type=int)
    ap.add_argument("--output-device", type=int)
    ap.add_argument("--list-devices", action="store_true")
    ap.add_argument("--debug", action="store_true")
    ap.add_argument("--db")
    args = ap.parse_args()

    if args.list_devices:
        from .audio_io import list_devices
        print(list_devices())
        return
    settings = Settings.from_env()
    if args.db:
        settings.db_path = args.db
    if not settings.boson_api_key:
        raise SystemExit("BOSON_API_KEY is not set (put it in .env)")
    try:
        asyncio.run(_main_async(args, settings))
    except VoiceError as e:
        raise SystemExit(f"[voice error] {e}")
    except KeyboardInterrupt:
        print("\n[stopped]")


if __name__ == "__main__":
    main()
