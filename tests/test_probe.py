"""The probe must tell a steering method that works from one that is silently ignored."""
import asyncio
import json
import re

from websockets.asyncio.server import serve

from hangon.config import Settings
from hangon.probe import try_method


def word_in(text):
    m = re.search(r"exactly one word and nothing else: (\w+)", text or "")
    return m.group(1) if m else None


async def session_only_server(ws):
    """Follows session.update; ignores per-response instructions and system messages (like a stubborn model)."""
    current = None
    async for raw in ws:
        evt = json.loads(raw)
        if evt["type"] == "session.update":
            current = word_in(evt["session"]["instructions"])
            await ws.send(json.dumps({"type": "session.updated"}))
        elif evt["type"] == "response.create":
            await ws.send(json.dumps({"type": "response.output_text.delta", "delta": current}))
            await ws.send(json.dumps({"type": "response.done", "response": {"status": "completed"}}))


def run(method):
    async def go():
        async with serve(session_only_server, "127.0.0.1", 0) as srv:
            s = Settings(boson_api_key="k", boson_url=f"ws://127.0.0.1:{srv.sockets[0].getsockname()[1]}")
            return await try_method(method, s, debug=False)
    return asyncio.run(go())


def test_probe_passes_the_method_the_server_follows():
    ok, replies, err = run("session")
    assert ok and replies == ["APPLE", "BANANA", "CHERRY"] and not err


def test_probe_fails_methods_the_server_ignores():
    for method in ("response", "system"):
        ok, replies, _ = run(method)
        assert not ok and replies == ["APPLE", "APPLE", "APPLE"]
