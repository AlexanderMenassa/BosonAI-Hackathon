"""HiggsTextAgent against a fake server that speaks the Higgs Realtime event protocol."""
import asyncio
import json

import pytest
from websockets.asyncio.server import serve

from hangon.agent import AgentError, HiggsTextAgent
from hangon.models import Mode, Persona


async def fake_higgs(received, ws):
    received.append(("auth", ws.request.headers.get("Authorization")))
    async for raw in ws:
        evt = json.loads(raw)
        received.append((evt["type"], evt))
        if evt["type"] == "session.update":
            first = sum(1 for t, _ in received if t == "session.update") == 1
            await ws.send(json.dumps({"type": "session.created" if first else "session.updated"}))
        elif evt["type"] == "response.create":
            for chunk in ("Oh, hello ", "dear."):
                await ws.send(json.dumps({"type": "response.output_text.delta", "delta": chunk}))
            await ws.send(json.dumps({"type": "response.done", "response": {"status": "completed"}}))


def run(coro):
    return asyncio.run(coro)


def test_greeting_then_a_stalling_turn():
    async def go():
        received = []
        async with serve(lambda ws: fake_higgs(received, ws), "127.0.0.1", 0) as srv:
            port = srv.sockets[0].getsockname()[1]
            agent = HiggsTextAgent(Persona(name="Margaret"), "sk-test", url=f"ws://127.0.0.1:{port}")
            await agent.start()
            g = await agent.say(Mode.GREET)
            s = await agent.say(Mode.STALL, "pay me in gift cards")
            await agent.close()
        return received, g, s

    received, g, s = run(go())
    assert received[0] == ("auth", "Bearer sk-test")
    assert g == "Oh, hello dear." and s == "Oh, hello dear."
    updates = [e for t, e in received if t == "session.update"]
    assert len(updates) == 2  # one per mode change
    assert updates[0]["session"]["output_modalities"] == ["text"]
    assert "Who's calling?" in updates[0]["session"]["instructions"]
    assert "AS Margaret" in updates[1]["session"]["instructions"]
    item = next(e for t, e in received if t == "conversation.item.create")
    assert item["item"]["content"][0] == {"type": "input_text", "text": "pay me in gift cards"}


def test_same_mode_does_not_resend_session_update():
    async def go():
        received = []
        async with serve(lambda ws: fake_higgs(received, ws), "127.0.0.1", 0) as srv:
            agent = HiggsTextAgent(Persona(), "k", url=f"ws://127.0.0.1:{srv.sockets[0].getsockname()[1]}")
            await agent.start()
            await agent.say(Mode.STALL, "one")
            await agent.say(Mode.STALL, "two")
            await agent.close()
        return received

    assert sum(1 for t, _ in run(go()) if t == "session.update") == 1


def test_server_error_event_raises_agent_error():
    async def server(ws):
        async for raw in ws:
            if json.loads(raw)["type"] == "session.update":
                await ws.send(json.dumps({"type": "error", "error": {"message": "bad model"}}))

    async def go():
        async with serve(server, "127.0.0.1", 0) as srv:
            agent = HiggsTextAgent(Persona(), "k", url=f"ws://127.0.0.1:{srv.sockets[0].getsockname()[1]}")
            await agent.start()
            try:
                await agent.say(Mode.GREET)
            finally:
                await agent.close()

    with pytest.raises(AgentError):
        run(go())


def test_missing_api_key_is_a_clear_error():
    with pytest.raises(AgentError, match="BOSON_API_KEY"):
        HiggsTextAgent(Persona(), "")
