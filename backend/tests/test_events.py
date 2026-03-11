"""Tests for the realtime event dispatcher and app-state mapping."""

from types import SimpleNamespace

import pytest

from samantha.events import (
    AppState,
    EventDispatcher,
    msg_error,
    msg_state_change,
    msg_tool_approval_required,
    msg_tool_end,
    msg_tool_start,
    msg_transcript,
    normalize_transcript,
)
from samantha.protocol import protocol_message


def _evt(type: str, **kwargs) -> SimpleNamespace:
    return SimpleNamespace(type=type, **kwargs)


def _msg(msg_type: str, **payload):
    return protocol_message(msg_type, **payload)


@pytest.fixture
def dispatcher():
    return EventDispatcher()


# -- AppState enum --

def test_app_state_values():
    assert set(AppState) == {"idle", "listening", "thinking", "speaking", "error"}


# -- JSON message builders --

def test_msg_state_change():
    assert msg_state_change(AppState.SPEAKING) == _msg("state_change", state="speaking")


def test_msg_transcript():
    m = msg_transcript("user", "hello", final=True)
    assert m == _msg("transcript", role="user", text="hello", final=True)


def test_msg_transcript_defaults():
    m = msg_transcript("assistant", "hi")
    assert m["final"] is False


def test_msg_tool_start_with_args():
    m = msg_tool_start("reason_deeply", {"task": "analyze"})
    assert m == _msg("tool_start", name="reason_deeply", args={"task": "analyze"})


def test_msg_tool_start_no_args():
    m = msg_tool_start("safe_bash")
    assert m == _msg("tool_start", name="safe_bash")
    assert "args" not in m


def test_msg_tool_end_with_result():
    m = msg_tool_end("reason_deeply", "42")
    assert m == _msg("tool_end", name="reason_deeply", result="42")


def test_msg_tool_end_no_result():
    m = msg_tool_end("safe_bash")
    assert m == _msg("tool_end", name="safe_bash")
    assert "result" not in m


def test_msg_tool_approval_required():
    m = msg_tool_approval_required("file_write", "call_123", {"path": "/tmp/test.txt"})
    assert m == _msg(
        "tool_approval_required",
        name="file_write",
        call_id="call_123",
        args={"path": "/tmp/test.txt"},
    )


def test_msg_error():
    assert msg_error("timeout") == _msg("error", message="timeout")


# -- EventDispatcher state transitions --

def test_initial_state(dispatcher):
    assert dispatcher.state == AppState.IDLE


def test_audio_transitions_to_speaking(dispatcher):
    dispatcher.handle_event(_evt("audio", data=b"\x00\x01"))
    assert dispatcher.state == AppState.SPEAKING


def test_audio_end_transitions_to_idle(dispatcher):
    dispatcher.handle_event(_evt("audio", data=b"\x00"))
    dispatcher.handle_event(_evt("audio_end"))
    assert dispatcher.state == AppState.IDLE


def test_audio_interrupted_transitions_to_listening(dispatcher):
    dispatcher.handle_event(_evt("audio", data=b"\x00"))
    dispatcher.handle_event(_evt("audio_interrupted"))
    assert dispatcher.state == AppState.LISTENING


def test_tool_start_transitions_to_thinking(dispatcher):
    tool = SimpleNamespace(name="reason_deeply")
    dispatcher.handle_event(_evt("tool_start", tool=tool))
    assert dispatcher.state == AppState.THINKING


def test_tool_end_does_not_change_state(dispatcher):
    tool = SimpleNamespace(name="reason_deeply")
    dispatcher.handle_event(_evt("tool_start", tool=tool))
    dispatcher.handle_event(_evt("tool_end", tool=tool, output="done"))
    assert dispatcher.state == AppState.THINKING


def test_agent_end_transitions_to_idle(dispatcher):
    dispatcher.handle_event(_evt("audio", data=b"\x00"))
    dispatcher.handle_event(_evt("agent_end"))
    assert dispatcher.state == AppState.IDLE


def test_error_transitions_to_error(dispatcher):
    dispatcher.handle_event(_evt("error", error="connection lost"))
    assert dispatcher.state == AppState.ERROR


