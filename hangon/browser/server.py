"""Private two-browser gateway with optional live WebRTC audio."""
from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import ssl
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from websockets.asyncio.server import ServerConnection, serve
from websockets.datastructures import Headers
from websockets.exceptions import ConnectionClosed
from websockets.http11 import Response

from ..classifier import Classifier, HeuristicClassifier
from ..config import Settings
from ..gateway import Gateway, GatewayError
from .media import MediaBridge, screening_error_reason
from ..models import Action, ClassifierResult, ConnectionState as State, FailureReason, Label, Mode
from ..persona import known_facts, turn_guidance
from ..sim import make_classifier
from ..store import Store

WEB = Path(__file__).resolve().parents[2] / "web"

def demo_number(raw: object) -> str:
    """Canonical North American test number; no telephone-network call is made."""
    if not isinstance(raw, str) or not re.fullmatch(r"[+()0-9 .-]{10,32}", raw):
        raise GatewayError("enter a valid test phone number")
    digits = re.sub(r"[^0-9]", "", raw)
    if len(digits) == 10:
        digits = "1" + digits
    if len(digits) != 11 or not digits.startswith("1"):
        raise GatewayError("enter a 10-digit North American test number")
    return "+" + digits


def ice_configuration() -> list[dict]:
    servers = []
    stun = os.environ.get("HANGON_STUN_URL", "")
    if stun:
        servers.append({"urls": stun})
    turn, secret = os.environ.get("HANGON_TURN_URL", ""), os.environ.get("HANGON_TURN_SECRET", "")
    if bool(turn) != bool(secret):
        raise ValueError("HANGON_TURN_URL and HANGON_TURN_SECRET must be set together")
    if turn and secret:
        username = f"{int(time.time()) + 3600}:hangon"
        credential = base64.b64encode(hmac.new(secret.encode(), username.encode(), hashlib.sha1).digest()).decode()
        servers.append({"urls": turn, "username": username, "credential": credential})
    return servers


class ControlTransport:
    """Records control transitions; it never reports audio readiness."""

    def __init__(self):
        self.ops: list[tuple[str, str]] = []

    def start(self, call_id: str) -> None:
        self.ops.append(("start", call_id))

    def pause(self, call_id: str) -> None:
        self.ops.append(("pause", call_id))

    def detach(self, call_id: str) -> None:
        self.ops.append(("detach", call_id))

    def connect_ready(self, call_id: str) -> bool:
        self.ops.append(("connect_ready", call_id))
        return False

    def close(self, call_id: str) -> None:
        self.ops.append(("close", call_id))


class LiveReceiverTransport(ControlTransport):
    def __init__(self, room):
        super().__init__()
        self.room = room

    def connect_ready(self, call_id: str) -> bool:
        self.ops.append(("connect_ready", call_id))
        return self.room.media is not None and self.room.media.call_id == call_id and self.room.media.route == "human"


class SafeBrowserClassifier:
    """A provider failure must never approve or block a browser caller."""

    def __init__(self, delegate: Classifier):
        self.delegate = delegate

    def classify(self, transcript):
        try:
            result = self.delegate.classify(transcript)
            if "fallback" not in result.source:
                return result
            match = re.search(r"fallback after: ([A-Za-z][A-Za-z0-9_]*)", result.source)
            failure = match.group(1) if match else "fallback"
        except Exception as exc:
            failure = type(exc).__name__
        return ClassifierResult(Label.UNSURE, 0.5, f"classifier unavailable ({failure})",
                                source="unavailable")


@dataclass
class Participant:
    role: str
    token: str
    socket: ServerConnection | None
    ready: bool = False


