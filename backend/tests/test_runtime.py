"""Tests for the realtime runtime bridge."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from agents.realtime import RealtimeModelSendRawMessage

from samantha.agents import create_voice_agent
from samantha.config import Config
from samantha.events import AppState
from samantha.protocol import protocol_message
from samantha.runtime import RealtimeRuntime

_STOP = object()


class FakeModel:
    def __init__(self) -> None:
        self.sent_events: list[object] = []

    async def send_event(self, event: object) -> None:
        self.sent_events.append(event)


class FakeSession:
    def __init__(self) -> None:
        self.model = FakeModel()
        self.sent_audio: list[tuple[bytes, bool]] = []
        self.interrupt_calls = 0
        self.approved: list[tuple[str, bool]] = []
        self.rejected: list[tuple[str, bool]] = []
        self._queue: asyncio.Queue[object] = asyncio.Queue()

    async def __aenter__(self):
        return self

    async def __aexit__(self, _exc_type, _exc, _tb):
        await self._queue.put(_STOP)

    def __aiter__(self):
        return self

    async def __anext__(self):
        event = await self._queue.get()
        if event is _STOP:
            raise StopAsyncIteration
        return event

    async def send_audio(self, audio: bytes, *, commit: bool = False) -> None:
        self.sent_audio.append((audio, commit))

    async def interrupt(self) -> None:
        self.interrupt_calls += 1

    async def approve_tool_call(self, call_id: str, *, always: bool = False) -> None:
        self.approved.append((call_id, always))

    async def reject_tool_call(self, call_id: str, *, always: bool = False) -> None:
        self.rejected.append((call_id, always))

    async def push(self, event: object) -> None:
        await self._queue.put(event)


class FakeRunner:
    def __init__(self, session: FakeSession) -> None:
        self.session = session
        self.run_calls = 0

    async def run(self, *, context=None, model_config=None):
        del context, model_config
        self.run_calls += 1
        return self.session


class FakeWSServer:
    def __init__(self) -> None:
        self.json_messages: list[dict] = []
        self.audio_messages: list[bytes] = []
        self.app_state = AppState.IDLE
        self.audio_handler = None
        self.start_listening_handler = None
        self.stop_listening_handler = None
        self.interrupt_handler = None
        self.inject_context_handler = None
        self.approve_tool_call_handler = None
        self.reject_tool_call_handler = None

    async def send_json(self, msg: dict) -> None:
        self.json_messages.append(msg)

    async def send_audio(self, data: bytes) -> None:
        self.audio_messages.append(data)

    async def publish_state(self, state: AppState) -> None:
        self.app_state = state
        self.json_messages.append(protocol_message("state_change", state=str(state)))


class FakeMemoryStore:
    def __init__(self) -> None:
        self.entries: list[str] = []

    async def append_daily_log(self, entry: str) -> int:
        self.entries.append(entry)
        return len(self.entries)


@pytest.fixture
def cfg(tmp_path):
    return Config(data_dir=tmp_path / ".samantha")


def _make_runtime(cfg: Config, *, memory_store: FakeMemoryStore | None = None):
    ws = FakeWSServer()
    agent, runner_config = create_voice_agent(cfg)
    session = FakeSession()
    runner = FakeRunner(session)
    runtime = RealtimeRuntime(
        cfg,
        ws,
        agent=agent,
        runner_config=runner_config,
        runner_factory=lambda **_: runner,
        memory_store=memory_store,
        require_api_key=False,
    )
    return runtime, ws, session, runner


@pytest.mark.asyncio
async def test_runtime_controls_audio_commit_context_and_approvals(cfg):
    runtime, ws, session, runner = _make_runtime(cfg)

    await runtime.handle_start_listening()
    await asyncio.sleep(0.05)
    assert runner.run_calls == 1
    assert ws.app_state == AppState.LISTENING

    await runtime.handle_audio_chunk(b"\x00\x01")
    await runtime.handle_stop_listening()
    await runtime.handle_inject_context("user is tired")

    runtime._dispatcher.set_state(AppState.SPEAKING)
    await runtime.handle_interrupt()
    await runtime.handle_approve_tool_call("call_1", True)
    await runtime.handle_reject_tool_call("call_2", False)
    await asyncio.sleep(0.05)

    assert session.sent_audio == [(b"\x00\x01", False)]
    assert session.interrupt_calls == 1
    assert session.approved == [("call_1", True)]
    assert session.rejected == [("call_2", False)]
    assert protocol_message("clear_playback") in ws.json_messages

    raw_messages = [
        event.message
        for event in session.model.sent_events
        if isinstance(event, RealtimeModelSendRawMessage)
    ]
    assert {"type": "input_audio_buffer.commit"} in raw_messages
    assert {
        "type": "conversation.item.create",
        "other_data": {
            "item": {
                "type": "message",
                "role": "system",
                "content": [{"type": "input_text", "text": "user is tired"}],
            }
        },
    } in raw_messages

    await runtime.stop()


@pytest.mark.asyncio
async def test_runtime_forwards_session_events_to_websocket(cfg):
    runtime, ws, session, _runner = _make_runtime(cfg)

    await runtime.handle_start_listening()
    await session.push(SimpleNamespace(type="audio", data=b"\xaa\xbb"))
    await session.push(
        SimpleNamespace(
            type="raw_model_event",
            data=SimpleNamespace(
                type="raw_server_event",
                data={"type": "input_audio_buffer.speech_started"},
            ),
        )
    )
    await session.push(
        SimpleNamespace(
            type="history_added",
            item=SimpleNamespace(
                item_id="user_1",
                type="message",
                role="user",
                content=[SimpleNamespace(type="input_text", text="hello")],
            ),
        )
    )
    await session.push(
        SimpleNamespace(
            type="tool_start",
            tool=SimpleNamespace(name="file_write"),
            arguments='{"path": "/tmp/test.txt"}',
        )
    )
    await session.push(
        SimpleNamespace(
            type="tool_approval_required",
            tool=SimpleNamespace(name="file_write"),
            call_id="call_7",
            arguments='{"path": "/tmp/test.txt"}',
        )
    )
    await session.push(
        SimpleNamespace(
            type="history_updated",
            history=[
                SimpleNamespace(
                    item_id="assistant_1",
                    type="message",
                    role="assistant",
                    status="completed",
                    content=[SimpleNamespace(type="audio", transcript="hi there")],
                )
            ],
        )
    )
    await session.push(SimpleNamespace(type="tool_end", tool=SimpleNamespace(name="file_write"), output="ok"))
    await session.push(SimpleNamespace(type="error", error="boom"))
    await asyncio.sleep(0.1)

    assert ws.audio_messages == [b"\xaa\xbb"]
    assert protocol_message("transcript", role="user", text="hello", final=True) in ws.json_messages
    assert protocol_message("transcript", role="assistant", text="hi there", final=True) in ws.json_messages
    assert protocol_message("tool_start", name="file_write", args={"path": "/tmp/test.txt"}) in ws.json_messages
    assert protocol_message(
        "tool_approval_required",
        name="file_write",
        call_id="call_7",
        args={"path": "/tmp/test.txt"},
    ) in ws.json_messages
    assert protocol_message("clear_playback") in ws.json_messages
    assert protocol_message("tool_end", name="file_write", result="ok") in ws.json_messages
    assert protocol_message("error", message="boom") in ws.json_messages
    assert protocol_message("state_change", state="speaking") in ws.json_messages
    assert protocol_message("state_change", state="listening") in ws.json_messages

    await runtime.stop()


@pytest.mark.asyncio
async def test_runtime_appends_daily_log_entries_for_turns_and_memory_signals(cfg):
    memory_store = FakeMemoryStore()
    runtime, _ws, session, _runner = _make_runtime(cfg, memory_store=memory_store)

    await runtime.handle_start_listening()
    await session.push(
        SimpleNamespace(
            type="history_added",
            item=SimpleNamespace(
                item_id="user_1",
                type="message",
                role="user",
                content=[SimpleNamespace(type="input_text", text="I prefer tea in the morning")],
            ),
        )
    )
    await session.push(
        SimpleNamespace(
            type="tool_start",
            tool=SimpleNamespace(name="memory_save"),
            arguments='{"content": "Fraser prefers tea in the morning", "tags": "preference"}',
        )
    )
    await session.push(
        SimpleNamespace(
            type="history_updated",
            history=[
                SimpleNamespace(
                    item_id="assistant_1",
                    type="message",
                    role="assistant",
                    status="completed",
                    content=[SimpleNamespace(type="audio", transcript="I will remember that.")],
                )
            ],
        )
    )
    await asyncio.sleep(0.1)

    entries = [json.loads(entry) for entry in memory_store.entries]
    assert entries == [
        {
            "final": True,
            "kind": "conversation_turn",
            "role": "user",
            "text": "I prefer tea in the morning",
        },
        {
            "content": "Fraser prefers tea in the morning",
            "kind": "memory_promotion_signal",
            "tags": "preference",
            "tool": "memory_save",
        },
        {
            "final": True,
            "kind": "conversation_turn",
            "role": "assistant",
            "text": "I will remember that.",
        },
    ]

    await runtime.stop()


@pytest.mark.asyncio
async def test_runtime_logs_turn_lifecycle_metrics(cfg, caplog):
    runtime, _ws, session, _runner = _make_runtime(cfg)
    caplog.set_level("INFO")

    await runtime.handle_start_listening()
    await session.push(SimpleNamespace(type="audio", data=b"\xaa\xbb"))
    await session.push(SimpleNamespace(type="agent_end"))
    await asyncio.sleep(0.1)

    assert "Turn started session_id=" in caplog.text
    assert "First audio session_id=" in caplog.text
    assert "Turn finished session_id=" in caplog.text

    await runtime.stop()
