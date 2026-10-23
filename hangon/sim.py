"""Call simulator. You (or a script) play the caller; the real decision loop runs.

    python -m hangon.sim --number +14165550100                       # type the caller's lines
    python -m hangon.sim --number +14165550100 --scenario scenarios/cra_scam.txt
    python -m hangon.sim --classifier ollama --agent higgs ...       # real local LLM + Higgs Realtime
    python -m hangon.sim --list-blocked | --unblock NUMBER | --trust NUMBER --label "Dr. Lee"

In interactive mode type /hangup (or press Ctrl-D) to hang up as the caller.
"""
from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
from typing import Awaitable, Callable

from .agent import Agent, AgentError, HiggsTextAgent, ScriptedAgent
from .classifier import Classifier, HeuristicClassifier, OllamaClassifier
from .config import Settings
from .engine import CallSession
from .models import Action, Mode, Persona
from .notify import ConsoleNotifier, Notifier
from .reports import notify_after_call
from .store import Store

NextLine = Callable[[], Awaitable["str | None"]]


def make_classifier(kind: str, s: Settings) -> Classifier:
    if kind == "heuristic":
        return HeuristicClassifier()
    if kind == "ollama":
        return OllamaClassifier(s.ollama_url, s.ollama_model, s.ollama_timeout)
    raise SystemExit(f"unknown classifier {kind!r}")


def make_agent(kind: str, persona: Persona, s: Settings) -> Agent:
    if kind == "scripted":
        return ScriptedAgent(persona)
    if kind == "higgs":
        return HiggsTextAgent(persona, s.boson_api_key, s.boson_url)
    raise SystemExit(f"unknown agent {kind!r}")


def scripted_lines(path: str) -> NextLine:
    lines = [l.strip() for l in Path(path).read_text().splitlines() if l.strip() and not l.startswith("#")]
    it = iter(lines)

    async def nxt() -> str | None:
        return next(it, None)
    return nxt


def typed_lines() -> NextLine:
    async def nxt() -> str | None:
        try:
            text = (await asyncio.to_thread(input, "caller> ")).strip()
        except EOFError:
            return None
        return None if text == "/hangup" else (text or await nxt())
    return nxt


async def run_call(number: str, next_line: NextLine, classifier: Classifier, agent: Agent, store: Store,
                   settings: Settings, notifier: Notifier, echo: bool = True,
                   show_caller: bool = False) -> CallSession:
    say = print if echo else (lambda *a, **k: None)
    session = CallSession(number, classifier, store, settings)

    d = session.open()
    say(f"\n=== incoming call from {number} ===")
    if d.action in (Action.REJECT, Action.FORWARD_TRUSTED):
        say(f"[{d.action.value}] {d.note}")
        notify_after_call(session, settings, notifier)
        return session

    await agent.start()
    try:
        line = await agent.say(Mode.GREET)
        session.record_agent(line)
        say(f"agent> {line}")
        while True:
            text = await next_line()
            if text is None:
                say("[caller hung up]")
                session.on_hangup()
                break
            if show_caller:  # scripted callers: echo their lines (a typed caller already sees them)
                say(f"caller> {text}")
            d = await asyncio.to_thread(session.on_caller_turn, text)
            say(f"  [{session.status_line()}]  {session.last_result.reason}")
            line = await agent.say(d.mode, text)
            session.record_agent(line)
            say(f"agent> {line}")
            if d.action in (Action.END, Action.FORWARD):
                say(f"[{d.action.value}] {d.note}")
                session.finish()
                break
    except AgentError as e:
        say(f"[agent error] {e}")
        session.finish()
    finally:
        await agent.close()

    say(f"\n=== call over: outcome={session.outcome.value}, number "
        f"{'BLOCKED' if store.is_blocked(number) else 'not blocked'} ===")
    notify_after_call(session, settings, notifier)
    return session


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--number", default="+14165550100", help="caller's phone number")
    ap.add_argument("--scenario", help="text file with one caller line per line")
    ap.add_argument("--classifier", choices=["heuristic", "ollama"], default="heuristic")
    ap.add_argument("--agent", choices=["scripted", "higgs"], default="scripted")
    ap.add_argument("--persona-name", default="Margaret")
    ap.add_argument("--db", help="sqlite path (default from HANGON_DB)")
    ap.add_argument("--list-blocked", action="store_true")
    ap.add_argument("--unblock", metavar="NUMBER")
    ap.add_argument("--trust", metavar="NUMBER")
    ap.add_argument("--label", default="")
    args = ap.parse_args()

    settings = Settings.from_env()
    if args.db:
        settings.db_path = args.db
    store = Store(settings.db_path)

    if args.list_blocked:
        for r in store.blocklist():
            print(f"{r['number']}  {r['reason']}")
        return
    if args.unblock:
        print("unblocked" if store.unblock(args.unblock) else "was not blocked")
        return
    if args.trust:
        store.add_trusted(args.trust, args.label)
        print(f"trusted: {args.trust}")
        return

    persona = Persona(name=args.persona_name)
    classifier = make_classifier(args.classifier, settings)
    agent = make_agent(args.agent, persona, settings)
    if args.scenario:
        next_line = scripted_lines(args.scenario)
    else:
        next_line = typed_lines()
    asyncio.run(run_call(args.number, next_line, classifier, agent, store, settings,
                         ConsoleNotifier(settings.reports_dir), show_caller=bool(args.scenario)))


if __name__ == "__main__":
    main()
