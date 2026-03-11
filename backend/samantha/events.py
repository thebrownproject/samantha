"""Realtime event dispatcher and app-state mapping."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from enum import StrEnum
from typing import Any, ClassVar

from samantha.protocol import protocol_message

logger = logging.getLogger(__name__)

_VALID_ROLES = {"user", "assistant"}


def normalize_transcript(text: str, role: str, final: bool = False) -> dict[str, Any] | None:
    """Normalize a transcript fragment into a stable IPC payload.

    Returns None if text is empty after stripping (suppresses blank partials).
    """
    cleaned = text.strip() if text else ""
    if not cleaned:
        return None
    if role not in _VALID_ROLES:
        logger.warning("Unknown transcript role %r, defaulting to 'assistant'", role)
        role = "assistant"
    return msg_transcript(role, cleaned, final=final)


class AppState(StrEnum):
    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"
    ERROR = "error"


# -- JSON message builders for IPC protocol --

def msg_state_change(state: AppState) -> dict[str, Any]:
    return protocol_message("state_change", state=str(state))


def msg_transcript(role: str, text: str, final: bool = False) -> dict[str, Any]:
    return protocol_message("transcript", role=role, text=text, final=final)


def msg_tool_start(name: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
    msg = protocol_message("tool_start", name=name)
    if args is not None:
        msg["args"] = args
    return msg


def msg_tool_end(name: str, result: str | None = None) -> dict[str, Any]:
    msg = protocol_message("tool_end", name=name)
    if result is not None:
        msg["result"] = result
    return msg


def msg_tool_approval_required(
    name: str,
    call_id: str,
    args: dict[str, Any] | None = None,
) -> dict[str, Any]:
    msg = protocol_message("tool_approval_required", name=name, call_id=call_id)
    if args is not None:
        msg["args"] = args
    return msg


def msg_clear_playback() -> dict[str, Any]:
    return protocol_message("clear_playback")


def msg_error(message: str) -> dict[str, Any]:
    return protocol_message("error", message=message)


# -- Event dispatcher --

# Callback type aliases
StateCallback = Callable[[AppState], Any]
TranscriptCallback = Callable[[dict[str, Any]], Any]
ToolCallback = Callable[[dict[str, Any]], Any]
ApprovalCallback = Callable[[dict[str, Any]], Any]
AudioCallback = Callable[[bytes], Any]
ErrorCallback = Callable[[dict[str, str]], Any]


class EventDispatcher:
    """Maps realtime session events to app states and IPC messages.

    Sits between `async for event in session` and ws_server output.
    Maintains current AppState, suppresses duplicate state emissions,
    and invokes registered callbacks.
    """

    def __init__(self) -> None:
        self.state: AppState = AppState.IDLE
        self._on_state_change: list[StateCallback] = []
        self._on_transcript: list[TranscriptCallback] = []
        self._on_tool_event: list[ToolCallback] = []
        self._on_tool_approval: list[ApprovalCallback] = []
        self._on_audio: list[AudioCallback] = []
        self._on_error: list[ErrorCallback] = []
        self._transcript_snapshots: dict[str, tuple[str, str, bool]] = {}

    def on_state_change(self, cb: StateCallback) -> None:
        self._on_state_change.append(cb)

    def on_transcript(self, cb: TranscriptCallback) -> None:
        self._on_transcript.append(cb)

    def on_tool_event(self, cb: ToolCallback) -> None:
        self._on_tool_event.append(cb)

    def on_tool_approval(self, cb: ApprovalCallback) -> None:
        self._on_tool_approval.append(cb)

    def on_audio(self, cb: AudioCallback) -> None:
        self._on_audio.append(cb)

    def on_error(self, cb: ErrorCallback) -> None:
        self._on_error.append(cb)

    def set_state(self, new: AppState) -> None:
        if new == self.state:
            return
        self.state = new
        for cb in self._on_state_change:
            cb(new)

    def handle_event(self, event: Any) -> None:
        """Dispatch a single realtime session event."""
        etype = getattr(event, "type", None)
        if etype is None:
            logger.warning("Event missing 'type' attribute: %r", event)
            return

        handler = self._handlers.get(etype)
        if handler is not None:
            handler(self, event)
        else:
            logger.debug("Ignoring unknown event type: %s", etype)

    # -- Per-event-type handlers --

    def emit_transcript(self, text: str, role: str, final: bool = False) -> None:
        """Normalize and dispatch a transcript event."""
        msg = normalize_transcript(text, role, final=final)
        if msg is None:
            return
        for cb in self._on_transcript:
            cb(msg)

    def _parse_tool_args(self, raw: str | None) -> dict[str, Any] | None:
        if not raw:
            return None
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None

    def _emit_history_transcript(self, item: Any) -> None:
        if getattr(item, "type", None) != "message":
            return

        role = getattr(item, "role", None)
        if role not in _VALID_ROLES:
            return

        content = getattr(item, "content", None) or []
        fragments: list[str] = []
        for entry in content:
            entry_type = getattr(entry, "type", None)
            text: str | None = None
            if entry_type in {"text", "input_text"}:
                text = getattr(entry, "text", None)
            elif entry_type in {"audio", "input_audio"}:
                text = getattr(entry, "transcript", None)

            cleaned = text.strip() if isinstance(text, str) else ""
            if cleaned and (not fragments or cleaned != fragments[-1]):
                fragments.append(cleaned)

        if not fragments:
            return

        item_id = getattr(item, "item_id", None)
        if not isinstance(item_id, str) or not item_id:
            return

        transcript = " ".join(fragments)
        final = True if role == "user" else getattr(item, "status", None) == "completed"
        snapshot = (role, transcript, final)
        if self._transcript_snapshots.get(item_id) == snapshot:
            return
        self._transcript_snapshots[item_id] = snapshot
        self.emit_transcript(transcript, role, final=final)

    def _handle_audio(self, event: Any) -> None:
        self.set_state(AppState.SPEAKING)
        data = getattr(event, "data", None) or getattr(event, "audio", None)
        if isinstance(data, bytes):
            for cb in self._on_audio:
                cb(data)

    def _handle_audio_end(self, _event: Any) -> None:
        self.set_state(AppState.IDLE)

    def _handle_audio_interrupted(self, _event: Any) -> None:
        self.set_state(AppState.LISTENING)

    def _handle_tool_start(self, event: Any) -> None:
        self.set_state(AppState.THINKING)
        tool = getattr(event, "tool", None)
        name = getattr(tool, "name", "unknown") if tool else "unknown"
        msg = msg_tool_start(name, self._parse_tool_args(getattr(event, "arguments", None)))
        for cb in self._on_tool_event:
            cb(msg)

    def _handle_tool_end(self, event: Any) -> None:
        tool = getattr(event, "tool", None)
        name = getattr(tool, "name", "unknown") if tool else "unknown"
        output = getattr(event, "output", None)
        msg = msg_tool_end(name, str(output) if output is not None else None)
        for cb in self._on_tool_event:
            cb(msg)

    def _handle_tool_approval_required(self, event: Any) -> None:
        self.set_state(AppState.THINKING)
        tool = getattr(event, "tool", None)
        name = getattr(tool, "name", "unknown") if tool else "unknown"
        call_id = getattr(event, "call_id", "")
        msg = msg_tool_approval_required(
            name,
            call_id,
            self._parse_tool_args(getattr(event, "arguments", None)),
        )
        for cb in self._on_tool_approval:
            cb(msg)

    def _handle_agent_end(self, _event: Any) -> None:
        self.set_state(AppState.IDLE)

    def _handle_error(self, event: Any) -> None:
        self.set_state(AppState.ERROR)
        error = getattr(event, "error", "unknown error")
        msg = msg_error(str(error))
        for cb in self._on_error:
            cb(msg)

    def _handle_raw_model_event(self, event: Any) -> None:
        payload = getattr(event, "data", None)
        payload_type = None
        if isinstance(payload, dict):
            payload_type = payload.get("type")
        else:
            payload_type = getattr(payload, "type", None)
            if payload_type == "raw_server_event":
                raw_data = getattr(payload, "data", None)
                if isinstance(raw_data, dict):
                    payload_type = raw_data.get("type")
                else:
                    payload_type = getattr(raw_data, "type", payload_type)

        if payload_type == "input_audio_buffer.speech_started":
            self.set_state(AppState.LISTENING)

    def _handle_history_added(self, event: Any) -> None:
        self._emit_history_transcript(getattr(event, "item", None))

    def _handle_history_updated(self, event: Any) -> None:
        history = getattr(event, "history", None) or []
        for item in history:
            self._emit_history_transcript(item)

    # Handler dispatch table
    _handlers: ClassVar[dict[str, Callable[[EventDispatcher, Any], None]]] = {
        "audio": _handle_audio,
        "audio_end": _handle_audio_end,
        "audio_interrupted": _handle_audio_interrupted,
        "tool_start": _handle_tool_start,
        "tool_end": _handle_tool_end,
        "tool_approval_required": _handle_tool_approval_required,
        "agent_end": _handle_agent_end,
        "error": _handle_error,
        "raw_model_event": _handle_raw_model_event,
        "history_added": _handle_history_added,
        "history_updated": _handle_history_updated,
    }
