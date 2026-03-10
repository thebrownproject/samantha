"""Settings management (~/.samantha/config.json)."""

from __future__ import annotations

import json
from pathlib import Path

CONFIG_DIR = Path.home() / ".samantha"
CONFIG_PATH = CONFIG_DIR / "config.json"

DEFAULT_CONFIG: dict = {
    "voice": "ash",
    "model": "gpt-realtime",
    "reasoning_model": "gpt-5-mini-2025-08-07",
    "ws_port": 9090,
}


def load_config() -> dict:
    """Load config from disk, returning defaults if file doesn't exist."""
    if CONFIG_PATH.exists():
        return json.loads(CONFIG_PATH.read_text())
    return dict(DEFAULT_CONFIG)


def save_config(config: dict) -> None:
    """Write config to disk."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(config, indent=2))
