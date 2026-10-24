"""GeminiClassifier against a local stub that answers in the Gemini generateContent format."""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from hangon.classifier import GeminiClassifier
from hangon.models import Label


class Stub(BaseHTTPRequestHandler):
    status = 200
    reply: dict = {}
    seen: list = []

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        Stub.seen.append((self.path, self.headers.get("x-goog-api-key"), body))
        out = json.dumps(Stub.reply).encode()
        self.send_response(Stub.status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):
        pass


@pytest.fixture
def gemini():
    Stub.status, Stub.seen = 200, []
    srv = HTTPServer(("127.0.0.1", 0), Stub)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield GeminiClassifier("test-key", "gemini-test", base_url=f"http://127.0.0.1:{srv.server_port}/v1beta")
    srv.shutdown()


def answer(d: dict) -> dict:
    return {"candidates": [{"content": {"role": "model", "parts": [{"text": json.dumps(d)}]}}]}


def test_request_shape_and_verdict(gemini):
    Stub.reply = answer({"behaviours": [{"type": "unusual_payment", "quote": "pay now with coupons"},
                                        {"type": "impersonates_authority", "quote": "This is Amazon"}],
                         "ordinary_purpose_quote": None, "caller_name": None, "purpose": "an unpaid shipment",
                         "claimed_org": "Amazon", "payment_method": "coupons", "callback_number": None,
                         "urgency_tactic": "pay now", "high_stakes": True, "caller_summary": "Claimed to be Amazon"})
    r = gemini.classify([("caller", "This is Amazon. You must pay now with coupons.")])
    assert r.label is Label.SCAM and r.source == "gemini:gemini-test" and r.purpose == "an unpaid shipment"
    assert [t for t, _ in r.behaviours] == ["unusual_payment", "impersonates_authority"]
    assert "unusual payment" in r.reason and "pay now with coupons" in r.reason
    assert r.evidence.claimed_org == "Amazon" and r.evidence.payment_method == "coupons"
    path, key, body = Stub.seen[0]
    assert path == "/v1beta/models/gemini-test:generateContent" and key == "test-key"
    assert body["generationConfig"]["responseMimeType"] == "application/json"
    assert "report what the CALLER has said" in body["systemInstruction"]["parts"][0]["text"]
    assert "<transcript>" in body["contents"][0]["parts"][0]["text"]


def test_invented_evidence_is_dropped_like_for_any_llm(gemini):
    Stub.reply = answer({"behaviours": [], "ordinary_purpose_quote": None, "claimed_org": "Amazon",
                         "payment_method": "gift cards", "callback_number": "647-555-0199",
                         "urgency_tactic": None, "high_stakes": True, "caller_summary": "s"})
    r = gemini.classify([("caller", "Hi, this is Amazon calling.")])
    assert r.evidence.payment_method is None and r.evidence.callback_number is None


def test_api_error_falls_back_and_says_why(gemini, caplog):
    Stub.status, Stub.reply = 404, {"error": {"message": "models/gemini-test is not found"}}
    r = gemini.classify([("caller", "Hello?")])
    assert "fallback" in r.source
    assert "is not found" in caplog.text           # the server's own message reaches the log


def test_blocked_prompt_falls_back(gemini):
    Stub.reply = {"promptFeedback": {"blockReason": "SAFETY"}}
    assert "fallback" in gemini.classify([("caller", "Hello?")]).source


def test_missing_key_is_a_clear_error():
    with pytest.raises(ValueError, match="GEMINI_API_KEY"):
        GeminiClassifier("")


def test_a_quote_the_caller_never_said_is_dropped_so_no_scam_is_invented(gemini, caplog):
    """The spill problem: a small model mixed a prompt example into its report. Quotes are checked against the caller."""
    Stub.reply = answer({"behaviours": [{"type": "threat", "quote": "you will be arrested today"},
                                        {"type": "unusual_payment", "quote": "pay with gift cards"}],
                         "ordinary_purpose_quote": None, "claimed_org": "Canada Revenue Agency",
                         "payment_method": None, "callback_number": None, "urgency_tactic": None,
                         "high_stakes": True, "caller_summary": "s"})
    r = gemini.classify([("caller", "Hello, this is Officer Davis from the Canada Revenue Agency.")])
    assert r.behaviours == [] and r.label is Label.UNSURE and r.scam_score == 0.50
    assert "judge dropped threat" in caplog.text and "you will be arrested today" in caplog.text   # visible while tuning


def test_the_screener_is_labelled_assistant_so_its_words_are_not_taken_for_the_callers(gemini):
    Stub.reply = answer({"behaviours": []})
    gemini.classify([("agent", "Hello? Who's calling?"), ("caller", "Hi there.")])
    sent = Stub.seen[0][2]["contents"][0]["parts"][0]["text"]
    assert "assistant: Hello? Who's calling?" in sent and "caller: Hi there." in sent and "agent:" not in sent