def test_raw_speech_started_transitions_to_listening(dispatcher):
    dispatcher.handle_event(_evt("audio", data=b"\x00"))
    dispatcher.handle_event(
        _evt("raw_model_event", data={"type": "input_audio_buffer.speech_started"})
    )
    assert dispatcher.state == AppState.LISTENING


def test_raw_server_event_speech_started_transitions_to_listening(dispatcher):
    dispatcher.handle_event(_evt("audio", data=b"\x00"))
    dispatcher.handle_event(
        _evt(
            "raw_model_event",
            data=SimpleNamespace(
                type="raw_server_event",
                data={"type": "input_audio_buffer.speech_started"},
            ),
        )
    )
    assert dispatcher.state == AppState.LISTENING


def test_raw_model_event_ignores_other_types(dispatcher):
    dispatcher.handle_event(_evt("raw_model_event", data={"type": "other.event"}))
    assert dispatcher.state == AppState.IDLE


def test_unknown_event_ignored(dispatcher):
    dispatcher.handle_event(_evt("some_future_event"))
    assert dispatcher.state == AppState.IDLE


def test_event_missing_type_ignored(dispatcher):
    dispatcher.handle_event(object())
    assert dispatcher.state == AppState.IDLE


# -- Duplicate state suppression --

def test_duplicate_state_suppressed(dispatcher):
    states = []
    dispatcher.on_state_change(states.append)
    dispatcher.handle_event(_evt("audio", data=b"\x00"))
    dispatcher.handle_event(_evt("audio", data=b"\x01"))
    dispatcher.handle_event(_evt("audio", data=b"\x02"))
    assert states == [AppState.SPEAKING]


def test_state_change_emits_on_actual_change(dispatcher):
    states = []
    dispatcher.on_state_change(states.append)
    dispatcher.handle_event(_evt("audio", data=b"\x00"))
    dispatcher.handle_event(_evt("audio_interrupted"))
    dispatcher.handle_event(_evt("audio", data=b"\x00"))
    assert states == [AppState.SPEAKING, AppState.LISTENING, AppState.SPEAKING]


# -- Callback invocation --

def test_on_state_change_callback(dispatcher):
    received = []
    dispatcher.on_state_change(received.append)
    dispatcher.handle_event(_evt("error", error="fail"))
    assert received == [AppState.ERROR]


def test_on_audio_callback(dispatcher):
    chunks = []
    dispatcher.on_audio(chunks.append)
    dispatcher.handle_event(_evt("audio", data=b"\xaa\xbb"))
    assert chunks == [b"\xaa\xbb"]


def test_on_tool_event_callback_start(dispatcher):
    events = []
    dispatcher.on_tool_event(events.append)
    tool = SimpleNamespace(name="safe_bash")
    dispatcher.handle_event(_evt("tool_start", tool=tool, arguments='{"command": "ls"}'))
    assert len(events) == 1
    assert events[0]["type"] == "tool_start"
    assert events[0]["name"] == "safe_bash"
    assert events[0]["args"] == {"command": "ls"}


def test_on_tool_event_callback_end(dispatcher):
    events = []
    dispatcher.on_tool_event(events.append)
    tool = SimpleNamespace(name="safe_bash")
    dispatcher.handle_event(_evt("tool_end", tool=tool, output="ok"))
    assert len(events) == 1
    assert events[0] == _msg("tool_end", name="safe_bash", result="ok")


def test_on_error_callback(dispatcher):
    errors = []
    dispatcher.on_error(errors.append)
    dispatcher.handle_event(_evt("error", error="oops"))
    assert errors == [_msg("error", message="oops")]


def test_on_tool_approval_callback(dispatcher):
    approvals = []
    dispatcher.on_tool_approval(approvals.append)
    tool = SimpleNamespace(name="file_write")
    dispatcher.handle_event(
        _evt("tool_approval_required", tool=tool, call_id="call_1", arguments='{"path": "/tmp/test.txt"}')
    )
    assert approvals == [
        _msg(
            "tool_approval_required",
            name="file_write",
            call_id="call_1",
            args={"path": "/tmp/test.txt"},
        )
    ]


def test_multiple_callbacks(dispatcher):
    a, b = [], []
    dispatcher.on_state_change(a.append)
    dispatcher.on_state_change(b.append)
    dispatcher.handle_event(_evt("audio", data=b"\x00"))
    assert a == [AppState.SPEAKING]
    assert b == [AppState.SPEAKING]