class Room:
    def __init__(self, code: str, store: Store, ring_timeout: float = 30.0,
                 connect_timeout: float = 30.0, live_media: bool = False,
                 boson_key: str = "", boson_url: str = "", ice_servers: list[dict] | None = None,
                 classifier: Classifier | None = None, call_log_dir: Path | None = None):
        self.code = code
        self.store = store
        self.lock = asyncio.Lock()
        self.caller: Participant | None = None
        self.receiver: Participant | None = None
        self.screening = ControlTransport()
        self.receiver_transport = LiveReceiverTransport(self) if live_media else ControlTransport()
        self.media = (MediaBridge(self._on_turn, self._on_caption, self._on_media_failure,
                                  boson_key, boson_url, ice_servers, self._on_interrupt) if live_media else None)
        self.media_started = 0.0
        self.assistant_caption = ""
        self.screening_issue = ""
        self.asked: set[str] = set()
        self.known: dict[str, str] = {}
        self.call_log_dir = call_log_dir
        self.call_log_path: Path | None = None
        self.logged_outcome = False
        self.gateway = Gateway(store, SafeBrowserClassifier(classifier or HeuristicClassifier()), self.screening,
                               self.receiver_transport, receiver_available=self.receiver_available,
                               ring_timeout=ring_timeout, connect_timeout=connect_timeout)

    def receiver_available(self) -> bool:
        return bool(self.receiver and self.receiver.socket and self.receiver.ready and
                    self.gateway.state not in (State.CONNECTING, State.CONNECTED))

    def bind(self, role: str, socket: ServerConnection, token: str | None = None) -> Participant:
        if role not in ("caller", "receiver"):
            raise GatewayError("invalid room role")
        current = getattr(self, role)
        if current:
            if token != current.token or current.socket is not None:
                raise GatewayError("role occupied or invalid resume token")
            current.socket = socket
            return current
        if token:
            raise GatewayError("resume token not found")
        participant = Participant(role, secrets.token_urlsafe(24), socket, ready=role == "receiver")
        setattr(self, role, participant)
        return participant

    def snapshot(self, role: str) -> dict:
        g = self.gateway
        return {
            "type": "room.state", "room": self.code, "role": role,
            "receiver_online": bool(self.receiver and self.receiver.socket),
            "receiver_ready": bool(self.receiver and self.receiver.socket and self.receiver.ready),
            "receiver_busy": g.state in (State.RINGING, State.CONNECTING, State.CONNECTED),
            "call_id": g.incoming.call_id if g.incoming else None,
            "caller_number": g.incoming.number if g.incoming else None,
            "route": g.route.value if g.route else None,
            "state": g.state.value, "revision": g.revision,
            "summary": g.session.caller_summary if g.session else "",
            "classifier_source": g.session.last_result.source if g.session and g.session.last_result else "",
            "verdict": g.session.verdict.value if g.session else "unsure",
            "assistant_caption": self.assistant_caption if g.state is State.SCREENING else "",
            "screening_issue": self.screening_issue if g.state is State.MESSAGE_PENDING else "",
            "events": [vars(event) for event in g.events[-12:]],
            "audio": ("idle" if self.media and g.state is State.ENDED else
                      "stopped" if self.media and g.state is State.MESSAGE_PENDING else
                      "human" if self.media and self.media.route == "human" else
                      "screening" if self.media and self.media.screen and self.media.screen.active else
                      "connecting" if self.media else "unavailable: Phase 2A control demo"),
            "synthetic_mode": self.media is None,
            "ice_servers": self.media_ice_servers if hasattr(self, "media_ice_servers") else [],
            "relay_only": os.environ.get("HANGON_RELAY_ONLY") == "1",
            "trusted_numbers": self.store.trusted_list() if role == "receiver" else [],
            "blocked_numbers": self.store.blocklist() if role == "receiver" else [],
        }

    async def _on_turn(self, line: str) -> None:
        async with self.lock:
            g = self.gateway
            if not g.incoming or g.state is not State.SCREENING:
                return
            call_id = g.incoming.call_id
            self._log(f"caller> {line}")
            started = time.monotonic()
            await asyncio.to_thread(g.caller_turn, line)
            decision = g.last_decision
            if g.session and g.session.last_result:
                result = g.session.last_result
                self._log(f"  [{g.session.status_line()}] {result.reason} "
                          f"({time.monotonic() - started:.1f}s; classifier={result.source})")
            if decision and decision.action in (Action.END, Action.FORWARD):
                self._log(f"[{decision.action.value}] {decision.note}")
            if self.media and self.media.call_id == call_id and self.media.screen:
                if decision and decision.mode is not None:
                    session = g.session
                    heard = [text for who, text in session.transcript if who == "caller"]
                    for fact, value in known_facts(session.last_result, session.evidence, heard).items():
                        self.known.setdefault(fact, value)
                    guidance = turn_guidance(decision.mode, self.known, self.asked)
                    await self.media.screen.speak(decision.mode, guidance, line, heard[:-1])
                    if decision.action in (Action.END, Action.FORWARD):
                        await self.media.screen.finish_reply()
                if g.state is State.RINGING:
                    self.media.pause_screen()
                elif g.state in (State.ENDED, State.MESSAGE_PENDING):
                    await self._close_media()
            await self.broadcast()

    async def _on_caption(self, line: str) -> None:
        if self.gateway.state is State.SCREENING:
            # Record a completed caption before the next caller turn is classified.
            self.assistant_caption = line
            self.gateway.session.record_agent(line)
            self._log(f"agent> {line}")
            await self.broadcast()

    def _log(self, entry: str) -> None:
        if not self.call_log_path:
            return
        try:
            with self.call_log_path.open("a", encoding="utf-8") as file:
                file.write(" ".join(entry.split())[:2000] + "\n")
        except OSError:
            pass  # Logging must not change call routing.

    def _start_log(self, call_id: str) -> None:
        self.call_log_path = None
        if not self.call_log_dir:
            return
        try:
            self.call_log_dir.mkdir(parents=True, exist_ok=True)
            path = self.call_log_dir / f"browser-{call_id}.log"
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(fd)
            self.call_log_path = path
            print(f"Browser call log: {path}", flush=True)
        except OSError:
            pass  # A log path failure must not end a call.

    async def _on_interrupt(self) -> None:
        if self.caller and self.caller.socket and self.gateway.state is State.SCREENING:
            self.assistant_caption = ""
            await self.caller.socket.send(json.dumps({"type": "media.clear", "reason": "interruption",
                                                      "call_id": self.gateway.incoming.call_id}))
            await self.broadcast()

    async def _on_media_failure(self, reason: str) -> None:
        async with self.lock:
            g = self.gateway
            if g.incoming and g.state not in (State.ENDED, State.MESSAGE_PENDING):
                self.screening_issue = reason if reason.startswith("screening_") else ""
                g.fail(g.incoming.call_id, FailureReason.MEDIA_UNAVAILABLE if reason == "media_unavailable"
                       else FailureReason.SCREENING_UNAVAILABLE)
                await self._close_media()
                await self.broadcast()

    async def _close_media(self) -> None:
        if self.media:
            await self.media.close()

    async def broadcast(self) -> None:
        if (self.gateway.state is State.ENDED and self.gateway.session and
                self.gateway.session.outcome and not self.logged_outcome):
            blocked = self.store.is_blocked(self.gateway.incoming.number)
            self._log(f"=== call over: outcome={self.gateway.session.outcome.value}, "
                      f"number {'BLOCKED' if blocked else 'not blocked'} ===")
            self.logged_outcome = True
        for participant in (self.caller, self.receiver):
            if participant and participant.socket:
                try:
                    await participant.socket.send(json.dumps(self.snapshot(participant.role)))
                except ConnectionClosed:
                    pass

    async def command(self, participant: Participant, socket: ServerConnection, data: dict) -> None:
        async with self.lock:
            if getattr(self, participant.role) is not participant or participant.socket is not socket:
                raise GatewayError("stale room session")
            if data.get("token") != participant.token:
                raise GatewayError("invalid session token")
            kind = data.get("type")
            g = self.gateway
            if kind == "receiver.ready" and participant.role == "receiver":
                if type(data.get("ready")) is not bool:
                    raise GatewayError("ready must be true or false")
                participant.ready = bool(data.get("ready"))
                if not participant.ready and g.state is State.RINGING:
                    g.fail(g.incoming.call_id, FailureReason.RECEIVER_UNAVAILABLE)
            elif kind in ("list.add", "list.remove") and participant.role == "receiver":
                number = demo_number(data.get("number"))
                list_name = data.get("list")
                if list_name not in ("trusted", "blocked"):
                    raise GatewayError("choose the trust or block list")
                if kind == "list.add":
                    label = data.get("label", "")
                    if not isinstance(label, str) or len(label.strip()) > 40:
                        raise GatewayError("contact name must be at most 40 characters")
                    if list_name == "trusted":
                        self.store.add_trusted(number, label.strip())
                    else:
                        self.store.block(number, label.strip())
                elif list_name == "trusted":
                    self.store.remove_trusted(number)
                else:
                    self.store.unblock(number)
            elif kind == "call.start" and participant.role == "caller":
                number = demo_number(data.get("number"))
                request_id = data.get("request_id")
                if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
                    raise GatewayError("invalid request_id")
                g.arrive(request_id, number)
                self.assistant_caption = ""
                self.screening_issue = ""
                self.asked.clear()
                self.known.clear()
                self._start_log(g.incoming.call_id)
                self.logged_outcome = False
                self._log(f"incoming call from {number}")
                if self.media and g.state is not State.ENDED:
                    self.media_started = time.monotonic()
            elif kind == "media.offer" and self.media:
                call_id = data.get("call_id")
                if not g.incoming or call_id != g.incoming.call_id or g.state is State.ENDED:
                    raise GatewayError("wrong or ended call_id")
                if participant.role == "receiver" and g.state not in (State.RINGING, State.CONNECTING):
                    raise GatewayError("receiver media is not permitted yet")
                if participant.role == "caller" and g.state not in (State.SCREENING, State.RINGING, State.CONNECTING):
                    raise GatewayError("caller media is not permitted now")
                if participant.role in self.media.peers:
                    raise GatewayError("media offer already active for this role")
                sdp = data.get("sdp")
                if not isinstance(sdp, str):
                    raise GatewayError("invalid media offer")
                try:
                    answer = await self.media.offer(participant.role, call_id, sdp)
                except Exception as exc:
                    g.fail(call_id, FailureReason.MEDIA_UNAVAILABLE)
                    await self._close_media()
                    await self.broadcast()
                    raise GatewayError("media setup failed; end and retry") from exc
                await socket.send(json.dumps({"type": "media.answer", "call_id": call_id, "sdp": answer}))
                if participant.role == "caller" and g.state is State.SCREENING and not self.media.screen:
                    try:
                        await self.media.start_screen(call_id)
                    except Exception as exc:
                        self.screening_issue = screening_error_reason(exc)
                        g.fail(call_id, FailureReason.SCREENING_UNAVAILABLE)
                        await self._close_media()
            elif kind == "call.line" and participant.role == "caller":
                if self.media:
                    raise GatewayError("synthetic lines are unavailable in live audio mode")
                if data.get("call_id") != (g.incoming.call_id if g.incoming else None):
                    raise GatewayError("wrong call_id")
                line = data.get("text")
                if not isinstance(line, str) or not 1 <= len(line.strip()) <= 2000:
                    raise GatewayError("invalid caller line")
                g.caller_turn(line)
            elif kind == "call.message" and participant.role == "caller":
                message = data.get("text")
                if not isinstance(message, str) or not 1 <= len(message.strip()) <= 2000:
                    raise GatewayError("invalid message")
                g.record_message(str(data.get("call_id", "")), str(data.get("event_id", "")),
                                 data.get("expected_revision"), message)
            elif kind in ("call.accept", "call.decline", "call.end"):
                if not isinstance(data.get("event_id"), str) or not 1 <= len(data["event_id"]) <= 128:
                    raise GatewayError("invalid event_id")
                g.command(participant.role, kind, str(data.get("call_id", "")),
                          str(data.get("event_id", "")), data.get("expected_revision"))
                if kind == "call.end":
                    self._log(f"{participant.role} hung up")
                if kind == "call.accept" and self.media and g.state is State.CONNECTING and self.caller and self.caller.socket:
                    self.media.pause_screen()
                    await self.caller.socket.send(json.dumps({"type": "media.clear", "reason": "handoff",
                                                              "call_id": g.incoming.call_id}))
            else:
                raise GatewayError("unauthorized or unknown command")
            if self.media and g.state in (State.ENDED, State.MESSAGE_PENDING):
                await self._close_media()
            await self.broadcast()

    async def disconnect(self, participant: Participant, socket: ServerConnection) -> None:
        async with self.lock:
            if participant.socket is not socket:
                return
            participant.socket = None
            g = self.gateway
            if g.incoming and g.state is not State.ENDED:
                if participant.role == "caller":
                    self._log("caller disconnected")
                    g.command("caller", "call.end", g.incoming.call_id,
                              secrets.token_urlsafe(12), g.revision)
                elif g.state is State.RINGING:
                    self._log("receiver disconnected while ringing")
                    g.fail(g.incoming.call_id, FailureReason.RECEIVER_UNAVAILABLE)
                elif g.state in (State.CONNECTING, State.CONNECTED):
                    self._log("receiver disconnected")
                    g.command("receiver", "call.end", g.incoming.call_id,
                              secrets.token_urlsafe(12), g.revision)
            if self.media and (participant.role == "caller" or g.state in
                               (State.CONNECTING, State.CONNECTED, State.MESSAGE_PENDING, State.ENDED)):
                await self._close_media()
            await self.broadcast()

    async def check_timeout(self) -> None:
        async with self.lock:
            if self.media and self.gateway.state is State.SCREENING and not self.media.screen and self.media_started and time.monotonic() - self.media_started > 10:
                self.gateway.fail(self.gateway.incoming.call_id, FailureReason.MEDIA_UNAVAILABLE)
                await self._close_media()
                await self.broadcast()
            if self.gateway.tick():
                await self.broadcast()
            if self.media and self.gateway.state is State.CONNECTING:
                call_id = self.gateway.incoming.call_id
                try:
                    if await self.media.human(call_id):
                        self.gateway.media_ready(call_id)
                        await self.broadcast()
                except Exception:
                    self.gateway.fail(call_id, FailureReason.MEDIA_UNAVAILABLE)
                    await self._close_media()
                    await self.broadcast()
            if self.media and self.gateway.state in (State.ENDED, State.MESSAGE_PENDING):
                await self._close_media()


