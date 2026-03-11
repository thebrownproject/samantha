"""Runtime bridge between the local WebSocket protocol and the Agents SDK realtime session."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import Callable
from typing import Any

from agents.realtime import RealtimeAgent, RealtimeModelSendRawMessage, RealtimeRunner, RealtimeSession

from samantha.config import Config
from samantha.events import AppState, EventDispatcher, msg_clear_playback
from samantha.interruption import InterruptionHandler
from samantha.memory import MemoryStore
from samantha.session_manager import SessionManager
from samantha.ws_server import WSServer

logger = logging.getLogger(__name__)


class RealtimeRuntime:
    """Owns the live realtime session and bridges it to the local IPC server."""

    def __init__(
        self,
        cfg: Config,
        ws: WSServer,
        *,
        agent: RealtimeAgent,
        runner_config: dict[str, Any],
        runner_factory: Callable[..., RealtimeRunner] = RealtimeRunner,
        memory_store: MemoryStore | None = None,
        session_manager: SessionManager | None = None,
        require_api_key: bool = True,
    ) -> None:
        self._cfg = cfg
        self._ws = ws
        self._runner = runner_factory(starting_agent=agent, config=runner_config)
        self._dispatcher = session_manager.dispatcher if session_manager else EventDispatcher()
        self._session_manager = session_manager or SessionManager(dispatcher=self._dispatcher)
        self._interruption = InterruptionHandler()
        self._memory_store = memory_store
        self._require_api_key = require_api_key

        self._session: RealtimeSession | None = None
        self._session_ready = asyncio.Event()
        self._ensure_lock = asyncio.Lock()
        self._started = False
        self._last_connect_error: Exception | None = None
        self._turn_has_audio = False
        self._tasks: set[asyncio.Task[Any]] = set()

        self._interruption.wire(self._dispatcher)
        self._wire_dispatcher()
        self._wire_ws_server()

    async def start(self) -> None:
        """No-op for symmetry with shutdown; session startup is lazy on first interaction."""

    async def stop(self) -> None:
        """Stop the active session and drain background send tasks."""
        self._started = False
        await self._session_manager.stop()
        self._cancel_background_tasks()

    async def wait_until_ready(self, timeout: float = 15.0) -> None:
        """Ensure the realtime session is connected before using it."""
        if self._require_api_key and not os.environ.get("OPENAI_API_KEY"):
            raise RuntimeError("OPENAI_API_KEY is not set")

        if self._session is not None:
            return

        async with self._ensure_lock:
            if self._session is None and (not self._started or not self._session_manager.is_running):
                self._session_ready.clear()
                self._started = True
                await self._session_manager.start(self)

        try:
            await asyncio.wait_for(self._session_ready.wait(), timeout=timeout)
        except TimeoutError as exc:
            detail = f": {self._last_connect_error}" if self._last_connect_error else ""
            raise RuntimeError(f"Realtime session failed to start{detail}") from self._last_connect_error or exc

    async def run(self) -> None:
        """SessionManager entrypoint. Blocks until the realtime session exits."""
        session = await self._runner.run()
        try:
            async with session:
                self._session = session
                self._last_connect_error = None
                self._session_ready.set()
                logger.info("Realtime session connected")

                async for event in session:
                    await self._handle_session_event(event)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._last_connect_error = exc
            raise
        finally:
            self._session = None
            self._session_ready.clear()
            self._turn_has_audio = False

    async def handle_start_listening(self) -> None:
        await self.wait_until_ready()
        self._turn_has_audio = False
        self._dispatcher.set_state(AppState.LISTENING)

    async def handle_stop_listening(self) -> None:
        if self._session is None or not self._turn_has_audio:
            self._turn_has_audio = False
            self._dispatcher.set_state(AppState.IDLE)
            return

        await self._send_raw_message("input_audio_buffer.commit")
        self._turn_has_audio = False
        self._dispatcher.set_state(AppState.THINKING)

    async def handle_audio_chunk(self, data: bytes) -> None:
        session = self._require_session()
        await session.send_audio(data)
        self._turn_has_audio = True

    async def handle_interrupt(self) -> None:
        session = self._session
        if session is None:
            return

        await session.interrupt()
        result = self._interruption.handle_manual_interrupt()
        if result.clear_playback:
            await self._ws.send_json(msg_clear_playback())
        if result.new_state is not None:
            self._dispatcher.set_state(result.new_state)

    async def handle_inject_context(self, text: str) -> None:
        await self.wait_until_ready()
        await self._send_raw_message(
            "conversation.item.create",
            item={
                "type": "message",
                "role": "system",
                "content": [{"type": "input_text", "text": text}],
            },
        )

    async def handle_approve_tool_call(self, call_id: str, always: bool) -> None:
        session = self._require_session()
        logger.info("Approving tool call %s (always=%s)", call_id, always)
        await session.approve_tool_call(call_id, always=always)

    async def handle_reject_tool_call(self, call_id: str, always: bool) -> None:
        session = self._require_session()
        logger.info("Rejecting tool call %s (always=%s)", call_id, always)
        await session.reject_tool_call(call_id, always=always)

    def _wire_dispatcher(self) -> None:
        self._dispatcher.on_state_change(self._on_state_change)
        self._dispatcher.on_transcript(self._on_transcript)
        self._dispatcher.on_tool_event(self._on_tool_event)
        self._dispatcher.on_tool_approval(self._on_tool_approval)
        self._dispatcher.on_audio(self._on_audio)
        self._dispatcher.on_error(self._on_error)

    def _wire_ws_server(self) -> None:
        self._ws.audio_handler = self.handle_audio_chunk
        self._ws.start_listening_handler = self.handle_start_listening
        self._ws.stop_listening_handler = self.handle_stop_listening
        self._ws.interrupt_handler = self.handle_interrupt
        self._ws.inject_context_handler = self.handle_inject_context
        self._ws.approve_tool_call_handler = self.handle_approve_tool_call
        self._ws.reject_tool_call_handler = self.handle_reject_tool_call

    async def _handle_session_event(self, event: Any) -> None:
        if self._should_clear_playback(event):
            await self._ws.send_json(msg_clear_playback())
        self._dispatcher.handle_event(event)

    def _should_clear_playback(self, event: Any) -> bool:
        if getattr(event, "type", None) != "raw_model_event":
            return False

        payload = getattr(event, "data", None)
        payload_type = getattr(payload, "type", None)
        if payload_type == "raw_server_event":
            payload = getattr(payload, "data", None)
            payload_type = payload.get("type") if isinstance(payload, dict) else getattr(payload, "type", None)

        if payload_type != "input_audio_buffer.speech_started":
            return False

        result = self._interruption.handle_speech_started()
        if result.new_state is not None:
            self._dispatcher.set_state(result.new_state)
        return result.clear_playback

    def _require_session(self) -> RealtimeSession:
        if self._session is None:
            raise RuntimeError("Realtime session is not connected")
        return self._session

    async def _send_raw_message(self, message_type: str, **other_data: Any) -> None:
        session = self._require_session()
        payload: dict[str, Any] = {"type": message_type}
        if other_data:
            payload["other_data"] = other_data
        await session.model.send_event(RealtimeModelSendRawMessage(message=payload))

    def _on_state_change(self, state: AppState) -> None:
        self._spawn(self._ws.publish_state(state))

    def _on_transcript(self, msg: dict[str, Any]) -> None:
        self._spawn(self._ws.send_json(msg))
        self._spawn(self._append_turn_log(msg))

    def _on_tool_event(self, msg: dict[str, Any]) -> None:
        self._spawn(self._ws.send_json(msg))
        self._spawn(self._append_promotion_signal(msg))

    def _on_tool_approval(self, msg: dict[str, Any]) -> None:
        logger.info(
            "Tool approval required call_id=%s tool=%s",
            msg.get("call_id", ""),
            msg.get("name", "unknown"),
        )
        self._spawn(self._ws.send_json(msg))

    def _on_audio(self, data: bytes) -> None:
        self._spawn(self._ws.send_audio(data))

    def _on_error(self, msg: dict[str, str]) -> None:
        self._spawn(self._ws.send_json(msg))

    def _spawn(self, coro: Any) -> None:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._on_task_done)

    def _on_task_done(self, task: asyncio.Task[Any]) -> None:
        self._tasks.discard(task)
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.warning("Background send task failed", exc_info=exc)

    def _cancel_background_tasks(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        self._tasks.clear()

    async def _append_turn_log(self, msg: dict[str, Any]) -> None:
        if self._memory_store is None or not msg.get("final"):
            return

        text = str(msg.get("text", "")).strip()
        role = str(msg.get("role", "")).strip()
        if not text or not role:
            return

        await self._append_daily_log_entry({
            "kind": "conversation_turn",
            "role": role,
            "text": text,
            "final": True,
        })

    async def _append_promotion_signal(self, msg: dict[str, Any]) -> None:
        if self._memory_store is None:
            return
        if msg.get("type") != "tool_start" or msg.get("name") != "memory_save":
            return

        args = msg.get("args")
        if not isinstance(args, dict):
            return

        content = str(args.get("content", "")).strip()
        tags = str(args.get("tags", "")).strip()
        if not content:
            return

        payload: dict[str, Any] = {
            "kind": "memory_promotion_signal",
            "tool": "memory_save",
            "content": content,
        }
        if tags:
            payload["tags"] = tags

        await self._append_daily_log_entry(payload)

    async def _append_daily_log_entry(self, payload: dict[str, Any]) -> None:
        if self._memory_store is None:
            return
        try:
            await self._memory_store.append_daily_log(
                json.dumps(payload, separators=(",", ":"), sort_keys=True),
            )
        except Exception:
            logger.warning("Failed to append daily log entry", exc_info=True)
