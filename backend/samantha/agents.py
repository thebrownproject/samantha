"""RealtimeAgent definitions and delegation behavior."""

from __future__ import annotations

from agents.mcp import MCPServerStdio
from agents.realtime import RealtimeAgent

from samantha.config import Config
from samantha.prompts import SYSTEM_PROMPT
from samantha.tools import register_tools

AUDIO_FORMAT = "pcm16"


def build_runner_config(cfg: Config) -> dict:
    """Build the RealtimeRunner config dict from a Config dataclass."""
    return {
        "model_settings": {
            "model_name": cfg.model_name,
            "tool_choice": "auto",
            "audio": {
                "input": {
                    "format": AUDIO_FORMAT,
                    "transcription": {"model": cfg.transcription_model},
                    "turn_detection": {
                        "type": cfg.turn_detection_type,
                        "interrupt_response": cfg.interrupt_response,
                    },
                },
                "output": {"format": AUDIO_FORMAT, "voice": cfg.voice},
            },
        },
        "async_tool_calls": True,
    }


def create_voice_agent(
    cfg: Config | None = None,
    mcp_servers: list[MCPServerStdio] | None = None,
) -> tuple[RealtimeAgent, dict]:
    """Create the primary voice agent and its runner config.

    Returns (agent, runner_config) for use by ws_server.
    """
    if cfg is None:
        cfg = Config()

    kwargs: dict = {
        "name": "samantha",
        "instructions": SYSTEM_PROMPT,
        "tools": register_tools(cfg),
    }
    if mcp_servers:
        kwargs["mcp_servers"] = mcp_servers

    agent = RealtimeAgent(**kwargs)
    return agent, build_runner_config(cfg)
