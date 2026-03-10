"""Tests for MCP integration -- platform gating, config, agent wiring."""

from unittest.mock import patch

from samantha.config import Config
from samantha.mcp_integration import create_mcp_server


def test_mcp_skipped_on_non_macos():
    """On Linux/WSL2, create_mcp_server returns None."""
    cfg = Config()
    with patch("samantha.mcp_integration.is_macos", return_value=False):
        result = create_mcp_server(cfg)
    assert result is None


def test_mcp_disabled_by_config():
    cfg = Config(mcp_enabled=False)
    with patch("samantha.mcp_integration.is_macos", return_value=True):
        result = create_mcp_server(cfg)
    assert result is None


def test_mcp_created_on_macos():
    cfg = Config()
    with patch("samantha.mcp_integration.is_macos", return_value=True):
        server = create_mcp_server(cfg)
    assert server is not None
    assert server.params.command == "npx"
    assert server.params.args == ["applescript-mcp"]


def test_mcp_custom_command():
    cfg = Config(mcp_server_command="/usr/local/bin/my-mcp --flag")
    with patch("samantha.mcp_integration.is_macos", return_value=True):
        server = create_mcp_server(cfg)
    assert server is not None
    assert server.params.command == "/usr/local/bin/my-mcp"
    assert server.params.args == ["--flag"]


def test_config_mcp_defaults():
    cfg = Config()
    assert cfg.mcp_enabled is True
    assert cfg.mcp_server_command == ""


def test_config_mcp_overrides():
    cfg = Config(mcp_enabled=False, mcp_server_command="custom-cmd arg1")
    assert cfg.mcp_enabled is False
    assert cfg.mcp_server_command == "custom-cmd arg1"


def test_agent_created_without_mcp():
    from samantha.agents import create_voice_agent

    agent, _ = create_voice_agent()
    assert agent.mcp_servers == []


def test_agent_created_with_mcp_servers():
    from unittest.mock import MagicMock

    from samantha.agents import create_voice_agent

    mock_server = MagicMock()
    agent, _ = create_voice_agent(mcp_servers=[mock_server])
    assert agent.mcp_servers == [mock_server]


def test_is_macos_returns_bool():
    from samantha.mcp_integration import is_macos

    # We're on Linux, so this should be False
    assert is_macos() is False
