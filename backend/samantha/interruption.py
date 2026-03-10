"""Interruption handling for automatic barge-in and manual interrupt."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from samantha.events import AppState

if TYPE_CHECKING:
    from samantha.events import EventDispatcher


@dataclass(frozen=True, slots=True)
class InterruptResult:
    clear_playback: bool
    new_state: AppState | None = None


_NOOP = InterruptResult(clear_playback=False)


class InterruptionHandler:
    """Tracks speaking state and handles interruption requests.

    Wire to an EventDispatcher via `wire()` to auto-track state.
    Call `handle_speech_started()` for VAD barge-in or
    `handle_manual_interrupt()` for user-initiated stop.
    """

    def __init__(self) -> None:
        self._current_state: AppState = AppState.IDLE

    @property
    def is_interruptible(self) -> bool:
        return self._current_state == AppState.SPEAKING

    def on_state_changed(self, state: AppState) -> None:
        self._current_state = state

    def handle_speech_started(self) -> InterruptResult:
        if not self.is_interruptible:
            return _NOOP
        return InterruptResult(clear_playback=True, new_state=AppState.LISTENING)

    def handle_manual_interrupt(self) -> InterruptResult:
        if not self.is_interruptible:
            return _NOOP
        return InterruptResult(clear_playback=True, new_state=AppState.LISTENING)

    def wire(self, dispatcher: EventDispatcher) -> None:
        dispatcher.on_state_change(self.on_state_changed)