# -- Full sequence: realistic event flow --

def test_full_conversation_flow(dispatcher):
    states = []
    dispatcher.on_state_change(states.append)

    # User speaks -> assistant responds -> user interrupts -> assistant resumes
    dispatcher.handle_event(
        _evt("raw_model_event", data={"type": "input_audio_buffer.speech_started"})
    )
    dispatcher.handle_event(_evt("audio", data=b"\x00"))
    dispatcher.handle_event(_evt("audio", data=b"\x01"))
    dispatcher.handle_event(_evt("audio_interrupted"))
    dispatcher.handle_event(_evt("audio", data=b"\x02"))
    dispatcher.handle_event(_evt("audio_end"))
    dispatcher.handle_event(_evt("agent_end"))

    assert states == [
        AppState.LISTENING,
        AppState.SPEAKING,
        AppState.LISTENING,
        AppState.SPEAKING,
        AppState.IDLE,
    ]
    # audio_end -> idle, agent_end suppressed (already idle)


# -- normalize_transcript --

def test_normalize_strips_whitespace():
    result = normalize_transcript("  hello world  ", "user", final=True)
    assert result == _msg("transcript", role="user", text="hello world", final=True)


def test_normalize_partial_transcript():
    result = normalize_transcript("partial", "assistant", final=False)
    assert result["final"] is False
    assert result["text"] == "partial"


def test_normalize_final_transcript():
    result = normalize_transcript("done", "user", final=True)
    assert result["final"] is True


def test_normalize_empty_returns_none():
    assert normalize_transcript("", "user") is None


def test_normalize_whitespace_only_returns_none():
    assert normalize_transcript("   \n\t  ", "assistant") is None


def test_normalize_none_text_returns_none():
    assert normalize_transcript(None, "user") is None


def test_normalize_unknown_role_defaults_to_assistant():
    result = normalize_transcript("text", "system")
    assert result["role"] == "assistant"


def test_normalize_user_role():
    result = normalize_transcript("hi", "user")
    assert result["role"] == "user"


def test_normalize_assistant_role():
    result = normalize_transcript("hi", "assistant")
    assert result["role"] == "assistant"


# -- EventDispatcher.emit_transcript --

def test_emit_transcript_fires_callbacks(dispatcher):
    received = []
    dispatcher.on_transcript(received.append)
    dispatcher.emit_transcript("hello", "user", final=True)
    assert len(received) == 1
    assert received[0] == _msg("transcript", role="user", text="hello", final=True)


def test_emit_transcript_suppresses_empty(dispatcher):
    received = []
    dispatcher.on_transcript(received.append)
    dispatcher.emit_transcript("  ", "user")
    assert received == []


def test_emit_transcript_normalizes_role(dispatcher):
    received = []
    dispatcher.on_transcript(received.append)
    dispatcher.emit_transcript("text", "unknown_role")
    assert received[0]["role"] == "assistant"


def test_history_added_emits_user_transcript(dispatcher):
    received = []
    dispatcher.on_transcript(received.append)
    item = SimpleNamespace(
        item_id="user_1",
        type="message",
        role="user",
        content=[SimpleNamespace(type="input_text", text="hello from user")],
    )
    dispatcher.handle_event(_evt("history_added", item=item))
    assert received == [
        _msg("transcript", role="user", text="hello from user", final=True)
    ]


def test_history_updated_emits_assistant_partial_then_final(dispatcher):
    received = []
    dispatcher.on_transcript(received.append)
    partial = SimpleNamespace(
        item_id="assistant_1",
        type="message",
        role="assistant",
        status="in_progress",
        content=[SimpleNamespace(type="audio", transcript="hello there")],
    )
    final = SimpleNamespace(
        item_id="assistant_1",
        type="message",
        role="assistant",
        status="completed",
        content=[SimpleNamespace(type="audio", transcript="hello there")],
    )
    dispatcher.handle_event(_evt("history_updated", history=[partial]))
    dispatcher.handle_event(_evt("history_updated", history=[final]))
    assert received == [
        _msg("transcript", role="assistant", text="hello there", final=False),
        _msg("transcript", role="assistant", text="hello there", final=True),
    ]
