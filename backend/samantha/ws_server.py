"""WebSocket server for Swift IPC (audio + control messages)."""

from __future__ import annotations

import collections
import contextlib
import json
import logging
from collections.abc import Awaitable, Callable
from enum import StrEnum
from typing import Any, ClassVar

from websockets.asyncio.server import ServerConnection, serve
from websockets.exceptions import ConnectionClosed

from samantha.config import VALID_VOICES, Config
from samantha.events import AppState, msg_error, msg_state_change
from samantha.protocol import attach_protocol_version, validate_protocol_message

logger = logging.getLogger(__name__)


class ConnectionState(StrEnum):
    DISCONNECTED = "disconnected"
    CONNECTED = "connected"


class WSServer:
    """Single-client WebSocket server for Swift IPC.

    Handles binary PCM16 audio frames and JSON control messages.
    Only one client connection at a time.
    """

    def __init__(self, config: Config) -> None:
        self.config = config
        self.state = ConnectionState.DISCONNECTED
        self.app_state = AppState.IDLE
        self.listening = False
        self._ws: ServerConnection | None = None
        self._server: Any = None

        # Session integration hooks (set by caller before start)
        self.audio_handler: Callable[[bytes], Awaitable[None]] | None = None
        self.start_listening_handler: Callable[[], Awaitable[None]] | None = None
        self.stop_listening_handler: Callable[[], Awaitable[None]] | None = None
        self.interrupt_handler: Callable[[], Awaitable[None]] | None = None
        self.inject_context_handler: Callable[[str], Awaitable[None]] | None = None
        self.voice_changed_handler: Callable[[str], Awaitable[None]] | None = None
        self.approve_tool_call_handler: Callable[[str, bool], Awaitable[None]] | None = None
        self.reject_tool_call_handler: Callable[[str, bool], Awaitable[None]] | None = None
        self.received_audio: collections.deque[bytes] = collections.deque(maxlen=4096)
        self.injected_contexts: list[str] = []
        self.interrupt_count: int = 0

    @property
    def address(self) -> tuple[str, int]:
        """Bound (host, port) after start."""
        for sock in self._server.sockets:
            return sock.getsockname()[:2]
        raise RuntimeError("Server not started")

    async def start(self) -> None:
        self._server = await serve(
            self._handler,
            self.config.ws_host,
            self.config.ws_port,
        )

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    async def send_json(self, msg: dict[str, Any]) -> None:
        if self._ws is None:
            return
        with contextlib.suppress(ConnectionClosed):
            await self._ws.send(json.dumps(attach_protocol_version(msg)))

    async def send_audio(self, data: bytes) -> None:
        if self._ws is None:
            return
        with contextlib.suppress(ConnectionClosed):
            await self._ws.send(data)

    async def publish_state(self, state: AppState) -> None:
        self.app_state = state
        await self.send_json(msg_state_change(state))

    async def _handler(self, ws: ServerConnection) -> None:
        if self._ws is not None:
            await ws.close(1013, "Only one client allowed")
            return

        self._ws = ws
        self.state = ConnectionState.CONNECTED
        self.listening = False
        logger.info("Client connected from %s", ws.remote_address)

        try:
            async for message in ws:
                if isinstance(message, bytes):
                    await self._handle_binary(message)
                else:
                    await self._handle_text(ws, message)
        except ConnectionClosed:
            pass
        finally:
            self._ws = None
            self.state = ConnectionState.DISCONNECTED
            self.listening = False
            logger.info("Client disconnected")

    async def _handle_binary(self, data: bytes) -> None:
        if not self.listening:
            return
        self.received_audio.append(data)
        await self._invoke_handler(self.audio_handler, data)

    async def _invoke_handler(
        self,
        handler: Callable[..., Awaitable[None]] | None,
        *args: Any,
    ) -> bool:
        if handler is None:
            return True
        try:
            await handler(*args)
        except Exception as exc:
            logger.warning("WebSocket handler failed", exc_info=True)
            await self.send_json(msg_error(str(exc)))
            return False
        return True

    async def _handle_text(self, ws: ServerConnection, raw: str) -> None:
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            await self._send_ws_json(ws, msg_error("Invalid JSON"))
            return

        try:
            msg = validate_protocol_message(msg)
        except ValueError as exc:
            await self._send_ws_json(ws, msg_error(str(exc)))
            return

        handler = self._text_handlers.get(msg["type"])
        if handler is None:
            await self._send_ws_json(ws, msg_error(f"Unknown message type: {msg['type']}"))
            return

        await handler(self, ws, msg)

    async def _on_start_listening(self, _ws: ServerConnection, _msg: dict) -> None:
        if not await self._invoke_handler(self.start_listening_handler):
            return
        self.listening = True
        logger.debug("Listening started")

    async def _on_stop_listening(self, _ws: ServerConnection, _msg: dict) -> None:
        self.listening = False
        await self._invoke_handler(self.stop_listening_handler)
        logger.debug("Listening stopped")

    async def _on_interrupt(self, _ws: ServerConnection, _msg: dict) -> None:
        self.interrupt_count += 1
        self.listening = False
        await self._invoke_handler(self.interrupt_handler)
        logger.debug("Interrupt requested")

    async def _on_set_voice(self, ws: ServerConnection, msg: dict) -> None:
        voice = msg.get("voice", "")
        if voice not in VALID_VOICES:
            await self._send_ws_json(ws, msg_error(f"Invalid voice: {voice!r}"))
            return
        self.config.voice = voice
        await self._invoke_handler(self.voice_changed_handler, voice)
        logger.info("Voice set to %s (takes effect on next session)", voice)

    async def _on_inject_context(self, ws: ServerConnection, msg: dict) -> None:
        text = msg.get("text")
        if not text:
            await self._send_ws_json(ws, msg_error("Missing 'text' for inject_context"))
            return
        self.injected_contexts.append(text)
        await self._invoke_handler(self.inject_context_handler, text)
        logger.debug("Context injected: %s", text[:80])

    async def _on_approve_tool_call(self, ws: ServerConnection, msg: dict) -> None:
        call_id = msg.get("call_id")
        if not isinstance(call_id, str) or not call_id:
            await self._send_ws_json(ws, msg_error("Missing 'call_id' for approve_tool_call"))
            return
        always = bool(msg.get("always", False))
        await self._invoke_handler(self.approve_tool_call_handler, call_id, always)

    async def _on_reject_tool_call(self, ws: ServerConnection, msg: dict) -> None:
        call_id = msg.get("call_id")
        if not isinstance(call_id, str) or not call_id:
            await self._send_ws_json(ws, msg_error("Missing 'call_id' for reject_tool_call"))
            return
        always = bool(msg.get("always", False))
        await self._invoke_handler(self.reject_tool_call_handler, call_id, always)

    async def _on_get_state(self, _ws: ServerConnection, _msg: dict) -> None:
        await self.send_json(msg_state_change(self.app_state))

    async def _send_ws_json(self, ws: ServerConnection, msg: dict[str, Any]) -> None:
        with contextlib.suppress(ConnectionClosed):
            await ws.send(json.dumps(attach_protocol_version(msg)))

    _text_handlers: ClassVar[dict[str, Any]] = {
        "start_listening": _on_start_listening,
        "stop_listening": _on_stop_listening,
        "interrupt": _on_interrupt,
        "set_voice": _on_set_voice,
        "inject_context": _on_inject_context,
        "approve_tool_call": _on_approve_tool_call,
        "reject_tool_call": _on_reject_tool_call,
        "get_state": _on_get_state,
    }


async def start_server(config: Config) -> WSServer:
    """Create, start, and return a WSServer instance."""
    srv = WSServer(config)
    await srv.start()
    logger.info("WebSocket server listening on %s:%d", *srv.address)
    return srv
