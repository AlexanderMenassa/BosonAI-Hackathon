"""Adaptive test calls, against a local stand-in for the Gemini API (no key, no network)."""
import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

import hangon.classifier as classifier_module
from hangon.agent import ScriptedAgent
from hangon.classifier import HeuristicClassifier
from hangon.config import Settings
from hangon.models import Outcome, Persona
from hangon.redteam import Brief, GeminiCaller, load_brief, meets_expectation, run_brief
from hangon.store import Store

ROOT = Path(__file__).resolve().parent.parent


class Stub(BaseHTTPRequestHandler):
    """Answers each request with the next queued (status, caller reply)."""
    queue: list = []
    seen: list = []

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        Stub.seen.append(body)
        status, reply = Stub.queue.pop(0) if Stub.queue else (200, {"say": "", "hang_up": True})
        if status == 200:
            out = {"candidates": [{"content": {"parts": [{"text": json.dumps(reply)}]}}]}
        else:
            out = {"error": {"code": status, "message": "Resource has been exhausted"}}
        data = json.dumps(out).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *_):
        pass


@pytest.fixture
def gemini_url():
    Stub.queue, Stub.seen = [], []
    srv = HTTPServer(("127.0.0.1", 0), Stub)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}/v1beta"
    srv.shutdown()


BRIEF = Brief("cra", "scam", "+15550001111", "You pretend to be from the CRA and demand gift cards.")


def caller(url, brief=BRIEF):
    return GeminiCaller(brief, "Margaret", "k", "gemini-test", base_url=url, retries=2)


# ---------------------------------------------------------------- briefs
def test_every_shipped_brief_loads_with_a_valid_expectation():
    briefs = [load_brief(p) for p in sorted((ROOT / "scenarios" / "briefs").glob("*.md"))]
    assert len(briefs) >= 8 and {b.expect for b in briefs} >= {"scam", "forward", "not_scam"}
    assert len({b.number for b in briefs}) == len(briefs)                # one caller ID each


def test_brief_header_parsing_and_errors(tmp_path):
    p = tmp_path / "x.md"
    p.write_text("expect: hold   # comment\n---\nYou are Lisa.\n", encoding="utf-8")
    b = load_brief(p)
    assert (b.name, b.expect, b.text) == ("x", "hold", "You are Lisa.") and b.number.startswith("+1555")
    p.write_text("expect: maybe\n---\nx", encoding="utf-8")
    with pytest.raises(ValueError, match="expect"):
        load_brief(p)
    p.write_text("expect: scam\nno separator", encoding="utf-8")
    with pytest.raises(ValueError, match="---"):
        load_brief(p)


def test_expectations():
    assert meets_expectation("scam", Outcome.SCAM_ENDED) and meets_expectation("scam", Outcome.SCAM_HANGUP)
    assert not meets_expectation("scam", Outcome.MESSAGE_TAKEN)
    assert meets_expectation("forward", Outcome.FORWARDED) and not meets_expectation("forward", Outcome.HELD_CALLBACK)
    assert meets_expectation("hold", Outcome.HELD_CALLBACK)
    assert meets_expectation("not_scam", Outcome.HELD_CALLBACK) and not meets_expectation("not_scam", Outcome.SCAM_ENDED)


# ---------------------------------------------------------------- the simulated caller
def test_the_caller_hears_margaret_and_keeps_the_whole_conversation(gemini_url):
    Stub.queue = [(200, {"say": "Hello, this is the CRA.", "hang_up": False}),
                  (200, {"say": "You owe taxes.", "hang_up": False})]
    c = caller(gemini_url)
    assert asyncio.run(c("Hello?")) == "Hello, this is the CRA."
    assert asyncio.run(c("Oh dear, what's this about?")) == "You owe taxes."
    first, second = Stub.seen
    assert "demand gift cards" in first["systemInstruction"]["parts"][0]["text"]       # the brief
    assert "never mention tests, AI" in first["systemInstruction"]["parts"][0]["text"]
    convo = second["contents"][0]["parts"][0]["text"]
    assert "Margaret: Hello?\nYou: Hello, this is the CRA.\nMargaret: Oh dear, what's this about?" in convo


