"""Tests for tools module -- bash, file_read, file_write, reason_deeply, web_search."""

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from samantha.config import Config
from samantha.tools import (
    DELEGATION_FALLBACK,
    _file_read,
    _file_write,
    _needs_approval_check,
    _reason_deeply,
    _safe_bash,
    _web_search,
    configure_tools,
    register_tools,
    safe_bash,
    file_write,
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
    assert len(tools) == 5


def test_register_tools_with_config():
    cfg = Config(safe_mode=False)
    tools = register_tools(cfg)
    assert len(tools) == 5


def test_register_tools_includes_reason_deeply():
    tools = register_tools()
    names = [t.name for t in tools]
    assert "reason_deeply" in names


def test_register_tools_includes_web_search():
    tools = register_tools()
    names = [t.name for t in tools]
    assert "web_search" in names


# -- reason_deeply --

async def test_reason_deeply_returns_string():
    mock_result = AsyncMock()
    mock_result.final_output = "The answer is 42."
    with patch("samantha.tools.Runner.run", new_callable=AsyncMock, return_value=mock_result):
        result = await _reason_deeply("What is the meaning of life?")
    assert result == "The answer is 42."


async def test_reason_deeply_truncates_long_output():
    mock_result = AsyncMock()
    mock_result.final_output = "x" * 3000
    with patch("samantha.tools.Runner.run", new_callable=AsyncMock, return_value=mock_result):
        result = await _reason_deeply("Generate a long response")
    assert len(result) == 2048 + 3  # truncated + "..."
    assert result.endswith("...")


async def test_reason_deeply_handles_error():
    with patch("samantha.tools.Runner.run", new_callable=AsyncMock, side_effect=RuntimeError("API down")):
        result = await _reason_deeply("This will fail")
    assert result == DELEGATION_FALLBACK


async def test_reason_deeply_timeout():
    """Runner.run hangs past delegation_timeout -- returns fallback after retries."""
    configure_tools(Config(safe_mode=False, delegation_timeout=1, delegation_max_retries=0))

    async def slow_run(*args, **kwargs):
        await asyncio.sleep(10)

    with patch("samantha.tools.Runner.run", side_effect=slow_run):
        result = await _reason_deeply("slow task")
    assert result == DELEGATION_FALLBACK


async def test_reason_deeply_retry_then_succeed():
    """First attempt fails, second succeeds."""
    configure_tools(Config(safe_mode=False, delegation_timeout=5, delegation_max_retries=1))

    mock_result = AsyncMock()
    mock_result.final_output = "recovered answer"
    with patch("samantha.tools.Runner.run", new_callable=AsyncMock,
               side_effect=[RuntimeError("transient"), mock_result]):
        with patch("samantha.tools.asyncio.sleep", new_callable=AsyncMock):
            result = await _reason_deeply("retry task")
    assert result == "recovered answer"


async def test_reason_deeply_all_retries_exhausted():
    """All attempts fail -- returns fallback."""
    configure_tools(Config(safe_mode=False, delegation_timeout=5, delegation_max_retries=2))

    with patch("samantha.tools.Runner.run", new_callable=AsyncMock,
               side_effect=RuntimeError("persistent failure")):
        with patch("samantha.tools.asyncio.sleep", new_callable=AsyncMock):
            result = await _reason_deeply("doomed task")
    assert result == DELEGATION_FALLBACK


# -- needs_approval / confirm_destructive --

async def test_needs_approval_true_when_confirm_destructive():
    configure_tools(Config(safe_mode=False, confirm_destructive=True))
    assert await _needs_approval_check(None, None, None) is True


async def test_needs_approval_false_when_not_confirm_destructive():
    configure_tools(Config(safe_mode=False, confirm_destructive=False))
    assert await _needs_approval_check(None, None, None) is False


def test_safe_bash_has_needs_approval():
    assert safe_bash.needs_approval is _needs_approval_check


def test_file_write_has_needs_approval():
    assert file_write.needs_approval is _needs_approval_check


# -- web_search --

async def test_web_search_returns_results():
    annotation = SimpleNamespace(type="url_citation", url="https://example.com", title="Example")
    text_block = SimpleNamespace(type="output_text", text="Here are results.", annotations=[annotation])
    message_item = SimpleNamespace(type="message", content=[text_block])
    mock_response = SimpleNamespace(output=[message_item])

    with patch("samantha.tools.openai.AsyncOpenAI", autospec=False) as mock_cls:
        mock_client = MagicMock()
        mock_client.responses.create = AsyncMock(return_value=mock_response)
        mock_cls.return_value = mock_client
        result = await _web_search("test query")

    assert "Here are results." in result
    assert "Example" in result
    assert "https://example.com" in result


async def test_web_search_handles_empty_results():
    mock_response = SimpleNamespace(output=[])

    with patch("samantha.tools.openai.AsyncOpenAI", autospec=False) as mock_cls:
        mock_client = MagicMock()
        mock_client.responses.create = AsyncMock(return_value=mock_response)
        mock_cls.return_value = mock_client
        result = await _web_search("obscure query xyz")

    assert "No results found" in result


async def test_web_search_handles_api_error():
    with patch("samantha.tools.openai.AsyncOpenAI", autospec=False) as mock_cls:
        mock_client = MagicMock()
        mock_client.responses.create = AsyncMock(side_effect=RuntimeError("API error"))
        mock_cls.return_value = mock_client
        result = await _web_search("fail query")

    assert "Error:" in result
    assert "web search failed" in result
