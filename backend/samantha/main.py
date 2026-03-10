"""Entry point: start WebSocket server and initialize agents."""

import asyncio
import logging
import signal

from samantha.agents import create_voice_agent
from samantha.config import load_config
from samantha.mcp_integration import create_mcp_server
from samantha.memory import MemoryStore
from samantha.storage import bootstrap_storage
from samantha.tools import configure_memory
from samantha.ws_server import start_server

logger = logging.getLogger(__name__)


async def _run() -> None:
    """Start the backend server and agent runtime."""
    cfg = load_config()
    logging.basicConfig(level=cfg.log_level)
    bootstrap_storage(cfg)

    # Initialize memory
    memory = MemoryStore(db_path=cfg.data_dir / "memory.db")
    if cfg.memory_enabled:
        await memory.initialize()
        configure_memory(memory)
        logger.info("Memory initialized at %s", memory.db_path)

    # MCP setup (macOS only)
    mcp_server = create_mcp_server(cfg)
    mcp_servers = [mcp_server] if mcp_server else []
    for server in mcp_servers:
        try:
            await server.connect()
            logger.info("MCP server connected")
        except Exception:
            logger.warning("MCP server failed to connect, continuing without it", exc_info=True)
            mcp_servers = []

    # Create agent
    agent, _runner_config = create_voice_agent(cfg, mcp_servers=mcp_servers or None)
    logger.info("Agent '%s' ready (model=%s, voice=%s)", agent.name, cfg.model_name, cfg.voice)

    # Start WebSocket server
    ws = await start_server(cfg)
    logger.info("Samantha backend running on ws://%s:%d", cfg.ws_host, cfg.ws_port)

    # Wait for shutdown signal
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    try:
        await stop.wait()
    finally:
        logger.info("Shutting down...")
        await ws.stop()
        if cfg.memory_enabled:
            await memory.close()
        for server in mcp_servers:
            try:
                await server.cleanup()
            except Exception:
                logger.warning("MCP cleanup failed", exc_info=True)
        logger.info("Shutdown complete")


def main() -> None:
    """Synchronous entry point for the samantha CLI."""
    asyncio.run(_run())


if __name__ == "__main__":
    main()
