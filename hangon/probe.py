"""Which way of changing instructions mid-conversation does Higgs actually follow?

    python -m hangon.probe            # all three methods
    python -m hangon.probe --debug    # also print every event

For each method: tell Higgs to answer with one word (APPLE), then switch it to BANANA, then to CHERRY,
using that method. A method passes only if Higgs follows BOTH switches. Costs a few seconds of text.
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
    def __init__(self, ws, debug: bool):
        self.ws, self.debug = ws, debug

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
            "model": "higgs-realtime", "instructions": instructions, "output_modalities": ["text"]}})
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
            r = Raw(ws, debug)
            await r.configure(rule(WORDS[0]))
            await r.user("Hello, who is this?")
            replies.append(await r.reply())
            for i, word in enumerate(WORDS[1:], start=2):
                if method == "session":
                    await r.configure(rule(word))
                    await r.user(f"Message number {i}, please answer.")
                    replies.append(await r.reply())
                elif method == "response":
                    await r.user(f"Message number {i}, please answer.")
                    replies.append(await r.reply({"instructions": rule(word)}))
                elif method == "system":
                    await r.user(f"Message number {i}, please answer.")
                    await r.send({"type": "conversation.item.create", "item": {
                        "type": "message", "role": "system",
                        "content": [{"type": "input_text", "text": "New instructions, replacing earlier ones: " + rule(word)}]}})
                    replies.append(await r.reply())
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
    ap.add_argument("--method", choices=["session", "response", "system"], action="append",
                    help="system is rejected by Higgs; kept to show it")
    args = ap.parse_args()
    s = Settings.from_env()
    if not s.boson_api_key:
        raise SystemExit("BOSON_API_KEY is not set (put it in .env)")

    results = {}
    for method in args.method or ["session", "response", "system"]:
        print(f"\n--- {method} ---")
        ok, replies, err = asyncio.run(try_method(method, s, args.debug))
        for want, got in zip(WORDS, replies):
            print(f"  wanted {want:<7} got: {got!r}")
        if err:
            print(f"  error: {err}")
        print(f"  => {'PASS' if ok else 'FAIL'}")
        results[method] = ok

    passing = [m for m, ok in results.items() if ok]
    print("\n=== summary:", ", ".join(f"{m}={'PASS' if ok else 'FAIL'}" for m, ok in results.items()))
    if passing:
        print(f"Put this in .env:  HIGGS_STEERING={passing[0]}")
    else:
        print("No method passed. Paste this output so we can look at the replies.")


if __name__ == "__main__":
    main()
