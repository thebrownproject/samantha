"""Tests for agents module -- agent creation and runner config building."""

import pytest

from samantha.config import Config
from samantha.prompts import SYSTEM_PROMPT


@pytest.fixture
def cfg():
    return Config()


@pytest.fixture
def custom_cfg():
    return Config(
        model_name="gpt-realtime",
        voice="coral",
        turn_detection_type="server_vad",
        interrupt_response=False,
        transcription_model="whisper-1",
    )


def test_build_runner_config_defaults(cfg):
    from samantha.agents import build_runner_config

    rc = build_runner_config(cfg)
    ms = rc["model_settings"]
    assert ms["model_name"] == "gpt-4o-realtime-preview"
    assert ms["audio"]["input"]["format"] == "pcm16"
    assert ms["audio"]["input"]["transcription"]["model"] == "gpt-4o-mini-transcribe"
    assert ms["audio"]["input"]["turn_detection"]["type"] == "semantic_vad"
    assert ms["audio"]["input"]["turn_detection"]["interrupt_response"] is True
    assert ms["audio"]["output"]["format"] == "pcm16"
    assert ms["audio"]["output"]["voice"] == "ash"
    assert rc["async_tool_calls"] is True


def test_build_runner_config_custom(custom_cfg):
    from samantha.agents import build_runner_config

    rc = build_runner_config(custom_cfg)
    ms = rc["model_settings"]
    assert ms["model_name"] == "gpt-realtime"
    assert ms["audio"]["input"]["transcription"]["model"] == "whisper-1"
    assert ms["audio"]["input"]["turn_detection"]["type"] == "server_vad"
    assert ms["audio"]["input"]["turn_detection"]["interrupt_response"] is False
    assert ms["audio"]["output"]["voice"] == "coral"


def test_create_voice_agent_returns_tuple():
    from samantha.agents import create_voice_agent

    result = create_voice_agent()
    assert isinstance(result, tuple)
    assert len(result) == 2


def test_create_voice_agent_agent_properties():
    from samantha.agents import create_voice_agent

    agent, _ = create_voice_agent()
    assert agent.name == "samantha"
    assert agent.instructions == SYSTEM_PROMPT


def test_create_voice_agent_with_config(custom_cfg):
    from samantha.agents import create_voice_agent

    agent, rc = create_voice_agent(custom_cfg)
    assert agent.name == "samantha"
    assert rc["model_settings"]["model_name"] == "gpt-realtime"
    assert rc["model_settings"]["audio"]["output"]["voice"] == "coral"


def test_create_voice_agent_has_tools():
    from samantha.agents import create_voice_agent

    agent, _ = create_voice_agent()
    tool_names = [t.name for t in agent.tools]
    assert "safe_bash" in tool_names
    assert "file_read" in tool_names
    assert "file_write" in tool_names


def test_create_voice_agent_passes_config_to_tools():
    from samantha import tools
    from samantha.agents import create_voice_agent

    cfg = Config(safe_mode=False, bash_allowlist=["ls", "echo"])
    create_voice_agent(cfg)
    assert tools._cfg is cfg
    assert tools._cfg.safe_mode is False
    assert tools._cfg.bash_allowlist == ["ls", "echo"]


def test_create_voice_agent_default_config_propagates():
    from samantha import tools
    from samantha.agents import create_voice_agent

    create_voice_agent()
    assert tools._cfg.safe_mode is True
    assert tools._cfg.bash_allowlist == []
