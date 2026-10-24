"""Two independent WebSocket sessions exercise the Phase 2A control server."""
import asyncio
import json

from websockets.asyncio.client import connect
from websockets.asyncio.server import serve
from aiortc import RTCPeerConnection, RTCSessionDescription

from hangon.media import OutboundAudio
from hangon.server import Hub, demo_number, static_page
from hangon.store import Store

TRUSTED = "+14165550001"
BLOCKED = "+14165550002"
BOTH = "+14165550003"
UNKNOWN = "+14165550004"


async def receive_type(socket, kind):
    for _ in range(8):
        data = json.loads(await asyncio.wait_for(socket.recv(), 2))
        if data["type"] == kind:
            return data
    raise AssertionError(f"did not receive {kind}")


async def send(socket, token, **data):
    await socket.send(json.dumps({**data, "token": token}))


def test_demo_number_accepts_formatted_test_numbers():
    assert demo_number("(416) 555-0101") == "+14165550101"
    assert demo_number("+1 416 555 0101") == "+14165550101"
    import pytest
    from hangon.gateway import GatewayError
    with pytest.raises(GatewayError):
        demo_number("555")
    with pytest.raises(GatewayError):
        demo_number("4165550101;drop")


def test_receiver_lists_control_subsequent_call_routes(tmp_path):
    async def scenario():
        store = Store(str(tmp_path / "demo.db"))
        hub = Hub(store)
        async with serve(hub.socket_handler, "127.0.0.1", 0, process_request=static_page) as server:
            url = f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}/ws"
            async with connect(url) as caller, connect(url) as receiver:
                await caller.send(json.dumps({"type": "room.create"}))
                c = await receive_type(caller, "room.joined")
                await receive_type(caller, "room.state")
                await receiver.send(json.dumps({"type": "room.join", "room": c["room"], "role": "receiver"}))
                r = await receive_type(receiver, "room.joined")
                await receive_type(receiver, "room.state")
                await receive_type(caller, "room.state")
                await send(receiver, r["token"], type="receiver.ready", ready=True)
                await receive_type(receiver, "room.state")
                await receive_type(caller, "room.state")

                await send(caller, c["token"], type="list.add", list="trusted", number=TRUSTED)
                assert "unauthorized" in (await receive_type(caller, "error"))["message"]
                await send(receiver, r["token"], type="list.add", list="trusted",
                           number="(416) 555-0001", label="Pharmacy")
                state = await receive_type(receiver, "room.state")
                await receive_type(caller, "room.state")
                assert state["trusted_numbers"] == [{"number": TRUSTED, "label": "Pharmacy"}]
                await send(caller, c["token"], type="call.start", number=TRUSTED, request_id="listed")
                state = await receive_type(caller, "room.state")
                await receive_type(receiver, "room.state")
                assert state["route"] == "pass" and state["caller_number"] == TRUSTED
                assert state["trusted_numbers"] == []
                await send(caller, c["token"], type="call.end", call_id=state["call_id"],
                           event_id="end-listed", expected_revision=state["revision"])
                await receive_type(caller, "room.state")
                await receive_type(receiver, "room.state")

                await send(receiver, r["token"], type="list.add", list="blocked", number=TRUSTED)
                state = await receive_type(receiver, "room.state")
                await receive_type(caller, "room.state")
                assert state["blocked_numbers"][0]["number"] == TRUSTED
                await send(caller, c["token"], type="call.start", number=TRUSTED, request_id="blocked")
                state = await receive_type(caller, "room.state")
                await receive_type(receiver, "room.state")
                assert state["route"] == "reject" and state["state"] == "ended"

                await send(receiver, r["token"], type="list.remove", list="blocked", number=TRUSTED)
                await receive_type(receiver, "room.state")
                await receive_type(caller, "room.state")
                await send(receiver, r["token"], type="list.remove", list="trusted", number=TRUSTED)
                state = await receive_type(receiver, "room.state")
                await receive_type(caller, "room.state")
                assert state["trusted_numbers"] == [] and state["blocked_numbers"] == []
                await send(caller, c["token"], type="call.start", number=TRUSTED, request_id="unlisted")
                state = await receive_type(caller, "room.state")
                await receive_type(receiver, "room.state")
                assert state["route"] == "screen" and state["state"] == "screening"
    asyncio.run(scenario())


