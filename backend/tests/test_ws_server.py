"""Tests for the WebSocket server protocol handling."""

from __future__ import annotations

import asyncio
import json
import socket

import pytest
import websockets
from websockets.asyncio.client import connect

from samantha.config import Config
from samantha.events import AppState
from samantha.protocol import IPC_PROTOCOL_VERSION, protocol_message
from samantha.ws_server import ConnectionState, WSServer


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("localhost", 0))
        return s.getsockname()[1]


@pytest.fixture
def config(tmp_path):
    return Config(ws_host="localhost", ws_port=_free_port(), data_dir=tmp_path / ".samantha")


@pytest.fixture
async def server(config):
    srv = WSServer(config)
    await srv.start()
    yield srv
    await srv.stop()


def _uri(srv: WSServer) -> str:
    host, port = srv.address
    return f"ws://{host}:{port}"


def _msg(msg_type: str, **payload) -> str:
    return json.dumps(protocol_message(msg_type, **payload))


# -- Server lifecycle --


@pytest.mark.asyncio
async def test_server_starts_and_accepts_connection(server):
    async with connect(_uri(server)):
        assert server.state == ConnectionState.CONNECTED


@pytest.mark.asyncio
async def test_server_tracks_disconnect(server):
    async with connect(_uri(server)):
        assert server.state == ConnectionState.CONNECTED
    await asyncio.sleep(0.05)
    assert server.state == ConnectionState.DISCONNECTED


@pytest.mark.asyncio
async def test_single_connection_only(server):
    async with connect(_uri(server)):
        with pytest.raises(websockets.exceptions.ConnectionClosed):
            async with connect(_uri(server)) as ws2:
                await ws2.recv()


# -- Text message routing --


@pytest.mark.asyncio
async def test_start_listening(server):
    async with connect(_uri(server)) as ws:
        await ws.send(_msg("start_listening"))
        await asyncio.sleep(0.05)
        assert server.listening is True


@pytest.mark.asyncio
async def test_stop_listening(server):
    async with connect(_uri(server)) as ws:
        await ws.send(_msg("start_listening"))
        await ws.send(_msg("stop_listening"))
        await asyncio.sleep(0.05)
        assert server.listening is False


@pytest.mark.asyncio
async def test_set_voice(server, config):
    async with connect(_uri(server)) as ws:
        await ws.send(_msg("set_voice", voice="coral"))
        await asyncio.sleep(0.05)
        assert config.voice == "coral"


@pytest.mark.asyncio
async def test_set_voice_invokes_handler(server):
    seen: list[str] = []

    async def handler(voice: str) -> None:
        seen.append(voice)

    server.voice_changed_handler = handler

    async with connect(_uri(server)) as ws:
        await ws.send(_msg("set_voice", voice="coral"))
        await asyncio.sleep(0.05)
        assert seen == ["coral"]


@pytest.mark.asyncio
async def test_set_voice_invalid(server, config):
    async with connect(_uri(server)) as ws:
        await ws.send(_msg("set_voice", voice="invalid_voice"))
        resp = json.loads(await asyncio.wait_for(ws.recv(), timeout=1.0))
        assert resp["type"] == "error"
        assert resp["protocol_version"] == IPC_PROTOCOL_VERSION
        assert "voice" in resp["message"].lower()
        assert config.voice == "ash"


@pytest.mark.asyncio
async def test_inject_context(server):
    async with connect(_uri(server)) as ws:
        await ws.send(_msg("inject_context", text="user is tired"))
        await asyncio.sleep(0.05)
        assert server.injected_contexts[-1] == "user is tired"


@pytest.mark.asyncio
async def test_interrupt(server):
    async with connect(_uri(server)) as ws:
        await ws.send(_msg("interrupt"))
        await asyncio.sleep(0.05)
        assert server.interrupt_count == 1


@pytest.mark.asyncio
async def test_start_listening_invokes_handler(server):
    called = False

    async def handler():
        nonlocal called
        called = True

    server.start_listening_handler = handler

    async with connect(_uri(server)) as ws:
        await ws.send(_msg("start_listening"))
        await asyncio.sleep(0.05)
        assert called is True


