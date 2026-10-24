import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from hangon.voices import create_voice, list_voices


class Stub(BaseHTTPRequestHandler):
    seen: list = []

    def _reply(self, payload):
        data = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        Stub.seen.append((self.path, self.headers.get("Authorization"),
                          json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
        self._reply({"voice_id": "voice_abc123", "title": "Margaret"})

    def do_GET(self):
        self._reply({"data": [{"voice_id": "voice_abc123", "title": "Margaret"}]})

    def log_message(self, *_):
        pass


@pytest.fixture
def api():
    Stub.seen = []
    srv = HTTPServer(("127.0.0.1", 0), Stub)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}/v1"
    srv.shutdown()


def test_create_sends_the_audio_as_base64_with_its_transcript(api, tmp_path):
    clip = tmp_path / "old_lady.mp3"
    clip.write_bytes(b"ID3fake-mp3-bytes")
    created = create_voice("bai-key", str(clip), "Oh hello dear.", "Margaret", api=api)
    assert created["voice_id"] == "voice_abc123"
    path, auth, body = Stub.seen[0]
    assert path == "/v1/audio/voices" and auth == "Bearer bai-key"
    assert base64.b64decode(body["ref_audio"]) == b"ID3fake-mp3-bytes"
    assert body["ref_text"] == "Oh hello dear." and body["title"] == "Margaret"


def test_list(api):
    assert list_voices("bai-key", api=api) == [{"voice_id": "voice_abc123", "title": "Margaret"}]
