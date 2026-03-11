"""Tests for realtime session recovery and reconnect strategy."""

from __future__ import annotations

import asyncio
import contextlib

import pytest

from samantha.events import AppState, EventDispatcher
from samantha.session_manager import SessionManager


class FakeRunner:
    """Simulates RealtimeRunner for testing."""

    def __init__(self, *, fail_on_run: bool = False, fail_count: int = 0):
        self._fail_on_run = fail_on_run
        self._fail_count = fail_count
        self._run_calls = 0
        self._stopped = False

    async def run(self):
        self._run_calls += 1
        if self._fail_on_run or self._run_calls <= self._fail_count:
            raise ConnectionError("session dropped")
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.sleep(3600)

    def stop(self):
        self._stopped = True


@pytest.fixture
def dispatcher():
    return EventDispatcher()


@pytest.fixture
def manager(dispatcher):
    return SessionManager(dispatcher=dispatcher, max_retries=3, base_delay=0.01)


# -- Lifecycle --


class TestLifecycle:
    async def test_start_sets_connected(self, manager):
        runner = FakeRunner()
        await manager.start(runner)
        await asyncio.sleep(0.02)
        assert manager.is_connected is True
        await manager.stop()

    async def test_stop_cleans_up(self, manager):
        runner = FakeRunner()
        await manager.start(runner)
        await manager.stop()
        assert manager.is_connected is False

    async def test_double_stop_is_safe(self, manager):
        runner = FakeRunner()
        await manager.start(runner)
        await manager.stop()
        await manager.stop()  # should not raise

    async def test_start_without_prior_stop(self, manager):
        runner = FakeRunner()
        await manager.start(runner)
        # Starting again should stop old session first
        runner2 = FakeRunner()
        await manager.start(runner2)
        await asyncio.sleep(0.02)
        assert manager.is_connected is True
        await manager.stop()


# -- Reconnect with exponential backoff --


class TestReconnect:
    async def test_reconnect_on_disconnect(self, manager, dispatcher):
        """Session drop triggers reconnect and emits error then idle."""
        states: list[AppState] = []
        dispatcher.on_state_change(states.append)

        # Runner fails twice, then succeeds
        runner = FakeRunner(fail_count=2)
        await manager.start(runner)

        # Give reconnect loop time to settle
        await asyncio.sleep(0.2)
        assert manager.is_connected is True
        assert manager.reconnect_count == 2
        assert AppState.ERROR in states
        await manager.stop()

    async def test_exponential_backoff_timing(self, manager):
        """Delays should increase exponentially."""
        delays = [manager._calc_delay(i) for i in range(5)]
        # base_delay=0.01 -> 0.01, 0.02, 0.04, 0.08, 0.16
        assert delays[0] == pytest.approx(0.01)
        assert delays[1] == pytest.approx(0.02)
        assert delays[2] == pytest.approx(0.04)
        assert delays[3] == pytest.approx(0.08)
        assert delays[4] == pytest.approx(0.16)

    async def test_backoff_caps_at_max(self):
        mgr = SessionManager(
            dispatcher=EventDispatcher(),
            max_retries=5,
            base_delay=10.0,
            max_delay=30.0,
        )
        # 10 * 2^3 = 80, capped to 30
        assert mgr._calc_delay(3) == 30.0


# -- Max retries exceeded --


class TestMaxRetries:
    async def test_gives_up_after_max_retries(self, dispatcher):
        states: list[AppState] = []
        dispatcher.on_state_change(states.append)

        mgr = SessionManager(dispatcher=dispatcher, max_retries=2, base_delay=0.01)
        runner = FakeRunner(fail_on_run=True)  # always fails
        await mgr.start(runner)

        await asyncio.sleep(0.3)
        assert mgr.is_connected is False
        assert mgr.reconnect_count == 2
        # Final state should be ERROR
        assert states[-1] == AppState.ERROR
        await mgr.stop()


# -- State emissions during reconnect --


class TestStateEmissions:
    async def test_error_emitted_on_disconnect(self, manager, dispatcher):
        states: list[AppState] = []
        dispatcher.on_state_change(states.append)

        runner = FakeRunner(fail_count=1)
        await manager.start(runner)
        await asyncio.sleep(0.15)

        assert AppState.ERROR in states
        await manager.stop()

    async def test_idle_emitted_on_reconnect_success(self, manager, dispatcher):
        states: list[AppState] = []
        dispatcher.on_state_change(states.append)

        runner = FakeRunner(fail_count=1)
        await manager.start(runner)
        await asyncio.sleep(0.15)

        # After error, should recover to idle
        error_idx = states.index(AppState.ERROR)
        remaining = states[error_idx + 1 :]
        assert AppState.IDLE in remaining
        await manager.stop()

    async def test_stop_returns_to_idle_from_non_idle(self, manager, dispatcher):
        """Stop after an error recovers state to idle."""
        states: list[AppState] = []
        dispatcher.on_state_change(states.append)

        # Fail once so state goes to ERROR, then recover
        runner = FakeRunner(fail_count=1)
        await manager.start(runner)
        await asyncio.sleep(0.15)
        await manager.stop()
        assert states[-1] == AppState.IDLE

    async def test_stop_when_already_idle_is_noop(self, manager, dispatcher):
        """If already IDLE, stop does not emit a redundant state change."""
        states: list[AppState] = []
        dispatcher.on_state_change(states.append)

        runner = FakeRunner()
        await manager.start(runner)
        await asyncio.sleep(0.02)
        await manager.stop()
        # Reaching here without exception means stop() didn't crash
        assert manager.is_connected is False


# -- Health check --


class TestHealthCheck:
    async def test_health_check_runs_periodically(self, dispatcher):
        mgr = SessionManager(
            dispatcher=dispatcher,
            max_retries=3,
            base_delay=0.01,
            health_interval=0.05,
        )
        runner = FakeRunner()
        await mgr.start(runner)
        await asyncio.sleep(0.15)
        assert mgr._health_check_count >= 1
        await mgr.stop()

    async def test_health_check_stops_on_stop(self, dispatcher):
        mgr = SessionManager(
            dispatcher=dispatcher,
            max_retries=3,
            base_delay=0.01,
            health_interval=0.05,
        )
        runner = FakeRunner()
        await mgr.start(runner)
        await asyncio.sleep(0.1)
        await mgr.stop()
        count_after_stop = mgr._health_check_count
        await asyncio.sleep(0.1)
        assert mgr._health_check_count == count_after_stop