class Hub:
    def __init__(self, store: Store, ring_timeout: float = 30.0,
                 connect_timeout: float = 30.0, live_media: bool = False,
                 boson_key: str = "", boson_url: str = "", ice_servers: list[dict] | None = None,
                 classifier_factory=None, call_log_dir: Path | None = None):
        self.store = store
        self.ring_timeout = ring_timeout
        self.connect_timeout = connect_timeout
        self.live_media, self.boson_key, self.boson_url = live_media, boson_key, boson_url
        self.ice_servers = ice_servers or []
        self.classifier_factory = classifier_factory or HeuristicClassifier
        self.call_log_dir = call_log_dir
        self.rooms: dict[str, Room] = {}
        self.lock = asyncio.Lock()

    async def connect(self, socket: ServerConnection, first: dict) -> tuple[Room, Participant]:
        async with self.lock:
            kind = first.get("type")
            if kind == "room.create":
                code = secrets.token_urlsafe(6)
                room = Room(code, self.store, self.ring_timeout, self.connect_timeout,
                            self.live_media, self.boson_key, self.boson_url, self.ice_servers,
                            self.classifier_factory(), self.call_log_dir)
                room.media_ice_servers = self.ice_servers
                self.rooms[code] = room
                role = "caller"
            elif kind == "room.join":
                code = first.get("room")
                room = self.rooms.get(code)
                role = first.get("role")
                if room is None:
                    raise GatewayError("room not found")
            else:
                raise GatewayError("create or join a room first")
            participant = room.bind(role, socket, first.get("token"))
            return room, participant

    async def socket_handler(self, socket: ServerConnection) -> None:
        room = participant = None
        try:
            first = json.loads(await socket.recv())
            if not isinstance(first, dict):
                raise GatewayError("message must be an object")
            room, participant = await self.connect(socket, first)
            await socket.send(json.dumps({"type": "room.joined", "room": room.code,
                                          "role": participant.role, "token": participant.token}))
            await room.broadcast()
            async for raw in socket:
                try:
                    data = json.loads(raw)
                    if not isinstance(data, dict):
                        raise GatewayError("message must be an object")
                    await room.command(participant, socket, data)
                except (GatewayError, ValueError, TypeError) as exc:
                    await socket.send(json.dumps({"type": "error", "message": str(exc)}))
        except (ConnectionClosed, json.JSONDecodeError, GatewayError) as exc:
            if not isinstance(exc, ConnectionClosed):
                try:
                    await socket.send(json.dumps({"type": "error", "message": str(exc)}))
                except ConnectionClosed:
                    pass
        finally:
            if room and participant:
                await room.disconnect(participant, socket)

    async def tick(self) -> None:
        while True:
            await asyncio.sleep(0.2)
            for room in list(self.rooms.values()):
                await room.check_timeout()


