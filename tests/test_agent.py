"""HiggsTextAgent against a fake server that speaks the Higgs Realtime event protocol."""
import asyncio
import json

import pytest
from websockets.asyncio.server import serve

from hangon.agent import AgentError, HiggsTextAgent
from hangon.models import Mode, Persona


DISTINCT = ["Hello, who is calling please?", "Which organization are you with?",
            "How exactly would I pay that?", "Could I have your reference number?",
            "Goodness, the kettle is whistling, goodbye!"]


async def fake_higgs(received, ws, replies=None):
    """Answers each response.create with the next scripted reply (default: a new line every time)."""
    received.append(("auth", ws.request.headers.get("Authorization")))
    n = 0
    async for raw in ws:
        evt = json.loads(raw)
        received.append((evt["type"], evt))
        if evt["type"] == "session.update":
            first = sum(1 for t, _ in received if t == "session.update") == 1
            await ws.send(json.dumps({"type": "session.created" if first else "session.updated"}))
        elif evt["type"] == "response.create":
            text = replies[min(n, len(replies) - 1)] if replies else DISTINCT[n % len(DISTINCT)]
            n += 1
            await ws.send(json.dumps({"type": "response.created", "response": {"id": f"resp_{n}"}}))
            for chunk in (text[:10], text[10:]):
                await ws.send(json.dumps({"type": "response.output_text.delta", "delta": chunk}))
            await ws.send(json.dumps({"type": "response.output_text.done", "text": text}))
            await ws.send(json.dumps({"type": "response.done", "response": {"status": "completed"}}))


def converse(turns, server_replies=None):
    """turns: list of (mode, caller_text, guidance). Returns (received events, replies)."""
    async def go():
        received, replies = [], []
        async with serve(lambda ws: fake_higgs(received, ws, server_replies), "127.0.0.1", 0) as srv:
            port = srv.sockets[0].getsockname()[1]
            agent = HiggsTextAgent(Persona(name="Margaret"), "sk-test", url=f"ws://127.0.0.1:{port}")
            await agent.start()
            for mode, text, guidance in turns:
                replies.append(await agent.say(mode, text, guidance))
            await agent.close()
        return received, replies
    return asyncio.run(go())


CALL = [(Mode.GREET, None, ""),
        (Mode.SCREEN, "This is the CRA", "This turn: Ask which organization."),
        (Mode.STALL, "You owe taxes", "Your question this turn: Ask which tax year.")]


def events(received, kind):
    return [e for t, e in received if t == kind]


def test_role_and_guidance_ride_on_each_reply_and_the_session_holds_only_the_base_prompt():
    received, replies = converse(CALL)
    assert received[0] == ("auth", "Bearer sk-test") and replies == DISTINCT[:3]
    updates = events(received, "session.update")
    assert len(updates) == 1 and updates[0]["session"]["output_modalities"] == ["text"]
    assert "Say exactly" not in updates[0]["session"]["instructions"]
    creates = events(received, "response.create")
    assert "Who's calling?" in creates[0]["response"]["instructions"]
    assert "Ask which organization" in creates[1]["response"]["instructions"]
    assert "collect the details" in creates[2]["response"]["instructions"]
    assert "Ask which tax year" in creates[2]["response"]["instructions"]


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

    with pytest.raises(AgentError, match="rejected"):
        asyncio.run(go())


def test_server_closing_with_auth_code_gives_a_clear_message():
    async def server(ws):
        await ws.recv()
        await ws.close(3000, "invalid key")

    async def go():
        async with serve(server, "127.0.0.1", 0) as srv:
            agent = HiggsTextAgent(Persona(), "k", url=f"ws://127.0.0.1:{srv.sockets[0].getsockname()[1]}")
            await agent.start()
            try:
                await agent.say(Mode.GREET)
            finally:
                await agent.close()

    with pytest.raises(AgentError, match="API key was rejected"):
        asyncio.run(go())


def test_unreachable_server_is_an_agent_error_not_a_traceback():
    async def go():
        agent = HiggsTextAgent(Persona(), "k", url="ws://127.0.0.1:1")
        await agent.start()
    with pytest.raises(AgentError, match="could not connect"):
        asyncio.run(go())



def test_repeated_reply_is_retried_and_the_fresh_one_is_used():
    received, replies = converse(CALL, server_replies=[
        "Hello? This is Margaret's assistant.", "Which company is this?", "Which company is this?",
        "How exactly would I pay that?"])
    assert replies == ["Hello? This is Margaret's assistant.", "Which company is this?",
                       "How exactly would I pay that?"]
    creates = events(received, "response.create")
    assert len(creates) == 4 and "IMPORTANT" in creates[3]["response"]["instructions"]
    # the retry does not resend the caller's words
    assert len([e for e in events(received, "conversation.item.create")]) == 2


def test_a_reply_that_keeps_repeating_falls_back_to_a_scripted_line():
    same = "Could you please hold on while I find a pen?"
    turns = [(Mode.STALL, "a", "g1"), (Mode.STALL, "b", "g2"), (Mode.GOODBYE, "c", "g3")]
    _, replies = converse(turns, server_replies=[same])
    assert replies[0] == same
    assert replies[1] != same and replies[2] != same           # scripted fallbacks, not loops
    assert "door" in replies[2] or "Goodbye" in replies[2]      # the goodbye still happens


def test_near_identical_reply_counts_as_a_repeat():
    agent = HiggsTextAgent(Persona(), "k")
    agent._said = ["Oh, hello there! I'm so sorry, I just got to the phone."]
    assert agent._is_repeat("Oh hello there, I'm so sorry I just got to the phone!")
    assert not agent._is_repeat("Gift cards? Which shop sells those, dear?")
    assert agent._is_repeat("   ")


def test_text_from_the_done_event_is_used_when_no_deltas_arrive():
    async def server(ws):
        async for raw in ws:
            t = json.loads(raw)["type"]
            if t == "session.update":
                await ws.send(json.dumps({"type": "session.created"}))
            elif t == "response.create":
                await ws.send(json.dumps({"type": "response.output_text.done", "text": "Only in done."}))
                await ws.send(json.dumps({"type": "response.done", "response": {"status": "completed"}}))

    async def go():
        async with serve(server, "127.0.0.1", 0) as srv:
            agent = HiggsTextAgent(Persona(), "k", url=f"ws://127.0.0.1:{srv.sockets[0].getsockname()[1]}")
            await agent.start()
            try:
                return await agent.say(Mode.GREET)
            finally:
                await agent.close()
    assert asyncio.run(go()) == "Only in done."


def test_bad_config_is_a_clear_error():
    with pytest.raises(AgentError, match="BOSON_API_KEY"):
        HiggsTextAgent(Persona(), "")
