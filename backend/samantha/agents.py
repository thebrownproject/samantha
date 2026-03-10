"""RealtimeAgent definitions and delegation behavior."""

from __future__ import annotations

from agents.realtime import RealtimeAgent

from samantha.config import Config
from samantha.prompts import SYSTEM_PROMPT
from samantha.tools import register_tools


def build_runner_config(cfg: Config) -> dict:
    """Build the RealtimeRunner config dict from a Config dataclass."""
    return {
        "model_settings": {
            "model_name": cfg.model_name,
            "audio": {
                "input": {
                    "format": "pcm16",
                    "transcription": {"model": cfg.transcription_model},
                    "turn_detection": {
                        "type": cfg.turn_detection_type,
                        "interrupt_response": cfg.interrupt_response,
                    },
                },
                "output": {"format": "pcm16", "voice": cfg.voice},
            },
        },
        "async_tool_calls": True,
    }


def create_voice_agent(cfg: Config | None = None) -> tuple[RealtimeAgent, dict]:
    """Create the primary voice agent and its runner config.

    Returns (agent, runner_config) for use by ws_server.
    """
    if cfg is None:
        cfg = Config()

    agent = RealtimeAgent(
        name="samantha",
        instructions=SYSTEM_PROMPT,
        tools=register_tools(cfg),
    )

    return agent, build_runner_config(cfg)
