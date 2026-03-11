"""Settings management (~/.samantha/config.json)."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

VALID_VOICES = {
    "alloy",
    "ash",
    "ballad",
    "cedar",
    "coral",
    "echo",
    "fable",
    "marin",
    "nova",
    "onyx",
    "sage",
    "shimmer",
    "verse",
}
VALID_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
VALID_TURN_DETECTION_TYPES = {"semantic_vad", "server_vad"}


@dataclass
class Config:
    safe_mode: bool = True
    confirm_destructive: bool = True
    memory_enabled: bool = True
    model_name: str = "gpt-realtime"
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
    delegation_timeout: int = 30
    delegation_max_retries: int = 1
    mcp_enabled: bool = True
    mcp_server_command: str = ""

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
        if self.turn_detection_type not in VALID_TURN_DETECTION_TYPES:
            raise ValueError(
                f"turn_detection_type must be one of {sorted(VALID_TURN_DETECTION_TYPES)}, "
                f"got {self.turn_detection_type!r}"
            )
        if not isinstance(self.bash_allowlist, list):
            raise ValueError("bash_allowlist must be a list")
        if self.delegation_timeout <= 0:
            raise ValueError(f"delegation_timeout must be positive, got {self.delegation_timeout}")
        if self.delegation_max_retries < 0:
            raise ValueError(f"delegation_max_retries must be >= 0, got {self.delegation_max_retries}")

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
    config_path = path or Config().config_path
    if config_path.exists():
        raw = json.loads(config_path.read_text())
        known = {f.name for f in fields(Config)}
        return Config(**{k: v for k, v in raw.items() if k in known})
    return Config()


def save_config(cfg: Config) -> None:
    ensure_data_dir(cfg)
    cfg.config_path.write_text(json.dumps(cfg.to_dict(), indent=2) + "\n")
