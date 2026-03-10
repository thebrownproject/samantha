"""WebSocket server for Swift IPC (audio + control messages)."""

from __future__ import annotations

import collections
import contextlib
import json
import logging
from enum import StrEnum
from typing import Any, ClassVar

from websockets.asyncio.server import ServerConnection, serve
from websockets.exceptions import ConnectionClosed

from samantha.config import VALID_VOICES, Config
from samantha.events import msg_error

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
        self.listening = False
        self._ws: ServerConnection | None = None
        self._server: Any = None

        # Session integration hooks (set by caller before start)
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
            await self._ws.send(json.dumps(msg))

    async def send_audio(self, data: bytes) -> None:
        if self._ws is None:
            return
        with contextlib.suppress(ConnectionClosed):
            await self._ws.send(data)

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
                    self._handle_binary(message)
                else:
                    await self._handle_text(ws, message)
        except ConnectionClosed:
            pass
        finally:
            self._ws = None
            self.state = ConnectionState.DISCONNECTED
            self.listening = False
            logger.info("Client disconnected")

    def _handle_binary(self, data: bytes) -> None:
        if not self.listening:
            return
        self.received_audio.append(data)

    async def _handle_text(self, ws: ServerConnection, raw: str) -> None:
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            await ws.send(json.dumps(msg_error("Invalid JSON")))
            return

        msg_type = msg.get("type")
        if msg_type is None:
            await ws.send(json.dumps(msg_error("Missing 'type' field")))
            return

        handler = self._text_handlers.get(msg_type)
        if handler is None:
            await ws.send(json.dumps(msg_error(f"Unknown message type: {msg_type}")))
            return

        await handler(self, ws, msg)

    async def _on_start_listening(self, _ws: ServerConnection, _msg: dict) -> None:
        self.listening = True
        logger.debug("Listening started")

    async def _on_stop_listening(self, _ws: ServerConnection, _msg: dict) -> None:
        self.listening = False
        logger.debug("Listening stopped")

    async def _on_interrupt(self, _ws: ServerConnection, _msg: dict) -> None:
        self.interrupt_count += 1
        self.listening = False
        logger.debug("Interrupt requested")

    async def _on_set_voice(self, ws: ServerConnection, msg: dict) -> None:
        voice = msg.get("voice", "")
        if voice not in VALID_VOICES:
            await ws.send(json.dumps(msg_error(f"Invalid voice: {voice!r}")))
            return
        self.config.voice = voice
        logger.info("Voice set to %s (takes effect on next session)", voice)

    async def _on_inject_context(self, ws: ServerConnection, msg: dict) -> None:
        text = msg.get("text")
        if not text:
            await ws.send(json.dumps(msg_error("Missing 'text' for inject_context")))
            return
        self.injected_contexts.append(text)
        logger.debug("Context injected: %s", text[:80])

    _text_handlers: ClassVar[dict[str, Any]] = {
        "start_listening": _on_start_listening,
        "stop_listening": _on_stop_listening,
        "interrupt": _on_interrupt,
        "set_voice": _on_set_voice,
        "inject_context": _on_inject_context,
    }


async def start_server(config: Config) -> WSServer:
    """Create, start, and return a WSServer instance."""
    srv = WSServer(config)
    await srv.start()
    logger.info("WebSocket server listening on %s:%d", *srv.address)
    return srv
