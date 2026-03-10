"""Realtime session recovery and reconnect strategy."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Protocol

from samantha.events import AppState, EventDispatcher

logger = logging.getLogger(__name__)

DEFAULT_MAX_RETRIES = 5
DEFAULT_BASE_DELAY = 1.0
DEFAULT_MAX_DELAY = 30.0
DEFAULT_HEALTH_INTERVAL = 30.0


class Runner(Protocol):
    """Minimal interface for a realtime runner."""

    async def run(self) -> None: ...


class SessionManager:
    """Wraps realtime session lifecycle with reconnect and health checking.

    Reconnect uses exponential backoff: base_delay * 2^attempt, capped at max_delay.
    Emits AppState.ERROR on disconnect, AppState.IDLE on recovery.
    """

    def __init__(
        self,
        dispatcher: EventDispatcher,
        *,
        max_retries: int = DEFAULT_MAX_RETRIES,
        base_delay: float = DEFAULT_BASE_DELAY,
        max_delay: float = DEFAULT_MAX_DELAY,
        health_interval: float = DEFAULT_HEALTH_INTERVAL,
    ) -> None:
        self._dispatcher = dispatcher
        self._max_retries = max_retries
        self._base_delay = base_delay
        self._max_delay = max_delay
        self._health_interval = health_interval

        self._runner: Runner | None = None
        self._run_task: asyncio.Task[None] | None = None
        self._health_task: asyncio.Task[None] | None = None
        self._is_connected = False
        self._reconnect_count = 0
        self._stopping = False
        self._health_check_count = 0

    @property
    def is_connected(self) -> bool:
        return self._is_connected

    @property
    def reconnect_count(self) -> int:
        return self._reconnect_count

    def _calc_delay(self, attempt: int) -> float:
        return min(self._base_delay * (2 ** attempt), self._max_delay)

    async def start(self, runner: Runner) -> None:
        """Start (or restart) the session with the given runner."""
        if self._run_task is not None:
            await self.stop()

        self._runner = runner
        self._stopping = False
        self._reconnect_count = 0
        self._run_task = asyncio.create_task(self._run_loop())
        self._health_task = asyncio.create_task(self._health_loop())

    async def stop(self) -> None:
        """Cleanly stop the session and all background tasks."""
        self._stopping = True
        for task in (self._run_task, self._health_task):
            if task and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        self._run_task = None
        self._health_task = None
        if self._is_connected:
            self._is_connected = False
            self._dispatcher.set_state(AppState.IDLE)

    async def _run_loop(self) -> None:
        """Run the session, reconnecting on failure up to max_retries."""
        attempt = 0
        while not self._stopping:
            try:
                self._is_connected = True
                if attempt > 0:
                    self._dispatcher.set_state(AppState.IDLE)
                    logger.info("Session reconnected (attempt %d)", attempt)
                await self._runner.run()
                # Clean exit from run() means session ended normally
                break
            except asyncio.CancelledError:
                break
            except Exception as exc:
                self._is_connected = False
                self._dispatcher.set_state(AppState.ERROR)
                logger.warning("Session disconnected: %s", exc)

                attempt += 1
                self._reconnect_count = attempt
                if attempt >= self._max_retries:
                    logger.error("Max reconnect attempts (%d) exceeded", self._max_retries)
                    break

                delay = self._calc_delay(attempt - 1)
                logger.info("Reconnecting in %.1fs (attempt %d/%d)", delay, attempt, self._max_retries)
                try:
                    await asyncio.sleep(delay)
                except asyncio.CancelledError:
                    break

    async def _health_loop(self) -> None:
        """Periodic health check while connected."""
        try:
            while not self._stopping:
                await asyncio.sleep(self._health_interval)
                if self._is_connected:
                    self._health_check_count += 1
                    logger.debug("Health check #%d: connected", self._health_check_count)
        except asyncio.CancelledError:
            pass
