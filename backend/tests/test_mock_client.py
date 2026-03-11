"""Tests for the backend mock IPC client harness."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from samantha.mock_client import (
    chunk_audio_bytes,
    default_visual_context_app_tool_results,
    encode_control_message,
    receive_messages,
    send_audio_file,
)
from samantha.protocol import IPC_PROTOCOL_VERSION


class FakeWebSocket:
    def __init__(self, incoming: list[object] | None = None) -> None:
        self._incoming = list(incoming or [])
        self.sent: list[object] = []

    async def recv(self) -> object:
        if self._incoming:
            return self._incoming.pop(0)
        await asyncio.sleep(10)
        return None

    async def send(self, message: object) -> None:
        self.sent.append(message)


def test_encode_control_message_includes_protocol_version():
    payload = json.loads(encode_control_message("get_state"))
    assert payload == {"protocol_version": IPC_PROTOCOL_VERSION, "type": "get_state"}


def test_chunk_audio_bytes():
    chunks = chunk_audio_bytes(b"abcdef", 2)
    assert chunks == [b"ab", b"cd", b"ef"]


def test_chunk_audio_bytes_rejects_invalid_chunk_size():
    with pytest.raises(ValueError, match="chunk_size"):
        chunk_audio_bytes(b"abc", 0)


@pytest.mark.asyncio
async def test_send_audio_file_streams_chunks(tmp_path: Path):
    audio_path = tmp_path / "sample.pcm"
    audio_path.write_bytes(b"abcdefgh")
    ws = FakeWebSocket()

    chunks = await send_audio_file(ws, audio_path, chunk_size=3, chunk_delay_ms=0)

    assert chunks == 3
    assert ws.sent == [b"abc", b"def", b"gh"]


@pytest.mark.asyncio
async def test_receive_messages_collects_audio_and_auto_approves():
    approval_event = json.dumps(
        {
            "protocol_version": IPC_PROTOCOL_VERSION,
            "type": "tool_approval_required",
            "call_id": "call_123",
            "name": "file_write",
            "args": {"path": "/tmp/test.txt"},
        }
    )
    ws = FakeWebSocket(
        incoming=[
            approval_event,
            b"\x00\x01\x02",
        ]
    )

    summary = await receive_messages(ws, idle_timeout=0.01, auto_approve=True, verbose=False)

    assert summary.audio_frames == 1
    assert summary.audio_bytes == 3
    assert summary.auto_approved == ["call_123"]
    assert summary.auto_rejected == []
    assert len(summary.json_messages) == 1
    approve_payload = json.loads(ws.sent[0])
    assert approve_payload == {
        "protocol_version": IPC_PROTOCOL_VERSION,
        "type": "approve_tool_call",
        "call_id": "call_123",
        "always": False,
    }


@pytest.mark.asyncio
async def test_receive_messages_collects_audio_and_auto_rejects():
    approval_event = json.dumps(
        {
            "protocol_version": IPC_PROTOCOL_VERSION,
            "type": "tool_approval_required",
            "call_id": "call_456",
            "name": "file_write",
            "args": {"path": "/tmp/test.txt"},
        }
    )
    ws = FakeWebSocket(incoming=[approval_event])

    summary = await receive_messages(ws, idle_timeout=0.01, auto_reject=True, verbose=False)

    assert summary.auto_rejected == ["call_456"]
    reject_payload = json.loads(ws.sent[0])
    assert reject_payload == {
        "protocol_version": IPC_PROTOCOL_VERSION,
        "type": "reject_tool_call",
        "call_id": "call_456",
        "always": False,
    }


@pytest.mark.asyncio
async def test_receive_messages_answers_app_tool_calls():
    request = json.dumps(
        {
            "protocol_version": IPC_PROTOCOL_VERSION,
            "type": "app_tool_call",
            "request_id": "req_123",
            "tool": "frontmost_app_context",
            "args": {},
        }
    )
    ws = FakeWebSocket(incoming=[request])

    summary = await receive_messages(
        ws,
        idle_timeout=0.01,
        app_tool_results=default_visual_context_app_tool_results(),
        verbose=False,
    )

    assert summary.app_tool_calls == ["frontmost_app_context"]
    assert summary.app_tool_results_sent == ["frontmost_app_context"]
    response_payload = json.loads(ws.sent[0])
    assert response_payload["protocol_version"] == IPC_PROTOCOL_VERSION
    assert response_payload["type"] == "app_tool_result"
    assert response_payload["request_id"] == "req_123"
    assert response_payload["ok"] is True
    assert response_payload["result"]["app_name"] == "Safari"


def test_default_visual_context_app_tool_results_contains_expected_tools():
    fixtures = default_visual_context_app_tool_results()
    assert set(fixtures) == {"frontmost_app_context", "capture_display"}
    assert fixtures["frontmost_app_context"]["app_name"] == "Safari"
    assert fixtures["capture_display"]["mime_type"] == "image/png"
