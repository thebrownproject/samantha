"""Tests for the WebSocket server protocol handling."""

from __future__ import annotations

import asyncio
import json
import socket

import pytest
import websockets
from websockets.asyncio.client import connect

from samantha.config import Config
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
        await ws.send(json.dumps({"type": "start_listening"}))
        await asyncio.sleep(0.05)
        assert server.listening is True


@pytest.mark.asyncio
async def test_stop_listening(server):
    async with connect(_uri(server)) as ws:
        await ws.send(json.dumps({"type": "start_listening"}))
        await ws.send(json.dumps({"type": "stop_listening"}))
        await asyncio.sleep(0.05)
        assert server.listening is False


@pytest.mark.asyncio
async def test_set_voice(server, config):
    async with connect(_uri(server)) as ws:
        await ws.send(json.dumps({"type": "set_voice", "voice": "coral"}))
        await asyncio.sleep(0.05)
        assert config.voice == "coral"


@pytest.mark.asyncio
async def test_set_voice_invalid(server, config):
    async with connect(_uri(server)) as ws:
        await ws.send(json.dumps({"type": "set_voice", "voice": "invalid_voice"}))
        resp = json.loads(await asyncio.wait_for(ws.recv(), timeout=1.0))
        assert resp["type"] == "error"
        assert "voice" in resp["message"].lower()
        assert config.voice == "ash"


@pytest.mark.asyncio
async def test_inject_context(server):
    async with connect(_uri(server)) as ws:
        await ws.send(json.dumps({"type": "inject_context", "text": "user is tired"}))
        await asyncio.sleep(0.05)
        assert server.injected_contexts[-1] == "user is tired"


@pytest.mark.asyncio
async def test_interrupt(server):
    async with connect(_uri(server)) as ws:
        await ws.send(json.dumps({"type": "interrupt"}))
        await asyncio.sleep(0.05)
        assert server.interrupt_count == 1


@pytest.mark.asyncio
async def test_unknown_message_type(server):
    async with connect(_uri(server)) as ws:
        await ws.send(json.dumps({"type": "unknown_type"}))
        resp = json.loads(await asyncio.wait_for(ws.recv(), timeout=1.0))
        assert resp["type"] == "error"
        assert "unknown" in resp["message"].lower()


@pytest.mark.asyncio
async def test_invalid_json(server):
    async with connect(_uri(server)) as ws:
        await ws.send("not json {{{")
        resp = json.loads(await asyncio.wait_for(ws.recv(), timeout=1.0))
        assert resp["type"] == "error"


@pytest.mark.asyncio
async def test_missing_type_field(server):
    async with connect(_uri(server)) as ws:
        await ws.send(json.dumps({"data": "no type"}))
        resp = json.loads(await asyncio.wait_for(ws.recv(), timeout=1.0))
        assert resp["type"] == "error"


# -- Binary message handling --

@pytest.mark.asyncio
async def test_binary_forwarded_when_listening(server):
    async with connect(_uri(server)) as ws:
        await ws.send(json.dumps({"type": "start_listening"}))
        await asyncio.sleep(0.02)
        audio = b"\x00\x01" * 480
        await ws.send(audio)
        await asyncio.sleep(0.05)
        assert server.received_audio[-1] == audio


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
        assert resp == {"type": "state_change", "state": "listening"}


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
