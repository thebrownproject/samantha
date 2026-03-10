"""Tests for backend package scaffold -- validates structure, imports, and pyproject."""

import importlib
import tomllib
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = BACKEND_ROOT / "pyproject.toml"

EXPECTED_MODULES = [
    "samantha",
    "samantha.main",
    "samantha.agents",
    "samantha.tools",
    "samantha.memory",
    "samantha.ws_server",
    "samantha.config",
    "samantha.prompts",
]

REQUIRED_RUNTIME_DEPS = [
    "openai-agents",
    "websockets",
    "sqlite-vec",
    "sentence-transformers",
    "mcp",
]

REQUIRED_DEV_DEPS = ["pytest", "pytest-asyncio", "ruff"]


def test_pyproject_exists():
    assert PYPROJECT.exists(), "pyproject.toml not found"


def test_pyproject_parses():
    with open(PYPROJECT, "rb") as f:
        data = tomllib.load(f)
    assert "project" in data


def test_pyproject_python_version():
    with open(PYPROJECT, "rb") as f:
        data = tomllib.load(f)
    assert data["project"].get("requires-python", "").startswith(">=3.11")


def test_pyproject_runtime_dependencies():
    with open(PYPROJECT, "rb") as f:
        data = tomllib.load(f)
    deps = [d.split(">=")[0].split("<")[0].strip() for d in data["project"].get("dependencies", [])]
    for dep in REQUIRED_RUNTIME_DEPS:
        assert dep in deps, f"Missing runtime dependency: {dep}"


def test_pyproject_dev_dependencies():
    with open(PYPROJECT, "rb") as f:
        data = tomllib.load(f)
    dev_deps_raw = data["project"].get("optional-dependencies", {}).get("dev", [])
    dev_deps = [d.split(">=")[0].split("<")[0].strip() for d in dev_deps_raw]
    for dep in REQUIRED_DEV_DEPS:
        assert dep in dev_deps, f"Missing dev dependency: {dep}"


def test_pyproject_entry_point():
    with open(PYPROJECT, "rb") as f:
        data = tomllib.load(f)
    scripts = data["project"].get("scripts", {})
    assert "samantha" in scripts, "Missing 'samantha' script entry point"
    assert scripts["samantha"] == "samantha.main:main"


def test_all_modules_importable():
    for mod_name in EXPECTED_MODULES:
        mod = importlib.import_module(mod_name)
        assert mod is not None, f"Failed to import {mod_name}"


def test_package_has_version():
    import samantha
    assert hasattr(samantha, "__version__")
    assert isinstance(samantha.__version__, str)


def test_main_has_entry_point():
    from samantha.main import main
    assert callable(main)


def test_module_files_exist():
    pkg_dir = BACKEND_ROOT / "samantha"
    expected_files = [
        "__init__.py",
        "main.py",
        "agents.py",
        "tools.py",
        "memory.py",
        "ws_server.py",
        "config.py",
        "prompts.py",
    ]
    for fname in expected_files:
        assert (pkg_dir / fname).exists(), f"Missing module: {fname}"
