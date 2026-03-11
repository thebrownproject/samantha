"""Tests for interruption handling (automatic barge-in and manual interrupt)."""

from types import SimpleNamespace

import pytest

from samantha.events import AppState, EventDispatcher, msg_clear_playback
from samantha.interruption import InterruptionHandler
from samantha.protocol import protocol_message


def _evt(type: str, **kwargs) -> SimpleNamespace:
    return SimpleNamespace(type=type, **kwargs)


@pytest.fixture
def handler():
    return InterruptionHandler()


@pytest.fixture
def wired_dispatcher():
    """EventDispatcher with InterruptionHandler wired in."""
    d = EventDispatcher()
    h = InterruptionHandler()
    h.wire(d)
    return d, h


# -- InterruptionHandler standalone --


class TestInterruptionHandler:
    def test_not_interruptible_when_idle(self, handler):
        assert handler.is_interruptible is False

    def test_interruptible_when_speaking(self, handler):
        handler.on_state_changed(AppState.SPEAKING)
        assert handler.is_interruptible is True

    def test_not_interruptible_when_listening(self, handler):
        handler.on_state_changed(AppState.LISTENING)
        assert handler.is_interruptible is False

    def test_not_interruptible_when_thinking(self, handler):
        handler.on_state_changed(AppState.THINKING)
        assert handler.is_interruptible is False

    def test_speech_started_during_speaking(self, handler):
        handler.on_state_changed(AppState.SPEAKING)
        result = handler.handle_speech_started()
        assert result.clear_playback is True
        assert result.new_state == AppState.LISTENING

    def test_speech_started_when_not_speaking(self, handler):
        result = handler.handle_speech_started()
        assert result.clear_playback is False

    def test_manual_interrupt_during_speaking(self, handler):
        handler.on_state_changed(AppState.SPEAKING)
        result = handler.handle_manual_interrupt()
        assert result.clear_playback is True
        assert result.new_state == AppState.LISTENING

    def test_manual_interrupt_when_not_speaking(self, handler):
        result = handler.handle_manual_interrupt()
        assert result.clear_playback is False

    def test_state_tracks_transitions(self, handler):
        handler.on_state_changed(AppState.SPEAKING)
        assert handler.is_interruptible is True
        handler.on_state_changed(AppState.IDLE)
        assert handler.is_interruptible is False


# -- msg_clear_playback --


def test_msg_clear_playback():
    assert msg_clear_playback() == protocol_message("clear_playback")


# -- Integration with EventDispatcher --


class TestInterruptionIntegration:
    def test_auto_barge_in_during_speaking(self, wired_dispatcher):
        d, h = wired_dispatcher

        # Start speaking
        d.handle_event(_evt("audio", data=b"\x00"))
        assert d.state == AppState.SPEAKING
        assert h.is_interruptible is True

        # VAD detects speech -> triggers interruption
        d.handle_event(_evt("raw_model_event", data={"type": "input_audio_buffer.speech_started"}))
        assert d.state == AppState.LISTENING
        assert h.is_interruptible is False

    def test_manual_interrupt_during_speaking(self, wired_dispatcher):
        d, h = wired_dispatcher
        d.handle_event(_evt("audio", data=b"\x00"))
        assert d.state == AppState.SPEAKING

        result = h.handle_manual_interrupt()
        assert result.clear_playback is True

    def test_interrupt_when_not_speaking_is_noop(self, wired_dispatcher):
        d, h = wired_dispatcher
        assert d.state == AppState.IDLE

        result = h.handle_manual_interrupt()
        assert result.clear_playback is False

    def test_state_sequence_through_interruption(self, wired_dispatcher):
        """Full flow: idle -> speaking -> interrupted -> listening -> speaking -> idle."""
        d, _h = wired_dispatcher
        states = []
        d.on_state_change(states.append)

        d.handle_event(_evt("audio", data=b"\x00"))
        d.handle_event(_evt("audio_interrupted"))
        d.handle_event(_evt("audio", data=b"\x01"))
        d.handle_event(_evt("audio_end"))

        assert states == [
            AppState.SPEAKING,
            AppState.LISTENING,
            AppState.SPEAKING,
            AppState.IDLE,
        ]

    def test_clear_playback_signal_emitted_on_speech_started(self, wired_dispatcher):
        """When speech_started fires during SPEAKING, the handler returns clear_playback."""
        d, h = wired_dispatcher
        d.handle_event(_evt("audio", data=b"\x00"))
        result = h.handle_speech_started()
        assert result.clear_playback is True
        assert result.new_state == AppState.LISTENING

    def test_clear_playback_not_emitted_when_idle(self, wired_dispatcher):
        _d, h = wired_dispatcher
        result = h.handle_speech_started()
        assert result.clear_playback is False
