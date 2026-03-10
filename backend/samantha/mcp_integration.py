"""AppleScript MCP server lifecycle and platform gating."""

from __future__ import annotations

import logging
import sys

from agents.mcp import MCPServerStdio

from samantha.config import Config

logger = logging.getLogger(__name__)

DEFAULT_MCP_COMMAND = "npx"
DEFAULT_MCP_ARGS = ["applescript-mcp"]


def is_macos() -> bool:
    return sys.platform == "darwin"


def create_mcp_server(cfg: Config) -> MCPServerStdio | None:
    """Create an MCPServerStdio for AppleScript tools, or None if unavailable.

    Returns None when MCP is disabled, platform is not macOS, or no command is configured.
    """
    if not cfg.mcp_enabled:
        logger.info("MCP disabled by config")
        return None

    if not is_macos():
        logger.info("MCP skipped: not macOS (platform=%s)", sys.platform)
        return None

    if cfg.mcp_server_command:
        parts = cfg.mcp_server_command.split()
        command, args = parts[0], parts[1:]
    else:
        command, args = DEFAULT_MCP_COMMAND, DEFAULT_MCP_ARGS

    logger.info("Creating MCP server: %s %s", command, " ".join(args))
    return MCPServerStdio(
        params={"command": command, "args": args},
        cache_tools_list=True,
    )