def test_two_browser_sessions_and_routes(tmp_path):
    async def scenario():
        store = Store(str(tmp_path / "demo.db"))
        store.add_trusted(TRUSTED)
        store.add_trusted(BOTH)
        store.block(BLOCKED)
        store.block(BOTH)
        hub = Hub(store)
        async with serve(hub.socket_handler, "127.0.0.1", 0, process_request=static_page) as server:
            url = f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}/ws"
            async with connect(url) as caller, connect(url) as receiver:
                await caller.send(json.dumps({"type": "room.create"}))
                joined_c = await receive_type(caller, "room.joined")
                await receive_type(caller, "room.state")
                code = joined_c["room"]
                await receiver.send(json.dumps({"type": "room.join", "room": code, "role": "receiver"}))
                joined_r = await receive_type(receiver, "room.joined")
                await receive_type(receiver, "room.state")
                await receive_type(caller, "room.state")
                await send(receiver, joined_r["token"], type="receiver.ready", ready=True)
                await receive_type(receiver, "room.state")
                await receive_type(caller, "room.state")

                await send(caller, joined_c["token"], type="call.start", number=TRUSTED, request_id="trusted-1")
                state = await receive_type(caller, "room.state")
                await receive_type(receiver, "room.state")
                assert state["route"] == "pass" and state["state"] == "ringing"
                room = hub.rooms[code]
                assert not any(op == "start" for op, _ in room.screening.ops)
                call_id = state["call_id"]
                await send(receiver, joined_r["token"], type="call.accept", call_id="wrong-room-call",
                           event_id="wrong-call", expected_revision=state["revision"])
                assert "wrong call_id" in (await receive_type(receiver, "error"))["message"]
                await send(caller, joined_c["token"], type="call.accept", call_id=call_id,
                           event_id="forged", expected_revision=state["revision"], role="receiver")
                assert (await receive_type(caller, "error"))["message"].startswith("unauthorized")
                await send(receiver, joined_r["token"], type="call.accept", call_id=call_id,
                           event_id="accept", expected_revision=state["revision"])
                state = await receive_type(receiver, "room.state")
                await receive_type(caller, "room.state")
                assert state["state"] == "connecting"
                assert not any(e["type"] == "call.connected" for e in state["events"])
                await send(caller, joined_c["token"], type="call.end", call_id=call_id,
                           event_id="end", expected_revision=state["revision"])
                await receive_type(caller, "room.state")
                await receive_type(receiver, "room.state")

                await send(caller, joined_c["token"], type="call.start", number=BLOCKED, request_id="blocked-1")
                state = await receive_type(caller, "room.state")
                await receive_type(receiver, "room.state")
                assert state["route"] == "reject" and state["state"] == "ended"
                assert not any(op == "start" and cid == state["call_id"] for op, cid in room.screening.ops)
                assert not any(op == "start" and cid == state["call_id"] for op, cid in room.receiver_transport.ops)

                await send(caller, joined_c["token"], type="call.start", number=UNKNOWN, request_id="unknown-1")
                state = await receive_type(caller, "room.state")
                await receive_type(receiver, "room.state")
                assert state["route"] == "screen" and state["state"] == "screening"
                assert any(op == "start" and cid == state["call_id"] for op, cid in room.screening.ops)
                assert not any(op == "start" and cid == state["call_id"] for op, cid in room.receiver_transport.ops)
                assert store.call_by_request("unknown-1")["public_call_id"] == state["call_id"]
                await send(caller, joined_c["token"], type="call.start", number=UNKNOWN, request_id="unknown-1")
                replay = await receive_type(caller, "room.state")
                await receive_type(receiver, "room.state")
                assert replay["call_id"] == state["call_id"]
                assert sum(op == "start" and cid == state["call_id"] for op, cid in room.screening.ops) == 1
                await send(caller, joined_c["token"], type="call.start", number=TRUSTED, request_id="busy-1")
                assert "busy" in (await receive_type(caller, "error"))["message"]
            async def finalized():
                while store.call_by_request("unknown-1")["outcome"] is None:
                    await asyncio.sleep(0)
            await asyncio.wait_for(finalized(), 2)
            assert store.call_by_request("unknown-1")["outcome"] == "hangup_unsure"
    asyncio.run(scenario())


