"""Realtime event dispatcher and app-state mapping."""

from __future__ import annotations

import logging
from collections.abc import Callable
from enum import StrEnum
from typing import Any, ClassVar

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

def msg_state_change(state: AppState) -> dict[str, str]:
    return {"type": "state_change", "state": str(state)}


def msg_transcript(role: str, text: str, final: bool = False) -> dict[str, Any]:
    return {"type": "transcript", "role": role, "text": text, "final": final}


def msg_tool_start(name: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
    msg: dict[str, Any] = {"type": "tool_start", "name": name}
    if args is not None:
        msg["args"] = args
    return msg


def msg_tool_end(name: str, result: str | None = None) -> dict[str, Any]:
    msg: dict[str, Any] = {"type": "tool_end", "name": name}
    if result is not None:
        msg["result"] = result
    return msg


def msg_clear_playback() -> dict[str, str]:
    return {"type": "clear_playback"}


def msg_error(message: str) -> dict[str, str]:
    return {"type": "error", "message": message}


# -- Event dispatcher --

# Callback type aliases
StateCallback = Callable[[AppState], Any]
TranscriptCallback = Callable[[dict[str, Any]], Any]
ToolCallback = Callable[[dict[str, Any]], Any]
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
        self._on_audio: list[AudioCallback] = []
        self._on_error: list[ErrorCallback] = []

    def on_state_change(self, cb: StateCallback) -> None:
        self._on_state_change.append(cb)

    def on_transcript(self, cb: TranscriptCallback) -> None:
        self._on_transcript.append(cb)

    def on_tool_event(self, cb: ToolCallback) -> None:
        self._on_tool_event.append(cb)

    def on_audio(self, cb: AudioCallback) -> None:
        self._on_audio.append(cb)

    def on_error(self, cb: ErrorCallback) -> None:
        self._on_error.append(cb)

    def _set_state(self, new: AppState) -> None:
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

    def _handle_audio(self, event: Any) -> None:
        self._set_state(AppState.SPEAKING)
        data = getattr(event, "data", None) or getattr(event, "audio", None)
        if isinstance(data, bytes):
            for cb in self._on_audio:
                cb(data)

    def _handle_audio_end(self, _event: Any) -> None:
        self._set_state(AppState.IDLE)

    def _handle_audio_interrupted(self, _event: Any) -> None:
        self._set_state(AppState.LISTENING)

    def _handle_tool_start(self, event: Any) -> None:
        self._set_state(AppState.THINKING)
        tool = getattr(event, "tool", None)
        name = getattr(tool, "name", "unknown") if tool else "unknown"
        msg = msg_tool_start(name)
        for cb in self._on_tool_event:
            cb(msg)

    def _handle_tool_end(self, event: Any) -> None:
        tool = getattr(event, "tool", None)
        name = getattr(tool, "name", "unknown") if tool else "unknown"
        output = getattr(event, "output", None)
        msg = msg_tool_end(name, str(output) if output is not None else None)
        for cb in self._on_tool_event:
            cb(msg)

    def _handle_agent_end(self, _event: Any) -> None:
        self._set_state(AppState.IDLE)

    def _handle_error(self, event: Any) -> None:
        self._set_state(AppState.ERROR)
        error = getattr(event, "error", "unknown error")
        msg = msg_error(str(error))
        for cb in self._on_error:
            cb(msg)

    def _handle_raw_model_event(self, event: Any) -> None:
        data = getattr(event, "data", None)
        if not isinstance(data, dict):
            return
        if data.get("type") == "input_audio_buffer.speech_started":
            self._set_state(AppState.LISTENING)

    # Handler dispatch table
    _handlers: ClassVar[dict[str, Callable[[EventDispatcher, Any], None]]] = {
        "audio": _handle_audio,
        "audio_end": _handle_audio_end,
        "audio_interrupted": _handle_audio_interrupted,
        "tool_start": _handle_tool_start,
        "tool_end": _handle_tool_end,
        "agent_end": _handle_agent_end,
        "error": _handle_error,
        "raw_model_event": _handle_raw_model_event,
    }
