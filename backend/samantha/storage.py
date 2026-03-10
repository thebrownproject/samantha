"""Storage bootstrap -- creates ~/.samantha layout on first run."""

from __future__ import annotations

from pathlib import Path

from samantha.config import Config, ensure_data_dir

PROFILE_TEMPLATE = """\
# Profile

Name:
Role:
Location:
Notes:
"""

PREFERENCES_TEMPLATE = """\
# Preferences

Communication style:
Topics of interest:
"""


def bootstrap_storage(cfg: Config) -> None:
    """Create the full storage layout. Idempotent -- safe to call on every startup.

    Raises OSError/PermissionError on filesystem failures so startup can halt.
    """
    ensure_data_dir(cfg)

    _write_template(cfg.data_dir / "profile.md", PROFILE_TEMPLATE)
    _write_template(cfg.data_dir / "preferences.md", PREFERENCES_TEMPLATE)

    _verify_layout(cfg.data_dir)


def _write_template(path: Path, content: str) -> None:
    """Write template file only if it does not already exist."""
    if not path.exists():
        path.write_text(content)


def _verify_layout(data_dir: Path) -> None:
    """Confirm all expected paths exist. Raises if partial initialization detected."""
    expected = [
        data_dir / "daily",
        data_dir / "profile.md",
        data_dir / "preferences.md",
    ]
    missing = [str(p) for p in expected if not p.exists()]
    if missing:
        raise OSError(f"Storage bootstrap incomplete, missing: {', '.join(missing)}")
