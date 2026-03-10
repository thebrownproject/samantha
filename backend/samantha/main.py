"""Entry point: start WebSocket server and initialize agents."""

import asyncio
import logging

from samantha.agents import create_voice_agent
from samantha.config import load_config
from samantha.storage import bootstrap_storage

logger = logging.getLogger(__name__)


async def _run() -> None:
    """Start the backend server and agent runtime."""
    cfg = load_config()
    bootstrap_storage(cfg)
    logging.basicConfig(level=cfg.log_level)

    agent, runner_config = create_voice_agent(cfg)
    logger.info("Agent '%s' initialized with model '%s'", agent.name, cfg.model_name)

    # ws_server will use agent + runner_config to start the session
    # (wired in task sam-0up.1)


def main() -> None:
    """Synchronous entry point for the samantha CLI."""
    asyncio.run(_run())


if __name__ == "__main__":
    main()