def test_static_page_and_origin_restriction(tmp_path, monkeypatch):
    async def scenario():
        hub = Hub(Store(str(tmp_path / "demo.db")))
        async with serve(hub.socket_handler, "127.0.0.1", 0, process_request=static_page) as server:
            port = server.sockets[0].getsockname()[1]
            import urllib.request
            body = await asyncio.to_thread(lambda: urllib.request.urlopen(f"http://127.0.0.1:{port}/").read())
            assert b"Browser phone demo" in body
            stylesheet = await asyncio.to_thread(lambda: urllib.request.urlopen(f"http://127.0.0.1:{port}/style.css").read())
            assert b".phone" in stylesheet
            from websockets.exceptions import InvalidStatus
            try:
                async with connect(f"ws://127.0.0.1:{port}/ws", origin="https://attacker.example"):
                    raise AssertionError("foreign origin accepted")
            except InvalidStatus:
                pass
            monkeypatch.setenv("HANGON_PUBLIC_ORIGIN", "https://demo.example.ts.net")
            async with connect(f"ws://127.0.0.1:{port}/ws", origin="https://demo.example.ts.net"):
                pass
    asyncio.run(scenario())


def test_live_server_marks_trusted_call_connected_only_after_media(tmp_path):
    async def scenario():
        store = Store(str(tmp_path / "demo.db"))
        store.add_trusted(TRUSTED)
        hub = Hub(store, live_media=True)
        peers = []
        async with serve(hub.socket_handler, "127.0.0.1", 0, process_request=static_page) as server:
            url = f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}/ws"
            try:
                async with connect(url) as caller, connect(url) as receiver:
                    await caller.send(json.dumps({"type": "room.create"}))
                    c = await receive_type(caller, "room.joined")
                    await receive_type(caller, "room.state")
                    await receiver.send(json.dumps({"type": "room.join", "room": c["room"], "role": "receiver"}))
                    r = await receive_type(receiver, "room.joined")
                    await receive_type(receiver, "room.state")
                    await receive_type(caller, "room.state")
                    await send(receiver, r["token"], type="receiver.ready", ready=True)
                    await receive_type(receiver, "room.state")
                    await receive_type(caller, "room.state")
                    await send(caller, c["token"], type="call.start", number=TRUSTED, request_id="trusted-live")
                    state = await receive_type(caller, "room.state")
                    await receive_type(receiver, "room.state")
                    call_id = state["call_id"]
                    assert state["state"] == "ringing"
                    for socket, token in ((caller, c["token"]), (receiver, r["token"])):
                        pc = RTCPeerConnection()
                        peers.append(pc)
                        pc.addTrack(OutboundAudio())
                        await pc.setLocalDescription(await pc.createOffer())
                        await send(socket, token, type="media.offer", call_id=call_id,
                                   sdp=pc.localDescription.sdp)
                        answer = await receive_type(socket, "media.answer")
                        await pc.setRemoteDescription(RTCSessionDescription(answer["sdp"], "answer"))
                        await receive_type(socket, "room.state")
                    await send(receiver, r["token"], type="call.accept", call_id=call_id,
                               event_id="accept", expected_revision=state["revision"])
                    state = await receive_type(receiver, "room.state")
                    await receive_type(caller, "room.state")
                    assert state["state"] == "connecting"
                    room = hub.rooms[c["room"]]
                    for _ in range(100):
                        await room.check_timeout()
                        if room.gateway.state.value == "connected":
                            break
                        await asyncio.sleep(0.05)
                    assert room.gateway.state.value == "connected"
                    assert room.media.route == "human"
                    assert not any(op == "start" for op, _ in room.screening.ops)
                    await send(caller, c["token"], type="call.end", call_id=call_id,
                               event_id="end", expected_revision=room.gateway.revision)
                    for _ in range(5):
                        ended = await receive_type(caller, "room.state")
                        if ended["state"] == "ended":
                            break
                    assert ended["state"] == "ended"
            finally:
                for pc in peers:
                    await pc.close()
    asyncio.run(scenario())
