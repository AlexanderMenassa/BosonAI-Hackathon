"""Adaptive test calls: Gemini plays the caller, following a written brief, and reacts to what Margaret says.

Scripted scenarios replay fixed lines whatever the agent answers, so they cannot show whether Margaret's questions
actually draw a scammer out. Here a second model plays the caller: it follows the brief's plan, answers her
questions in character, and escalates (or gives up) like a real caller would. Each run is scored against the
outcome the brief expects.

    python -m hangon.redteam scenarios/briefs/grandchild_bail.md
    python -m hangon.redteam --all                      # every brief, then a PASS/FAIL table
    python -m hangon.redteam --all --agent scripted     # skip Higgs (faster, no Boson credits)

A brief is a text file: a small header, a line with ---, then the caller's character and plan in plain English.

    expect: scam            # scam | forward | hold | not_scam
    number: +14165559001    # optional; the caller ID to use
    ---
    You are ...

Runs use their own database (redteam.db) and report folder (reports/redteam), so blocked test numbers never
touch your real blocklist. Both the caller and the judge use Gemini (GEMINI_API_KEY); free-tier rate limits are
handled by waiting and retrying, so a run can pause for a few seconds now and then.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import zlib
from dataclasses import dataclass, field
from pathlib import Path

from .agent import AgentError
from .classifier import GEMINI_URL, Classifier, GeminiClassifier, HeuristicClassifier, gemini_generate, parse_llm_json
from .config import Settings
from .models import ClassifierResult, Label, Outcome, Persona
from .notify import ConsoleNotifier
from .sim import make_agent, run_call
from .store import Store

BRIEFS_DIR = Path("scenarios/briefs")
EXPECTATIONS = ("scam", "forward", "hold", "not_scam")
_SCAM_OUTCOMES = (Outcome.SCAM_ENDED, Outcome.SCAM_HANGUP)

CALLER_SYSTEM = """You are an actor playing the CALLER in a realistic phone call. The call is used to test a \
scam-screening system that protects elderly people. The person who answers is {name}, an older person at home. \
Treat {name} as completely real. Stay in character for the whole call and never mention tests, AI, scripts or \
this brief.

Your character and plan:
{brief}

How to play it:
- Speak naturally, one to three short sentences per turn, like a real phone call. No stage directions, no emoji.
- Always respond to what {name} just said. Answer questions in character using the details in your brief, and \
invent plausible small details if asked for something the brief does not cover.
- Follow your plan at your own pace. Do not rush it all into one turn; move to the next step when it feels natural.
- When your plan is complete, or {name} clearly ends the call, or refuses firmly twice, say a short goodbye and \
hang up.