@pytest.mark.asyncio
async def test_interrupt_invokes_handler(server):
    called = False

    async def handler():
        nonlocal called
        called = True

    server.interrupt_handler = handler

    async with connect(_uri(server)) as ws:
        await ws.send(_msg("interrupt"))
        await asyncio.sleep(0.05)
        assert called is True


@pytest.mark.asyncio
async def test_unknown_message_type(server):
    async with connect(_uri(server)) as ws:
        await ws.send(_msg("unknown_type"))
        resp = json.loads(await asyncio.wait_for(ws.recv(), timeout=1.0))
        assert resp["type"] == "error"
        assert resp["protocol_version"] == IPC_PROTOCOL_VERSION
        assert "unknown" in resp["message"].lower()


@pytest.mark.asyncio
async def test_invalid_json(server):
    async with connect(_uri(server)) as ws:
        await ws.send("not json {{{")
        resp = json.loads(await asyncio.wait_for(ws.recv(), timeout=1.0))
        assert resp["type"] == "error"
        assert resp["protocol_version"] == IPC_PROTOCOL_VERSION


@pytest.mark.asyncio
async def test_missing_type_field(server):
    async with connect(_uri(server)) as ws:
        await ws.send(json.dumps({"protocol_version": IPC_PROTOCOL_VERSION, "data": "no type"}))
        resp = json.loads(await asyncio.wait_for(ws.recv(), timeout=1.0))
        assert resp["type"] == "error"
        assert resp["protocol_version"] == IPC_PROTOCOL_VERSION


@pytest.mark.asyncio
async def test_missing_protocol_version(server):
    async with connect(_uri(server)) as ws:
        await ws.send(json.dumps({"type": "start_listening"}))
        resp = json.loads(await asyncio.wait_for(ws.recv(), timeout=1.0))
        assert resp == {
            "protocol_version": IPC_PROTOCOL_VERSION,
            "type": "error",
            "message": "Missing 'protocol_version' field",
        }


@pytest.mark.asyncio
async def test_unsupported_protocol_version(server):
    async with connect(_uri(server)) as ws:
        await ws.send(json.dumps({"protocol_version": 99, "type": "start_listening"}))
        resp = json.loads(await asyncio.wait_for(ws.recv(), timeout=1.0))
        assert resp == {
            "protocol_version": IPC_PROTOCOL_VERSION,
            "type": "error",
            "message": "Unsupported protocol_version: 99. Supported versions: 1",
        }


# -- Binary message handling --


@pytest.mark.asyncio
async def test_binary_forwarded_when_listening(server):
    received: list[bytes] = []

    async def audio_handler(data: bytes):
        received.append(data)

    server.audio_handler = audio_handler

    async with connect(_uri(server)) as ws:
        await ws.send(_msg("start_listening"))
        await asyncio.sleep(0.02)
        audio = b"\x00\x01" * 480
        await ws.send(audio)
        await asyncio.sleep(0.05)
        assert server.received_audio[-1] == audio
        assert received == [audio]


@pytest.mark.asyncio
async def test_binary_dropped_when_not_listening(server):
    async with connect(_uri(server)) as ws:
        await ws.send(b"\x00\x01" * 480)
        await asyncio.sleep(0.05)
        assert len(server.received_audio) == 0


# -- Outgoing messages --


@pytest.mark.asyncio
async def test_send_text_message(server):
    async with connect(_uri(server)) as ws:
        await asyncio.sleep(0.02)
        await server.send_json({"type": "state_change", "state": "listening"})
        resp = json.loads(await asyncio.wait_for(ws.recv(), timeout=1.0))
        assert resp == {
            "protocol_version": IPC_PROTOCOL_VERSION,
            "type": "state_change",
            "state": "listening",
        }


@pytest.mark.asyncio
async def test_send_audio(server):
    async with connect(_uri(server)) as ws:
        await asyncio.sleep(0.02)
        audio = b"\xaa\xbb" * 100
        await server.send_audio(audio)
        data = await asyncio.wait_for(ws.recv(), timeout=1.0)
        assert data == audio


@pytest.mark.asyncio
async def test_send_when_no_client(server):
    # Should not raise
    await server.send_json({"type": "state_change", "state": "idle"})
    await server.send_audio(b"\x00")


