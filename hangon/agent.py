"""The conversational agent: speaks in the persona's voice. It never decides routing.

Backends:
* ScriptedAgent: canned lines, fully offline. For tests and demos without an API key.
* HiggsTextAgent: Higgs Realtime over WebSocket in text mode (output_modalities = ["text"]).

Each turn the agent gets a mode (the role) and guidance (what to do this turn). How that reaches Higgs
mid-conversation is the "steering" strategy:

* "response": per-reply instructions on response.create. Higgs follows these immediately (verified with
  `python -m hangon.probe`), so this is the default.
* "session":  session.update before the reply. Higgs acknowledges it but the probe showed the change landing
  late or not at all, so it is kept only for comparison.
(System-role messages are rejected by Higgs: "Invalid role: system".)

Repeat guard: Higgs sometimes copies its own previous reply word for word. If a reply is (nearly) identical to
something already said, the agent retries once with a firmer instruction, then falls back to a scripted line,
so a call can never get stuck in a loop.

The wire protocol follows Boson's tutorial (boson-ai/higgs-realtime-tutorial) and pipecat-boson.
"""
from __future__ import annotations

import asyncio
import difflib
import itertools
import json
import re
from typing import Protocol

from .models import Mode, Persona
from .persona import base_prompt, build_instructions, greeting

STEERING = ("response", "session")
REPEAT_SIMILARITY = 0.8   # replies at least this similar to an earlier line count as repeats


class AgentError(RuntimeError):
    pass


class Agent(Protocol):
    async def start(self) -> None: ...
    async def say(self, mode: Mode, caller_text: str | None = None, guidance: str = "") -> str: ...
    async def close(self) -> None: ...


# --------------------------------------------------------------------------- scripted

class ScriptedAgent:
    """Deterministic stand-in for the real agent. Ignores guidance."""

    def __init__(self, persona: Persona):
        self.p = persona
        n = persona.name
        self._screen = itertools.cycle([
            "Sorry, who did you say you were with?",
            "And what is this regarding, exactly?",
            "Could you tell me which company that is?",
        ])
        self._stall = itertools.cycle([
            f"Oh, hello dear, it's {n}. Sorry, I was out in the garden. What was that again?",
            "My cat Biscuit just knocked over a plant, hold on. Can you say that slower?",
            "I'm not very good with phones, dear. Could you spell that for me?",
            "Oh my, that sounds serious. Now who did you say you were?",
            "Hang on, let me find my glasses. They were right here a minute ago.",
        ])

    async def start(self) -> None:
        return None

    async def say(self, mode: Mode, caller_text: str | None = None, guidance: str = "") -> str:
        n = self.p.name
        if mode is Mode.GREET:
            return greeting(self.p)
        if mode is Mode.SCREEN:
            return next(self._screen)
        if mode is Mode.STALL:
            return next(self._stall)
        if mode is Mode.HANDOFF:
            return "Oh, one moment, I'll put you through."
        if mode is Mode.HOLD:
            return f"{n} can't come to the phone right now, but I'll tell them you called. They'll ring you back on your official number."
        if mode is Mode.TAKE_MESSAGE:
            return f"I'm sorry, I couldn't quite catch what this is about. I'll pass a message to {n}. Goodbye."
        if mode is Mode.GOODBYE:
            return "Oh, there's someone at the door, dear. I must go. Goodbye now!"
        raise ValueError(mode)

    async def close(self) -> None:
        return None


# --------------------------------------------------------------------------- Higgs Realtime

