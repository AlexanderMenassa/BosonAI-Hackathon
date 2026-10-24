"""Does Higgs follow instruction changes mid-conversation? Run this when switching HIGGS_MODEL.

    python -m hangon.probe            # both methods
    python -m hangon.probe --debug    # also print every event

Tells Higgs to answer with one word (APPLE), then switches it to BANANA, then CHERRY, two ways:
* response: per-reply instructions on response.create. The agent depends on this one: it must PASS.
* session:  session.update before the reply. Shown for comparison (it lands late on the models tried so far).
A method passes only if Higgs follows BOTH switches. Costs a few seconds of text.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys

from .config import Settings

WORDS = ("APPLE", "BANANA", "CHERRY")


def rule(word: str) -> str:
    return f"Whatever the user says, reply with exactly one word and nothing else: {word}"


class Raw:
    def __init__(self, ws, debug: bool, model: str = "higgs-realtime"):
        self.ws, self.debug, self.model = ws, debug, model

    async def send(self, evt: dict) -> None:
        if self.debug:
            print(f"      -> {evt['type']}")
        await self.ws.send(json.dumps(evt))

    async def recv(self) -> dict:
        evt = json.loads(await asyncio.wait_for(self.ws.recv(), timeout=30))
        if self.debug and not evt.get("type", "").endswith(".delta"):
            print(f"      <- {evt.get('type')}")
        return evt

    async def configure(self, instructions: str) -> None:
        await self.send({"type": "session.update", "session": {
            "model": self.model, "instructions": instructions, "output_modalities": ["text"]}})
        while True:
            evt = await self.recv()
            if evt.get("type") in ("session.created", "session.updated"):
                return
            if evt.get("type") == "error":
                raise RuntimeError(f"session.update rejected: {evt}")

    async def user(self, text: str) -> None:
        await self.send({"type": "conversation.item.create", "item": {
            "type": "message", "role": "user", "content": [{"type": "input_text", "text": text}]}})

    async def reply(self, response: dict | None = None) -> str:
        evt = {"type": "response.create"}
        if response:
            evt["response"] = response
        await self.send(evt)
        parts = []
        while True:
            e = await self.recv()
            t = e.get("type")
            if t == "response.output_text.delta":
                parts.append(e.get("delta", ""))
            elif t == "response.done":
                return "".join(parts).strip()
            elif t == "error":
                raise RuntimeError(f"server error: {e}")


async def try_method(method: str, s: Settings, debug: bool) -> tuple[bool, list[str], str]:
    from websockets.asyncio.client import connect
    replies: list[str] = []
    try:
        async with connect(s.boson_url, additional_headers={"Authorization": f"Bearer {s.boson_api_key}"}) as ws:
            r = Raw(ws, debug, s.higgs_model)
            await r.configure(rule(WORDS[0]))
            await r.user("Hello, who is this?")
            replies.append(await r.reply())
            for i, word in enumerate(WORDS[1:], start=2):
                if method == "session":
                    await r.configure(rule(word))
                    await r.user(f"Message number {i}, please answer.")
                    replies.append(await r.reply())
                else:  # response
                    await r.user(f"Message number {i}, please answer.")
                    replies.append(await r.reply({"instructions": rule(word)}))
    except Exception as e:
        return False, replies, f"{type(e).__name__}: {e}"
    ok = len(replies) == 3 and all(w in rep.upper() for w, rep in zip(WORDS, replies))
    return ok, replies, ""


def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--debug", action="store_true")
    ap.add_argument("--method", choices=["response", "session"], action="append")
    args = ap.parse_args()
    s = Settings.from_env()
    if not s.boson_api_key:
        raise SystemExit("BOSON_API_KEY is not set (put it in .env)")
    print(f"model: {s.higgs_model}")

    results = {}
    for method in args.method or ["response", "session"]:
        print(f"\n--- {method} ---")
        ok, replies, err = asyncio.run(try_method(method, s, args.debug))
        for want, got in zip(WORDS, replies):
            print(f"  wanted {want:<7} got: {got!r}")
        if err:
            print(f"  error: {err}")
        print(f"  => {'PASS' if ok else 'FAIL'}")
        results[method] = ok

    print("\n=== summary:", ", ".join(f"{m}={'PASS' if ok else 'FAIL'}" for m, ok in results.items()))
    if "response" in results:
        print("The agent uses per-reply instructions: " +
              ("OK for this model." if results["response"] else "this model does NOT follow them. Don't use it."))


if __name__ == "__main__":
    main()