Reply with ONLY one JSON object: {{"say": "your next words", "hang_up": true if this is your last line}}"""


# --------------------------------------------------------------------------- briefs

@dataclass
class Brief:
    name: str
    expect: str
    number: str
    text: str


def load_brief(path: str | Path) -> Brief:
    path = Path(path)
    header, sep, body = path.read_text(encoding="utf-8").partition("\n---")
    if not sep:
        raise ValueError(f"{path}: missing the '---' line between the header and the brief")
    fields = {}
    for line in header.splitlines():
        if ":" in line and not line.strip().startswith("#"):
            key, value = line.split(":", 1)
            fields[key.strip().lower()] = value.split("#")[0].strip()
    expect = fields.get("expect", "")
    if expect not in EXPECTATIONS:
        raise ValueError(f"{path}: 'expect:' must be one of {', '.join(EXPECTATIONS)}")
    # A stable made-up number per brief, so repeated runs are comparable.
    number = fields.get("number") or f"+1555{zlib.crc32(path.stem.encode()) % 10**7:07d}"
    return Brief(path.stem, expect, number, body.strip())


def meets_expectation(expect: str, outcome: Outcome | None) -> bool:
    if expect == "scam":
        return outcome in _SCAM_OUTCOMES
    if expect == "forward":
        return outcome is Outcome.FORWARDED
    if expect == "hold":
        return outcome is Outcome.HELD_CALLBACK
    return outcome not in _SCAM_OUTCOMES  # not_scam: anything except a block


# --------------------------------------------------------------------------- the simulated caller

class GeminiCaller:
    """Plays the brief's caller. Called once per turn with what the agent just said; returns the caller's reply,
    or None once the caller has hung up."""

    def __init__(self, brief: Brief, persona_name: str, api_key: str, model: str, timeout: float = 30.0,
                 retries: int = 3, base_url: str = GEMINI_URL, pace: float = 0.0):
        if not api_key:
            raise ValueError("GEMINI_API_KEY is not set (put it in .env)")
        self.system = CALLER_SYSTEM.format(name=persona_name, brief=brief.text)
        self.persona_name, self.api_key, self.model = persona_name, api_key, model
        self.timeout, self.retries, self.base_url, self.pace = timeout, retries, base_url, pace
        self.history: list[tuple[str, str]] = []   # ("them" | "you", text)
        self.hung_up = False

    def prompt(self) -> str:
        lines = [f"{self.persona_name if who == 'them' else 'You'}: {text}" for who, text in self.history]
        return "The call so far:\n" + "\n".join(lines) + "\n\nWhat do you say next?"

    async def __call__(self, heard: str) -> str | None:
        if heard:
            self.history.append(("them", heard))
        if self.hung_up:
            return None
        if self.pace:
            await asyncio.sleep(self.pace)
        raw = await asyncio.to_thread(gemini_generate, self.api_key, self.model, self.system, self.prompt(),
                                      self.timeout, self.base_url, self.retries)
        reply = parse_llm_json(raw)
        say = str(reply.get("say") or "").strip()
        self.hung_up = bool(reply.get("hang_up")) or not say
        if not say:
            return None
        self.history.append(("you", say))
        return say


# --------------------------------------------------------------------------- running and scoring

class RecordingClassifier:
    """Wraps the judge to keep every per-turn result, for the run summary."""

    def __init__(self, inner: Classifier):
        self.inner = inner
        self.results: list[ClassifierResult] = []

    def classify(self, transcript):
        result = self.inner.classify(transcript)
        self.results.append(result)
        return result


@dataclass
class RunResult:
    brief: Brief
    outcome: Outcome | None
    turns: int
    first_scam_turn: int | None
    max_score: float
    fallbacks: int
    reason: str
    passed: bool = field(init=False)

    def __post_init__(self):
        self.passed = meets_expectation(self.brief.expect, self.outcome)

    def line(self) -> str:
        outcome = self.outcome.value if self.outcome else "-"
        flagged = f"turn {self.first_scam_turn}" if self.first_scam_turn else "never"
        warn = f"  ({self.fallbacks} judge fallbacks!)" if self.fallbacks else ""
        return (f"{'PASS' if self.passed else 'FAIL'}  {self.brief.name:<26} expect={self.brief.expect:<9} "
                f"got={outcome:<17} scam flagged: {flagged:<8} max score {self.max_score:.2f}  "
                f"turns {self.turns}{warn}")


async def run_brief(brief: Brief, caller, judge: Classifier, agent, store: Store, settings: Settings,
                    echo: bool = True) -> RunResult:
    store.unblock(brief.number)  # a previous run of this brief may have blocked its number
    recording = RecordingClassifier(judge)
    session = await run_call(brief.number, caller, recording, agent, store, settings,
                             ConsoleNotifier(settings.reports_dir, echo=echo), echo=echo, show_caller=True)
    first_scam = next((i + 1 for i, r in enumerate(recording.results)
                       if r.label is Label.SCAM and r.scam_score >= settings.scam_enter), None)
    return RunResult(
        brief=brief, outcome=session.outcome, turns=session.turns, first_scam_turn=first_scam,
        max_score=max((r.scam_score for r in recording.results), default=0.0),
        fallbacks=sum("fallback" in r.source for r in recording.results),
        reason=session.last_result.reason if session.last_result else "",
    )


def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("briefs", nargs="*", help="brief files to run")
    ap.add_argument("--all", action="store_true", help=f"run every brief in {BRIEFS_DIR}")
    ap.add_argument("--agent", choices=["higgs", "scripted"], default="higgs")
    ap.add_argument("--classifier", choices=["gemini", "heuristic"], default="gemini")
    ap.add_argument("--pace", type=float, default=2.0, help="seconds to wait before each caller turn (rate limits)")
    ap.add_argument("--db", default="redteam.db", help="test database (kept apart from your real blocklist)")
    args = ap.parse_args()

    paths = sorted(BRIEFS_DIR.glob("*.md")) if args.all else [Path(p) for p in args.briefs]
    if not paths:
        raise SystemExit("give one or more brief files, or --all")
    settings = Settings.from_env()
    settings.db_path, settings.reports_dir = args.db, str(Path(settings.reports_dir) / "redteam")
    caller_model = os.environ.get("GEMINI_CALLER_MODEL") or settings.gemini_model
    store = Store(settings.db_path)
    persona = Persona()

    results = []
    for path in paths:
        brief = load_brief(path)
        print(f"\n######## {brief.name}  (expect: {brief.expect})")
        try:
            caller = GeminiCaller(brief, persona.name, settings.gemini_api_key, caller_model, pace=args.pace)
            judge = (GeminiClassifier(settings.gemini_api_key, settings.gemini_model, settings.gemini_timeout,
                                      retries=3)
                     if args.classifier == "gemini" else HeuristicClassifier())
            agent = make_agent(args.agent, persona, settings)
        except (ValueError, AgentError) as e:
            raise SystemExit(str(e))
        result = asyncio.run(run_brief(brief, caller, judge, agent, store, settings))
        print(f"\nRESULT: {result.line()}\nREASON: {result.reason}")
        results.append(result)

    if len(results) > 1:
        print("\n======== summary")
        for r in results:
            print(r.line())
        print(f"\n{sum(r.passed for r in results)}/{len(results)} passed")


if __name__ == "__main__":
    main()