async def static_page(connection: ServerConnection, request):
    if request.path == "/ws":
        origin = request.headers.get("Origin")
        host = request.headers.get("Host")
        public_origin = os.environ.get("HANGON_PUBLIC_ORIGIN", "").rstrip("/")
        if origin and origin.split("://", 1)[-1] != host and origin != public_origin:
            return connection.respond(403, "Origin not allowed")
        return None
    files = {"/": ("index.html", "text/html; charset=utf-8"),
             "/call.js": ("call.js", "text/javascript; charset=utf-8"),
             "/style.css": ("style.css", "text/css; charset=utf-8")}
    item = files.get(request.path)
    if not item:
        return connection.respond(404, "Not found")
    filename, mime = item
    body = (WEB / filename).read_bytes()
    return Response(200, "OK", Headers([("Content-Type", mime),
                                        ("Content-Length", str(len(body))),
                                        ("Cache-Control", "no-store")]), body)


async def run(host: str, port: int, db_path: str, ring_timeout: float = 30.0,
              connect_timeout: float = 30.0, cert: str | None = None,
              key_file: str | None = None) -> None:
    store = Store(db_path)
    settings = Settings.from_env()
    classifier_name = os.environ.get("HANGON_CLASSIFIER", "gemini")
    if classifier_name not in ("heuristic", "ollama", "gemini"):
        raise ValueError("HANGON_CLASSIFIER must be heuristic, ollama, or gemini")
    if classifier_name == "gemini" and not settings.gemini_api_key:
        raise ValueError("GEMINI_API_KEY is required for the selected classifier")
    ice_servers = ice_configuration()
    hub = Hub(store, ring_timeout, connect_timeout, True,
              settings.boson_api_key, settings.boson_url, ice_servers,
              lambda: make_classifier(classifier_name, settings),
              WEB.parent / "artifacts" / "call-logs")
    tls = None
    if cert and key_file:
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.load_cert_chain(cert, key_file)
    async with serve(hub.socket_handler, host, port, process_request=static_page,
                     max_size=131072, compression=None, ssl=tls):
        async with asyncio.TaskGroup() as tasks:
            tasks.create_task(hub.tick())
            print(f"Browser audio demo: {'https' if tls else 'http'}://{host}:{port}")
            await asyncio.Future()


def main() -> None:
    parser = argparse.ArgumentParser(description="Phase 2B browser audio demo")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--db", help="synthetic demo SQLite path; default is temporary")
    parser.add_argument("--ring-timeout", type=float, default=30.0)
    parser.add_argument("--connect-timeout", type=float, default=30.0)
    parser.add_argument("--cert", help="TLS certificate for HTTPS/WSS")
    parser.add_argument("--key-file", help="TLS private key for HTTPS/WSS")
    args = parser.parse_args()
    if args.ring_timeout <= 0 or args.connect_timeout <= 0:
        parser.error("timeouts must be positive")
    if bool(args.cert) != bool(args.key_file):
        parser.error("--cert and --key-file must be provided together")
    if args.host not in ("127.0.0.1", "localhost", "::1") and not args.cert:
        parser.error("a non-local host requires --cert and --key-file for HTTPS/WSS")
    with tempfile.TemporaryDirectory(prefix="hangon-controls-") as directory:
        try:
            asyncio.run(run(args.host, args.port, args.db or str(Path(directory) / "demo.db"),
                            args.ring_timeout, args.connect_timeout, args.cert, args.key_file))
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
