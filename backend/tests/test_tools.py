"""Tests for tools module -- bash, file_read, file_write."""

from pathlib import Path

import pytest

from samantha.config import Config
from samantha.tools import (
    _file_read,
    _file_write,
    _safe_bash,
    configure_tools,
    register_tools,
)


@pytest.fixture(autouse=True)
def _reset_config(tmp_path):
    configure_tools(Config(safe_mode=False, data_dir=tmp_path / ".samantha"))
    yield
    configure_tools(Config())


# -- safe_bash --

async def test_bash_echo():
    result = await _safe_bash("echo hello")
    assert "hello" in result


async def test_bash_ls(tmp_path):
    (tmp_path / "testfile.txt").write_text("x")
    result = await _safe_bash(f"ls {tmp_path}")
    assert "testfile.txt" in result


async def test_bash_rejects_dangerous():
    result = await _safe_bash("rm -rf /")
    assert "Blocked" in result


async def test_bash_rejects_dangerous_mkfs():
    result = await _safe_bash("mkfs.ext4 /dev/sda")
    assert "Blocked" in result


async def test_bash_timeout():
    result = await _safe_bash("sleep 60")
    assert "timed out" in result


async def test_bash_allowlist_blocks():
    configure_tools(Config(safe_mode=True, bash_allowlist=["ls", "echo"]))
    result = await _safe_bash("curl http://example.com")
    assert "Blocked" in result
    assert "allowlist" in result


async def test_bash_allowlist_allows():
    configure_tools(Config(safe_mode=True, bash_allowlist=["echo"]))
    result = await _safe_bash("echo allowed")
    assert "allowed" in result


async def test_bash_empty_allowlist():
    configure_tools(Config(safe_mode=True, bash_allowlist=[]))
    result = await _safe_bash("echo test")
    assert "Blocked" in result
    assert "empty" in result


# -- file_read --

async def test_read_existing_file(tmp_path):
    f = tmp_path / "hello.txt"
    f.write_text("file contents here")
    result = await _file_read(str(f))
    assert result == "file contents here"


async def test_read_not_found(tmp_path):
    result = await _file_read(str(tmp_path / "nope.txt"))
    assert "not found" in result


async def test_read_rejects_outside_home_in_safe_mode():
    configure_tools(Config(safe_mode=True))
    result = await _file_read("/etc/passwd")
    assert "Blocked" in result


async def test_read_rejects_oversized(tmp_path):
    f = tmp_path / "big.txt"
    f.write_bytes(b"x" * 1_100_000)
    result = await _file_read(str(f))
    assert "too large" in result


async def test_read_directory(tmp_path):
    result = await _file_read(str(tmp_path))
    assert "directory" in result


# -- file_write --

async def test_write_creates_file(tmp_path):
    f = tmp_path / "out.txt"
    result = await _file_write(str(f), "hello")
    assert "Wrote" in result
    assert f.read_text() == "hello"


async def test_write_creates_parent_dirs(tmp_path):
    f = tmp_path / "a" / "b" / "c.txt"
    result = await _file_write(str(f), "nested")
    assert "Wrote" in result
    assert f.read_text() == "nested"


async def test_write_rejects_protected_etc():
    result = await _file_write("/etc/evil.conf", "x")
    assert "Blocked" in result
    assert "protected" in result


async def test_write_rejects_protected_usr():
    result = await _file_write("/usr/local/bad", "x")
    assert "Blocked" in result


async def test_write_rejects_ssh_dir():
    ssh_path = Path.home() / ".ssh" / "test_key"
    result = await _file_write(str(ssh_path), "x")
    assert "Blocked" in result
    assert "sensitive" in result


async def test_write_safe_mode_blocks_outside_home():
    configure_tools(Config(safe_mode=True))
    result = await _file_write("/tmp/outside.txt", "x")
    assert "Blocked" in result


# -- register_tools --

def test_register_tools_returns_all():
    tools = register_tools()
    assert len(tools) == 3


def test_register_tools_with_config():
    cfg = Config(safe_mode=False)
    tools = register_tools(cfg)
    assert len(tools) == 3
