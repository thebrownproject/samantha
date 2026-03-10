"""WebSocket server for Swift IPC (audio + control messages)."""

from __future__ import annotations

DEFAULT_HOST = "localhost"
DEFAULT_PORT = 9090


async def start_server(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> None:
    """Start the WebSocket server for Swift client connections."""
    # from websockets.asyncio.server import serve
    ...
