"""Tests for config module -- schema, defaults, validation, persistence."""

import json
from pathlib import Path

import pytest

from samantha.config import Config, ensure_data_dir, load_config, save_config


def test_default_values():
    cfg = Config()
    assert cfg.safe_mode is True
    assert cfg.confirm_destructive is True
    assert cfg.memory_enabled is True
    assert cfg.model_name == "gpt-realtime"
    assert cfg.reasoning_model == "gpt-5-mini-2025-08-07"
    assert cfg.transcription_model == "gpt-4o-mini-transcribe"
    assert cfg.voice == "ash"
    assert cfg.turn_detection_type == "semantic_vad"
    assert cfg.interrupt_response is True
    assert cfg.ws_host == "localhost"
    assert cfg.ws_port == 9090
    assert cfg.bash_allowlist == []
    assert cfg.log_level == "INFO"
    assert cfg.data_dir.name == ".samantha"


def test_partial_config_merge(tmp_path):
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps({"voice": "coral", "ws_port": 8080}))
    cfg = load_config(config_file)
    assert cfg.voice == "coral"
    assert cfg.ws_port == 8080
    assert cfg.safe_mode is True
    assert cfg.model_name == "gpt-realtime"


def test_load_missing_file_returns_defaults(tmp_path):
    cfg = load_config(tmp_path / "nonexistent.json")
    assert cfg.voice == "ash"
    assert cfg.ws_port == 9090


def test_invalid_port():
    with pytest.raises(ValueError, match="ws_port"):
        Config(ws_port=0)
    with pytest.raises(ValueError, match="ws_port"):
        Config(ws_port=70000)


def test_invalid_turn_detection_type():
    with pytest.raises(ValueError, match="turn_detection_type"):
        Config(turn_detection_type="invalid_vad")


def test_invalid_voice():
    with pytest.raises(ValueError, match="voice"):
        Config(voice="nonexistent")


def test_invalid_log_level():
    with pytest.raises(ValueError, match="log_level"):
        Config(log_level="VERBOSE")


def test_invalid_bash_allowlist():
    with pytest.raises(ValueError, match="bash_allowlist"):
        Config(bash_allowlist="not-a-list")


def test_round_trip(tmp_path):
    data_dir = tmp_path / ".samantha"
    cfg = Config(voice="coral", ws_port=8080, data_dir=data_dir)
    save_config(cfg)
    loaded = load_config(cfg.config_path)
    assert loaded.voice == "coral"
    assert loaded.ws_port == 8080
    assert loaded.safe_mode is True


def test_data_dir_from_string():
    cfg = Config(data_dir="/tmp/test_samantha")
    assert isinstance(cfg.data_dir, Path)
    assert str(cfg.data_dir) == "/tmp/test_samantha"


def test_ensure_data_dir(tmp_path):
    data_dir = tmp_path / ".samantha"
    cfg = Config(data_dir=data_dir)
    ensure_data_dir(cfg)
    assert data_dir.exists()
    assert (data_dir / "daily").exists()


def test_to_dict():
    cfg = Config()
    d = cfg.to_dict()
    assert isinstance(d["data_dir"], str)
    assert d["voice"] == "ash"
    assert d["ws_port"] == 9090


def test_config_path_property(tmp_path):
    cfg = Config(data_dir=tmp_path)
    assert cfg.config_path == tmp_path / "config.json"
