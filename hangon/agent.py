"""The conversational agent: speaks in the persona's voice. It never decides routing.

Backends:
* ScriptedAgent: canned lines, fully offline. For tests and demos without an API key.
* HiggsTextAgent: Higgs Realtime over WebSocket in text mode (output_modalities = ["text"]),
  so the whole decision loop can be built and tested before wiring microphone audio.

The Higgs wire protocol below follows Boson's own tutorial (boson-ai/higgs-realtime-tutorial) and the
pipecat-boson integration: bearer-token auth on the WebSocket, an OpenAI-compatible event protocol,
session.update to configure, conversation.item.create + response.create per turn, and
response.output_text.delta events until response.done.
"""
from __future__ import annotations

import asyncio
import itertools
import json
from typing import Protocol

from .models import Mode, Persona
from .persona import build_instructions, greeting


class AgentError(RuntimeError):
    pass


class Agent(Protocol):
    async def start(self) -> None: ...
    async def say(self, mode: Mode, caller_text: str | None = None) -> str: ...
    async def close(self) -> None: ...


# --------------------------------------------------------------------------- scripted

class ScriptedAgent:
    """Deterministic stand-in for the real agent."""

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

    async def say(self, mode: Mode, caller_text: str | None = None) -> str:
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
    """Higgs Realtime in text mode. One persistent session for the whole call, so the model keeps
    the conversation history. The instructions are swapped with session.update when the mode changes."""

    def __init__(self, persona: Persona, api_key: str,
                 url: str = "wss://api.boson.ai/v1/realtime?model=higgs-realtime",
                 model: str = "higgs-realtime", timeout: float = 30.0):
        if not api_key:
            raise AgentError("BOSON_API_KEY is not set")
        self.p, self.api_key, self.url, self.model, self.timeout = persona, api_key, url, model, timeout
        self.ws = None
        self._mode: Mode | None = None

    async def start(self) -> None:
        from websockets.asyncio.client import connect  # imported lazily so offline use needs no websockets
        self.ws = await connect(self.url, additional_headers={"Authorization": f"Bearer {self.api_key}"})

    async def say(self, mode: Mode, caller_text: str | None = None) -> str:
        if self.ws is None:
            raise AgentError("call start() first")
        if mode is not self._mode:
            await self._configure(mode)
            self._mode = mode
        if caller_text is not None:
            await self._send({
                "type": "conversation.item.create",
                "item": {"type": "message", "role": "user",
                         "content": [{"type": "input_text", "text": caller_text}]},
            })
        await self._send({"type": "response.create"})
        return await self._collect_text()

    async def close(self) -> None:
        if self.ws is not None:
            await self.ws.close(1000, "call ended")
            self.ws = None

    # ---- protocol helpers
    async def _send(self, event: dict) -> None:
        await self.ws.send(json.dumps(event))

    async def _recv(self) -> dict:
        raw = await asyncio.wait_for(self.ws.recv(), timeout=self.timeout)
        return json.loads(raw)

    async def _configure(self, mode: Mode) -> None:
        """session.update with the instructions for this mode, then wait for the acknowledgement."""
        await self._send({
            "type": "session.update",
            "session": {
                "model": self.model,
                "instructions": build_instructions(self.p, mode),
                "output_modalities": ["text"],  # exactly one of "text" or "audio"
            },
        })
        while True:
            evt = await self._recv()
            t = evt.get("type")
            if t in ("session.created", "session.updated"):
                return
            if t == "error":
                raise AgentError(f"session.update rejected: {evt}")

    async def _collect_text(self) -> str:
        parts: list[str] = []
        while True:
            evt = await self._recv()
            t = evt.get("type", "")
            if t == "response.output_text.delta":
                parts.append(evt.get("delta", ""))
            elif t == "response.done":
                status = (evt.get("response") or {}).get("status")
                if status in ("failed", "cancelled", "incomplete") and not parts:
                    raise AgentError(f"response {status}: {evt}")
                return "".join(parts).strip()
            elif t == "error":
                raise AgentError(f"server error: {evt}")
            elif t in ("session.idle_timeout", "session.max_duration_reached"):
                raise AgentError(f"session ended: {t}")
