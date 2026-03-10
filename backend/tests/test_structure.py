"""Repository structure contract tests -- verify expected layout from CLAUDE.md."""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
BACKEND_ROOT = PROJECT_ROOT / "backend"


def test_backend_samantha_modules():
    pkg = BACKEND_ROOT / "samantha"
    expected = [
        "__init__.py", "main.py", "agents.py", "tools.py",
        "memory.py", "ws_server.py", "config.py", "prompts.py",
    ]
    for f in expected:
        assert (pkg / f).exists(), f"Missing backend module: samantha/{f}"


def test_docs_directory():
    docs = PROJECT_ROOT / "docs"
    assert docs.is_dir(), "docs/ directory missing"
    for f in ["spec.md", "architecture.md", "building-agents-reference.md"]:
        assert (docs / f).exists(), f"Missing doc: docs/{f}"


def test_scripts_dev_sh():
    assert (PROJECT_ROOT / "scripts" / "dev.sh").exists(), "scripts/dev.sh missing"


def test_backend_pyproject():
    assert (BACKEND_ROOT / "pyproject.toml").exists(), "backend/pyproject.toml missing"


def test_backend_tests_directory():
    tests = BACKEND_ROOT / "tests"
    assert tests.is_dir(), "backend/tests/ directory missing"
    assert (tests / "__init__.py").exists(), "backend/tests/__init__.py missing"


def test_claude_md_exists():
    assert (PROJECT_ROOT / "CLAUDE.md").exists(), "CLAUDE.md missing at project root"
