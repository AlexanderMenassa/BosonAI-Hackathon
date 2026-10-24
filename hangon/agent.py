"""The conversational agent: speaks AS the protected person. It never decides routing.

Backends:
* ScriptedAgent: canned lines, fully offline. For tests and demos without an API key.
* HiggsTextAgent: Higgs Realtime over WebSocket in text mode (output_modalities = ["text"]).

Each turn the agent gets a mode (the role) and guidance (what to do this turn). Both ride on that one reply as
per-reply instructions on response.create: `python -m hangon.probe` showed Higgs follows these immediately,
while a mid-call session.update lands late. The session itself only holds the stable base prompt.

Repeat guard: Higgs sometimes copies its own previous reply word for word, or opens every reply with the same stock
phrase ("Oh, hello there. ...") while the rest differs, which whole-reply similarity cannot see. If a reply is
(nearly) identical to something already said, or starts the same way, the agent retries once with a firmer
instruction that names the opening to avoid, then falls back to a scripted line, so a call can never get stuck
in a loop.

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
from .persona import base_prompt, build_instructions, exact_line, greeting

REPEAT_SIMILARITY = 0.8   # replies at least this similar to an earlier line count as repeats
OPENING_WORDS = 3         # a reply that starts with the same first words as an earlier one also counts as a repeat


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
        self._screen = itertools.cycle([
            "Sorry, dear, who is this?",
            "And what's this about?",
            "Could you tell me a little more about that?",
        ])
        self._stall = itertools.cycle([
            "Oh my. And what is it you need from me, exactly?",
            "How would that work, dear?",
            "Let me find a pen. What number can I call you back on?",
            "And what happens after that?",
            "What did you say your name was, dear?",
        ])

    async def start(self) -> None:
        return None

    async def say(self, mode: Mode, caller_text: str | None = None, guidance: str = "") -> str:
        if mode is Mode.GREET:
            return greeting(self.p)
        if mode is Mode.SCREEN:
            return next(self._screen)
        if mode is Mode.STALL:
            return next(self._stall)
        return exact_line(self.p, mode)  # HANDOFF, HOLD, TAKE_MESSAGE, GOODBYE

    async def close(self) -> None:
        return None


# --------------------------------------------------------------------------- Higgs Realtime

class HiggsTextAgent:
    """Higgs Realtime in text mode. One persistent session for the whole call, so the model keeps the
    conversation history; the role and per-turn guidance ride on each reply."""

    def __init__(self, persona: Persona, api_key: str,
                 url: str = "wss://api.boson.ai/v1/realtime?model=higgs-realtime",
                 model: str = "higgs-realtime", timeout: float = 30.0, debug: bool = False):
        if not api_key:
            raise AgentError("BOSON_API_KEY is not set")
        self.p, self.api_key, self.url, self.model, self.timeout = persona, api_key, url, model, timeout
        self.debug = debug
        self.ws = None
        self._configured = False
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
        instructions = build_instructions(self.p, mode, guidance, caller_text)
        self._log(f"mode={mode.value}\n      instructions: {instructions}")

        if caller_text is not None:
            await self._send({"type": "conversation.item.create", "item": {
                "type": "message", "role": "user", "content": [{"type": "input_text", "text": caller_text}]}})

        fixed = exact_line(self.p, mode) is not None      # fixed lines are word for word: no opener check
        text = await self._respond(instructions)
        if self._is_repeat(text, check_opening=not fixed):
            self._log("REPEAT detected; retrying once with a firmer instruction")
            avoid = self._opening(text)
            hint = f" Do not begin with the words '{avoid}'." if avoid and not fixed else ""
            text = await self._respond(instructions + " IMPORTANT: your last reply repeated something you had "
                                       "already said or started the same way as an earlier reply. Write a "
                                       "completely new reply that follows this turn's instructions." + hint)
            if self._is_repeat(text, check_opening=not fixed):
                text = await self._fallback.say(mode, caller_text)
                self._log(f"still repeating; using scripted fallback line: {text!r}")
        self._said.append(text)
        return text

    async def _respond(self, instructions: str) -> str:
        """Ask for one reply carrying these instructions."""
        if not self._configured:
            await self._configure(base_prompt(self.p))
        await self._send({"type": "response.create", "response": {"instructions": instructions}})
        return await self._collect_text()

    @staticmethod
    def _norm(t: str) -> str:
        return re.sub(r"[^a-z0-9 ]", "", t.lower()).strip()

    def _opening(self, text: str) -> str:
        """The first few words of a reply ('oh hello there'), or '' if it is too short to have one."""
        words = self._norm(text).split()
        return " ".join(words[:OPENING_WORDS]) if len(words) > OPENING_WORDS else ""

    def _is_repeat(self, text: str, check_opening: bool = True) -> bool:
        if not text.strip():
            return True  # an empty reply is as useless as a repeat
        new = self._norm(text)
        if any(difflib.SequenceMatcher(None, new, self._norm(old)).ratio() >= REPEAT_SIMILARITY for old in self._said):
            return True
        # Same stock opening as an earlier reply ("Oh, hello there. ..."), even though the rest differs.
        opening = self._opening(text)
        return bool(check_opening and opening and any(opening == self._opening(old) for old in self._said))

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
                self._configured = True
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