def test_the_caller_says_its_last_line_then_hangs_up(gemini_url):
    Stub.queue = [(200, {"say": "Fine, goodbye.", "hang_up": True})]
    c = caller(gemini_url)
    assert asyncio.run(c("I'll call the CRA myself.")) == "Fine, goodbye."
    assert asyncio.run(c("Goodbye.")) is None and len(Stub.seen) == 1           # no more requests


def test_rate_limits_are_waited_out_not_turned_into_a_wrong_result(gemini_url, monkeypatch):
    monkeypatch.setattr(classifier_module, "RETRY_WAIT_SECONDS", 0)
    Stub.queue = [(429, None), (429, None), (200, {"say": "Hello?", "hang_up": False})]
    assert asyncio.run(caller(gemini_url)("Hello?")) == "Hello?" and len(Stub.seen) == 3


def test_retries_give_up_eventually(gemini_url, monkeypatch):
    monkeypatch.setattr(classifier_module, "RETRY_WAIT_SECONDS", 0)
    Stub.queue = [(429, None)] * 3
    with pytest.raises(RuntimeError, match="429"):
        asyncio.run(caller(gemini_url)("Hello?"))


# ---------------------------------------------------------------- whole runs, scored
def run(tmp_path, url, brief, caller_lines):
    Stub.queue = [(200, {"say": line, "hang_up": False}) for line in caller_lines]
    settings = Settings(reports_dir=str(tmp_path / "r"))
    store = Store(str(tmp_path / "t.db"))
    return asyncio.run(run_brief(brief, caller(url, brief), HeuristicClassifier(), ScriptedAgent(Persona()),
                                 store, settings, echo=False)), store


def test_a_scam_run_is_scored_pass_with_the_turn_it_was_flagged(tmp_path, gemini_url):
    result, store = run(tmp_path, gemini_url, BRIEF, [
        "Hello, this is Officer Price from the Canada Revenue Agency.",
        "You owe back taxes, and a warrant will be issued for your arrest within 24 hours.",
        "This line is never needed."])
    assert result.passed and result.outcome is Outcome.SCAM_ENDED
    assert result.first_scam_turn == 2 and result.max_score >= 0.9 and result.fallbacks == 0
    assert "threat" in result.reason and store.is_blocked(BRIEF.number)
    assert result.line().startswith("PASS  cra")


def test_an_honest_caller_run_is_scored(tmp_path, gemini_url):
    honest = Brief("pharmacy", "forward", "+15550002222", "You are Priya from Riverside Pharmacy.")
    result, _ = run(tmp_path, gemini_url, honest, [
        "Hi, this is Priya from Riverside Pharmacy.", "Margaret's prescription is ready for pickup."])
    assert result.passed and result.outcome is Outcome.FORWARDED and result.first_scam_turn is None


def test_a_wrong_result_is_a_fail(tmp_path, gemini_url):
    wrong = Brief("pharmacy", "scam", "+15550003333", "x")
    result, _ = run(tmp_path, gemini_url, wrong, ["Hi, this is Riverside Pharmacy.",
                                                    "The prescription is ready for pickup."])
    assert not result.passed and result.line().startswith("FAIL")


def test_rerunning_a_scam_brief_is_not_rejected_by_its_own_earlier_block(tmp_path, gemini_url):
    lines = ["This is the CRA.", "A warrant will be issued for your arrest."]
    run(tmp_path, gemini_url, BRIEF, lines)
    again, _ = run(tmp_path, gemini_url, BRIEF, lines)
    assert again.outcome is Outcome.SCAM_ENDED and again.passed