class HiggsTextAgent:
    """Higgs Realtime in text mode. One persistent session for the whole call, so the model keeps the
    conversation history; the role and per-turn guidance are re-sent every turn via the steering strategy."""

    def __init__(self, persona: Persona, api_key: str,
                 url: str = "wss://api.boson.ai/v1/realtime?model=higgs-realtime",
                 model: str = "higgs-realtime", timeout: float = 30.0,
                 steering: str = "response", debug: bool = False):
        if not api_key:
            raise AgentError("BOSON_API_KEY is not set")
        if steering not in STEERING:
            raise AgentError(f"unknown steering {steering!r}; use one of {STEERING}")
        self.p, self.api_key, self.url, self.model, self.timeout = persona, api_key, url, model, timeout
        self.steering, self.debug = steering, debug
        self.ws = None
        self._session_instructions: str | None = None  # what the session currently holds
        self._said: list[str] = []                     # everything the agent has said this call
        self._fallback = ScriptedAgent(persona)        # used only if Higgs keeps repeating itself

    # ---- lifecycle
    async def start(self) -> None:
        from websockets.asyncio.client import connect  # imported lazily so offline use needs no websockets
        try:
            self.ws = await connect(self.url, additional_headers={"Authorization": f"Bearer {self.api_key}"})
        except Exception as e:
            raise AgentError(f"could not connect to Higgs ({type(e).__name__}: {e}). "
                             "Check BOSON_API_KEY, your credits, and your internet connection.") from e

    async def close(self) -> None:
        if self.ws is not None:
            try:
                await self.ws.close(1000, "call ended")
            finally:
                self.ws = None

    async def say(self, mode: Mode, caller_text: str | None = None, guidance: str = "") -> str:
        if self.ws is None:
            raise AgentError("call start() first")
        instructions = build_instructions(self.p, mode, guidance)
        self._log(f"mode={mode.value} steering={self.steering}\n      instructions: {instructions}")

        if caller_text is not None:
            await self._send({"type": "conversation.item.create", "item": {
                "type": "message", "role": "user", "content": [{"type": "input_text", "text": caller_text}]}})

        text = await self._respond(instructions)
        if self._is_repeat(text):
            self._log("REPEAT detected; retrying once with a firmer instruction")
            text = await self._respond(instructions + " IMPORTANT: your last reply repeated something you had "
                                       "already said. Write a completely new reply that follows this turn's instructions.")
            if self._is_repeat(text):
                text = await self._fallback.say(mode, caller_text)
                self._log(f"still repeating; using scripted fallback line: {text!r}")
        self._said.append(text)
        return text

    async def _respond(self, instructions: str) -> str:
        """Ask for one reply carrying these instructions, via the steering strategy."""
        # For response steering the session holds only the stable base prompt; the turn's role and guidance
        # ride on the reply itself. For session steering the session is updated before each reply.
        wanted = instructions if self.steering == "session" else base_prompt(self.p)
        if wanted != self._session_instructions:
            await self._configure(wanted)
        create: dict = {"type": "response.create"}
        if self.steering == "response":
            create["response"] = {"instructions": instructions}
        await self._send(create)
        return await self._collect_text()

    def _is_repeat(self, text: str) -> bool:
        if not text.strip():
            return True  # an empty reply is as useless as a repeat
        norm = lambda t: re.sub(r"[^a-z0-9 ]", "", t.lower()).strip()
        new = norm(text)
        return any(difflib.SequenceMatcher(None, new, norm(old)).ratio() >= REPEAT_SIMILARITY for old in self._said)

    # ---- protocol helpers
    def _log(self, msg: str) -> None:
        if self.debug:
            print(f"    [higgs] {msg}")

    async def _send(self, event: dict) -> None:
        self._log(f"-> {event['type']}" + (f" ({event['item']['role']})" if event.get("item") else ""))
        try:
            await self.ws.send(json.dumps(event))
        except Exception as e:
            raise AgentError(self._closed_message(e)) from e

    async def _recv(self) -> dict:
        try:
            raw = await asyncio.wait_for(self.ws.recv(), timeout=self.timeout)
        except asyncio.TimeoutError as e:
            raise AgentError(f"Higgs sent nothing for {self.timeout:.0f}s") from e
        except Exception as e:
            raise AgentError(self._closed_message(e)) from e
        evt = json.loads(raw)
        if not evt.get("type", "").endswith(".delta"):
            self._log(f"<- {evt.get('type')}")
        return evt

    @staticmethod
    def _closed_message(e: Exception) -> str:
        rcvd = getattr(e, "rcvd", None)
        code, reason = (rcvd.code, rcvd.reason) if rcvd else (None, "")
        hint = {3000: "the API key was rejected", 4429: "no credits left (billing refused)",
                1013: "server busy, try again"}.get(code, "")
        return f"Higgs connection closed (code {code}{', ' + reason if reason else ''})" + (f": {hint}" if hint else "")

    async def _configure(self, instructions: str) -> None:
        """session.update, then wait for the acknowledgement."""
        await self._send({"type": "session.update", "session": {
            "model": self.model, "instructions": instructions, "output_modalities": ["text"]}})
        while True:
            evt = await self._recv()
            t = evt.get("type")
            if t in ("session.created", "session.updated"):
                self._session_instructions = instructions
                return
            if t == "error":
                raise AgentError(f"session.update rejected: {evt}")

    async def _collect_text(self) -> str:
        parts: list[str] = []
        final = ""
        while True:
            evt = await self._recv()
            t = evt.get("type", "")
            if t == "response.created":
                self._log(f"   response id: {(evt.get('response') or {}).get('id')}")
            elif t == "response.output_text.delta":
                parts.append(evt.get("delta", ""))
            elif t == "response.output_text.done":
                final = evt.get("text", "") or ""
                self._log(f"   deltas={len(parts)} final text={final[:80]!r}")
            elif t == "response.done":
                if not parts and final:
                    parts = [final]
                status = (evt.get("response") or {}).get("status")
                if status in ("failed", "cancelled", "incomplete") and not parts:
                    raise AgentError(f"response {status}: {evt}")
                return "".join(parts).strip()
            elif t == "error":
                raise AgentError(f"server error: {evt}")
            elif t in ("session.idle_timeout", "session.max_duration_reached"):
                raise AgentError(f"session ended: {t}")