@pytest.mark.asyncio
async def test_approve_and_reject_tool_call_handlers(server):
    approved: list[tuple[str, bool]] = []
    rejected: list[tuple[str, bool]] = []

    async def approve(call_id: str, always: bool):
        approved.append((call_id, always))

    async def reject(call_id: str, always: bool):
        rejected.append((call_id, always))

    server.approve_tool_call_handler = approve
    server.reject_tool_call_handler = reject

    async with connect(_uri(server)) as ws:
        await ws.send(_msg("approve_tool_call", call_id="call_1", always=True))
        await ws.send(_msg("reject_tool_call", call_id="call_2"))
        await asyncio.sleep(0.05)

    assert approved == [("call_1", True)]
    assert rejected == [("call_2", False)]


@pytest.mark.asyncio
async def test_get_state_returns_current_app_state(server):
    server.app_state = AppState.THINKING

    async with connect(_uri(server)) as ws:
        await ws.send(_msg("get_state"))
        resp = json.loads(await asyncio.wait_for(ws.recv(), timeout=1.0))
        assert resp == {
            "protocol_version": IPC_PROTOCOL_VERSION,
            "type": "state_change",
            "state": "thinking",
        }


@pytest.mark.asyncio
async def test_call_app_tool_sends_request_and_receives_result(server):
    async with connect(_uri(server)) as ws:
        task = asyncio.create_task(server.call_app_tool("frontmost_app_context"))

        request = json.loads(await asyncio.wait_for(ws.recv(), timeout=1.0))
        assert request["type"] == "app_tool_call"
        assert request["protocol_version"] == IPC_PROTOCOL_VERSION
        assert request["tool"] == "frontmost_app_context"
        assert request["args"] == {}

        await ws.send(
            _msg(
                "app_tool_result",
                request_id=request["request_id"],
                ok=True,
                result={"app_name": "Safari", "window_title": "OpenAI"},
            )
        )

        result = await asyncio.wait_for(task, timeout=1.0)
        assert result == {"app_name": "Safari", "window_title": "OpenAI"}


@pytest.mark.asyncio
async def test_call_app_tool_surfaces_error_response(server):
    async with connect(_uri(server)) as ws:
        task = asyncio.create_task(server.call_app_tool("capture_display"))

        request = json.loads(await asyncio.wait_for(ws.recv(), timeout=1.0))
        await ws.send(
            _msg(
                "app_tool_result",
                request_id=request["request_id"],
                ok=False,
                error="Screen capture unavailable",
            )
        )

        with pytest.raises(RuntimeError, match="Screen capture unavailable"):
            await asyncio.wait_for(task, timeout=1.0)


@pytest.mark.asyncio
async def test_call_app_tool_requires_connected_client(server):
    with pytest.raises(RuntimeError, match="Swift client is not connected"):
        await server.call_app_tool("frontmost_app_context")


@pytest.mark.asyncio
async def test_call_app_tool_times_out_without_result(server):
    async with connect(_uri(server)):
        with pytest.raises(RuntimeError, match="Timed out waiting for app_tool_result"):
            await server.call_app_tool("capture_display", timeout=0.01)


@pytest.mark.asyncio
async def test_unknown_app_tool_result_request_id(server):
    async with connect(_uri(server)) as ws:
        await ws.send(
            _msg(
                "app_tool_result",
                request_id="missing",
                ok=True,
                result={"app_name": "Finder"},
            )
        )

        resp = json.loads(await asyncio.wait_for(ws.recv(), timeout=1.0))
        assert resp == {
            "protocol_version": IPC_PROTOCOL_VERSION,
            "type": "error",
            "message": "Unknown request_id for app_tool_result: missing",
        }


@pytest.mark.asyncio
async def test_pending_app_tool_call_fails_on_disconnect(server):
    async with connect(_uri(server)) as ws:
        task = asyncio.create_task(server.call_app_tool("frontmost_app_context"))
        _request = json.loads(await asyncio.wait_for(ws.recv(), timeout=1.0))

    with pytest.raises(RuntimeError, match="Swift client disconnected"):
        await asyncio.wait_for(task, timeout=1.0)
