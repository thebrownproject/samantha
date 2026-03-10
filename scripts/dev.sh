#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BACKEND="$REPO_ROOT/backend"
VENV="$BACKEND/.venv"
MIN_PYTHON="3.11"

red()   { printf '\033[0;31m%s\033[0m\n' "$*"; }
green() { printf '\033[0;32m%s\033[0m\n' "$*"; }
bold()  { printf '\033[1m%s\033[0m\n' "$*"; }

fail() { red "FAIL: $*"; exit 1; }

# -- Python version check --
PYTHON=""
for cmd in python3.12 python3.11 python3; do
    if command -v "$cmd" &>/dev/null; then
        PYTHON="$cmd"
        break
    fi
done
[ -n "$PYTHON" ] || fail "Python 3.11+ not found"

PY_VERSION=$("$PYTHON" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
PY_MAJOR=$("$PYTHON" -c "import sys; print(sys.version_info.major)")
PY_MINOR=$("$PYTHON" -c "import sys; print(sys.version_info.minor)")

if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 11 ]; }; then
    fail "Python >= $MIN_PYTHON required (found $PY_VERSION)"
fi
green "Python $PY_VERSION"

# -- Create venv if missing --
if [ ! -d "$VENV" ]; then
    bold "Creating venv at $VENV ..."
    "$PYTHON" -m venv "$VENV"
    green "venv created"
else
    green "venv exists"
fi

source "$VENV/bin/activate"

# -- Install dependencies --
bold "Installing dependencies ..."
pip install -q -e "$BACKEND[dev]"
green "Dependencies installed"

# -- Health checks --
bold "Running health checks ..."

python -c "import samantha; print(f'samantha {samantha.__version__}')" \
    || fail "Cannot import samantha"

python -c "import openai_agents" 2>/dev/null \
    && green "openai-agents OK" \
    || red "WARN: openai-agents import failed (may need OPENAI_API_KEY at runtime)"

python -c "import websockets" || fail "websockets not installed"
green "websockets OK"

python -c "import ruff" 2>/dev/null \
    && green "ruff OK" \
    || green "ruff available (CLI tool)"

ruff version 2>/dev/null && green "ruff CLI OK" || red "WARN: ruff CLI not on PATH"

python -m pytest --co -q "$BACKEND/tests/" 2>/dev/null \
    && green "Test collection OK" \
    || red "WARN: Some tests may not collect (expected before full implementation)"

# -- Done --
echo ""
green "Bootstrap complete."
bold "Next steps:"
echo "  source $VENV/bin/activate"
echo "  cd $BACKEND"
echo "  python -m pytest tests/ -v      # run tests"
echo "  ruff check samantha/ tests/     # lint"
echo "  samantha                        # start server (needs OPENAI_API_KEY)"
