"""Settings management (~/.samantha/config.json)."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

VALID_VOICES = {"alloy", "ash", "ballad", "coral", "echo", "sage", "shimmer", "verse"}
VALID_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


@dataclass
class Config:
    safe_mode: bool = True
    confirm_destructive: bool = True
    memory_enabled: bool = True
    model_name: str = "gpt-4o-realtime-preview"
    reasoning_model: str = "gpt-5-mini-2025-08-07"
    transcription_model: str = "gpt-4o-mini-transcribe"
    voice: str = "ash"
    turn_detection_type: str = "semantic_vad"
    interrupt_response: bool = True
    ws_host: str = "localhost"
    ws_port: int = 9090
    data_dir: Path = field(default_factory=lambda: Path.home() / ".samantha")
    bash_allowlist: list[str] = field(default_factory=list)
    log_level: str = "INFO"

    def __post_init__(self):
        if isinstance(self.data_dir, str):
            self.data_dir = Path(self.data_dir)
        self.validate()

    def validate(self):
        if not 1 <= self.ws_port <= 65535:
            raise ValueError(f"ws_port must be 1-65535, got {self.ws_port}")
        if self.voice not in VALID_VOICES:
            raise ValueError(f"voice must be one of {sorted(VALID_VOICES)}, got {self.voice!r}")
        if self.log_level not in VALID_LOG_LEVELS:
            raise ValueError(f"log_level must be one of {sorted(VALID_LOG_LEVELS)}, got {self.log_level!r}")
        if not isinstance(self.bash_allowlist, list):
            raise ValueError("bash_allowlist must be a list")

    @property
    def config_path(self) -> Path:
        return self.data_dir / "config.json"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["data_dir"] = str(d["data_dir"])
        return d


def ensure_data_dir(cfg: Config) -> None:
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    (cfg.data_dir / "daily").mkdir(exist_ok=True)


def load_config(path: Path | None = None) -> Config:
    config_path = path or (Path.home() / ".samantha" / "config.json")
    if config_path.exists():
        raw = json.loads(config_path.read_text())
        return Config(**raw)
    return Config()


def save_config(cfg: Config) -> None:
    ensure_data_dir(cfg)
    cfg.config_path.write_text(json.dumps(cfg.to_dict(), indent=2) + "\n")
