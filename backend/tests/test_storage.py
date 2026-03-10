"""Tests for storage bootstrap -- directory layout, template files, idempotency."""

from pathlib import Path

from samantha.config import Config
from samantha.storage import bootstrap_storage, PROFILE_TEMPLATE, PREFERENCES_TEMPLATE


def _make_config(tmp_path: Path) -> Config:
    return Config(data_dir=tmp_path)


def test_bootstrap_creates_data_dir(tmp_path):
    data_dir = tmp_path / "sam"
    cfg = Config(data_dir=data_dir)
    bootstrap_storage(cfg)
    assert data_dir.is_dir()


def test_bootstrap_creates_daily_dir(tmp_path):
    cfg = _make_config(tmp_path)
    bootstrap_storage(cfg)
    assert (tmp_path / "daily").is_dir()


def test_bootstrap_creates_profile(tmp_path):
    cfg = _make_config(tmp_path)
    bootstrap_storage(cfg)
    profile = tmp_path / "profile.md"
    assert profile.exists()
    assert profile.read_text() == PROFILE_TEMPLATE


def test_bootstrap_creates_preferences(tmp_path):
    cfg = _make_config(tmp_path)
    bootstrap_storage(cfg)
    prefs = tmp_path / "preferences.md"
    assert prefs.exists()
    assert prefs.read_text() == PREFERENCES_TEMPLATE


def test_bootstrap_idempotent_preserves_existing_files(tmp_path):
    cfg = _make_config(tmp_path)
    bootstrap_storage(cfg)

    custom_profile = "# My custom profile\nName: Alice\n"
    (tmp_path / "profile.md").write_text(custom_profile)
    custom_prefs = "# My prefs\nTheme: dark\n"
    (tmp_path / "preferences.md").write_text(custom_prefs)

    bootstrap_storage(cfg)

    assert (tmp_path / "profile.md").read_text() == custom_profile
    assert (tmp_path / "preferences.md").read_text() == custom_prefs


def test_bootstrap_idempotent_no_error_on_existing_dirs(tmp_path):
    cfg = _make_config(tmp_path)
    bootstrap_storage(cfg)
    bootstrap_storage(cfg)  # should not raise
    assert (tmp_path / "daily").is_dir()


def test_template_profile_has_structure():
    assert "# Profile" in PROFILE_TEMPLATE
    assert "Name:" in PROFILE_TEMPLATE


def test_template_preferences_has_structure():
    assert "# Preferences" in PREFERENCES_TEMPLATE


def test_bootstrap_raises_on_permission_error(tmp_path):
    import os
    import pytest

    blocked = tmp_path / "noaccess"
    blocked.mkdir()
    os.chmod(blocked, 0o000)
    try:
        cfg = Config(data_dir=blocked / "samantha")
        with pytest.raises(OSError):
            bootstrap_storage(cfg)
    finally:
        os.chmod(blocked, 0o755)


def test_bootstrap_verifies_all_paths(tmp_path):
    cfg = _make_config(tmp_path)
    bootstrap_storage(cfg)
    expected = [
        tmp_path / "daily",
        tmp_path / "profile.md",
        tmp_path / "preferences.md",
    ]
    for p in expected:
        assert p.exists(), f"Missing: {p}"
