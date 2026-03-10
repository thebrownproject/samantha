"""Entry point: start WebSocket server and initialize agents."""

import asyncio
import logging

from samantha.agents import create_voice_agent
from samantha.config import load_config
from samantha.mcp_integration import create_mcp_server
from samantha.storage import bootstrap_storage

logger = logging.getLogger(__name__)


async def _run() -> None:
    """Start the backend server and agent runtime."""
    cfg = load_config()
    bootstrap_storage(cfg)
    logging.basicConfig(level=cfg.log_level)

    mcp_server = create_mcp_server(cfg)
    mcp_servers = [mcp_server] if mcp_server else []

    if mcp_servers:
        # MCPServerStdio supports connect/cleanup lifecycle
        for server in mcp_servers:
            try:
                await server.connect()
                logger.info("MCP server connected")
            except Exception:
                logger.warning("MCP server failed to connect, continuing without it", exc_info=True)
                mcp_servers = []

    try:
        agent, _runner_config = create_voice_agent(cfg, mcp_servers=mcp_servers or None)
        logger.info("Agent '%s' initialized with model '%s'", agent.name, cfg.model_name)
        if mcp_servers:
            logger.info("MCP servers active: %d", len(mcp_servers))

        # ws_server will use agent + runner_config to start the session
        # (wired in task sam-0up.1)
    finally:
        for server in mcp_servers:
            try:
                await server.cleanup()
                logger.info("MCP server cleaned up")
            except Exception:
                logger.warning("MCP server cleanup failed", exc_info=True)


def main() -> None:
    """Synchronous entry point for the samantha CLI."""
    asyncio.run(_run())


if __name__ == "__main__":
    main()
