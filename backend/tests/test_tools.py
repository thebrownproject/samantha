"""Tests for tools module -- bash, file_read, file_write, reason_deeply, web_search."""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from agents import Usage
from openai.types.responses.response_usage import InputTokensDetails, OutputTokensDetails

from samantha.config import Config
from samantha.tools import (
    DELEGATION_FALLBACK,
    _capture_display,
    _file_read,
    _file_write,
    _frontmost_app_context,
    _needs_approval_check,
    _reason_deeply,
    _safe_bash,
    _web_search,
    capture_display,
    condense_for_voice,
    configure_app_tool_caller,
    configure_tools,
    estimate_usage_cost_usd,
    file_write,
    format_tool_error,
    frontmost_app_context,
    register_tools,
    resolve_pricing_model,
    safe_bash,
    usage_telemetry_fields,
)


@pytest.fixture(autouse=True)
def _reset_config(tmp_path):
    configure_tools(Config(safe_mode=False, data_dir=tmp_path / ".samantha"))
    configure_app_tool_caller(None)
    yield
    configure_tools(Config())
    configure_app_tool_caller(None)


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
    assert "Error in" in result


async def test_bash_rejects_dangerous_mkfs():
    result = await _safe_bash("mkfs.ext4 /dev/sda")
    assert "Error in" in result


async def test_bash_timeout():
    result = await _safe_bash("sleep 60")
    assert "timed out" in result


async def test_bash_allowlist_blocks():
    configure_tools(Config(safe_mode=True, bash_allowlist=["ls", "echo"]))
    result = await _safe_bash("curl http://example.com")
    assert "Error in" in result
    assert "allowlist" in result


async def test_bash_allowlist_allows():
    configure_tools(Config(safe_mode=True, bash_allowlist=["echo"]))
    result = await _safe_bash("echo allowed")
    assert "allowed" in result


async def test_bash_empty_allowlist():
    configure_tools(Config(safe_mode=True, bash_allowlist=[]))
    result = await _safe_bash("echo test")
    assert "Error in" in result
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
    assert "Error in" in result


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
    assert "Error in" in result
    assert "protected" in result


async def test_write_rejects_protected_usr():
    result = await _file_write("/usr/local/bad", "x")
    assert "Error in" in result


async def test_write_rejects_ssh_dir():
    ssh_path = Path.home() / ".ssh" / "test_key"
    result = await _file_write(str(ssh_path), "x")
    assert "Error in" in result
    assert "sensitive" in result


async def test_write_safe_mode_blocks_outside_home():
    configure_tools(Config(safe_mode=True))
    result = await _file_write("/tmp/outside.txt", "x")
    assert "Error in" in result


# -- register_tools --

def test_register_tools_returns_all():
    tools = register_tools()
    assert len(tools) == 11


def test_register_tools_with_config():
    cfg = Config(safe_mode=False)
    tools = register_tools(cfg)
    assert len(tools) == 11


def test_register_tools_includes_reason_deeply():
    tools = register_tools()
    names = [t.name for t in tools]
    assert "reason_deeply" in names


def test_register_tools_includes_web_search():
    tools = register_tools()
    names = [t.name for t in tools]
    assert "web_search" in names


def test_register_tools_includes_visual_context_tools():
    tools = register_tools()
    names = [t.name for t in tools]
    assert "frontmost_app_context" in names
    assert "capture_display" in names


def test_visual_context_tool_wrappers_are_named():
    assert frontmost_app_context.name == "frontmost_app_context"
    assert capture_display.name == "capture_display"


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
    # Truncated to MAX_DELEGATION_OUTPUT then condensed for voice (500 char default)
    assert len(result) <= 500 + 3
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
    with (
        patch("samantha.tools.Runner.run", new_callable=AsyncMock, side_effect=[RuntimeError("transient"), mock_result]),
        patch("samantha.tools.asyncio.sleep", new_callable=AsyncMock),
    ):
        result = await _reason_deeply("retry task")
    assert result == "recovered answer"


async def test_reason_deeply_all_retries_exhausted():
    """All attempts fail -- returns fallback."""
    configure_tools(Config(safe_mode=False, delegation_timeout=5, delegation_max_retries=2))

    with (
        patch("samantha.tools.Runner.run", new_callable=AsyncMock, side_effect=RuntimeError("persistent failure")),
        patch("samantha.tools.asyncio.sleep", new_callable=AsyncMock),
    ):
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

    import samantha.tools as _tools_mod
    _tools_mod._openai_client = None  # reset singleton so mock takes effect
    with patch("samantha.tools.openai.AsyncOpenAI", autospec=False) as mock_cls:
        mock_client = MagicMock()
        mock_client.responses.create = AsyncMock(return_value=mock_response)
        mock_cls.return_value = mock_client
        result = await _web_search("test query")
    _tools_mod._openai_client = None  # clean up

    payload = json.loads(result)
    assert payload == {
        "query": "test query",
        "results": [{"title": "Example", "url": "https://example.com"}],
        "summary": "Here are results.",
    }


async def test_web_search_handles_empty_results():
    import samantha.tools as _tools_mod
    _tools_mod._openai_client = None
    mock_response = SimpleNamespace(output=[])

    with patch("samantha.tools.openai.AsyncOpenAI", autospec=False) as mock_cls:
        mock_client = MagicMock()
        mock_client.responses.create = AsyncMock(return_value=mock_response)
        mock_cls.return_value = mock_client
        result = await _web_search("obscure query xyz")
    _tools_mod._openai_client = None

    payload = json.loads(result)
    assert payload == {
        "query": "obscure query xyz",
        "results": [],
        "summary": "",
    }


async def test_web_search_handles_api_error():
    import samantha.tools as _tools_mod
    _tools_mod._openai_client = None
    with patch("samantha.tools.openai.AsyncOpenAI", autospec=False) as mock_cls:
        mock_client = MagicMock()
        mock_client.responses.create = AsyncMock(side_effect=RuntimeError("API error"))
        mock_cls.return_value = mock_client
        result = await _web_search("fail query")
    _tools_mod._openai_client = None

    payload = json.loads(result)
    assert payload == {
        "error": "API error",
        "query": "fail query",
        "results": [],
        "summary": "",
    }


async def test_frontmost_app_context_returns_structured_json():
    async def fake_caller(tool_name: str, args: dict[str, str]):
        assert tool_name == "frontmost_app_context"
        assert args == {}
        return {"app_name": "Safari", "window_title": "OpenAI"}

    configure_app_tool_caller(fake_caller)
    payload = json.loads(await _frontmost_app_context())
    assert payload == {"app_name": "Safari", "window_title": "OpenAI"}


async def test_frontmost_app_context_requires_caller():
    configure_app_tool_caller(None)
    result = await _frontmost_app_context()
    assert "Error in frontmost_app_context" in result


async def test_capture_display_returns_summary_json():
    async def fake_caller(tool_name: str, args: dict[str, str]):
        assert tool_name == "capture_display"
        assert args == {}
        return {
            "mime_type": "image/png",
            "image_base64": "ZmFrZQ==",
            "width": 1280,
            "height": 720,
        }

    mock_response = SimpleNamespace(output_text="Safari is open to the OpenAI homepage.")
    mock_client = MagicMock()
    mock_client.responses.create = AsyncMock(return_value=mock_response)

    configure_app_tool_caller(fake_caller)
    with patch("samantha.tools._get_openai_client", return_value=mock_client):
        payload = json.loads(await _capture_display())

    assert payload == {
        "height": 720,
        "mime_type": "image/png",
        "summary": "Safari is open to the OpenAI homepage.",
        "width": 1280,
    }


async def test_capture_display_handles_missing_caller():
    configure_app_tool_caller(None)
    result = await _capture_display()
    assert "Error in capture_display" in result


# -- format_tool_error --

def test_format_tool_error_includes_tool_name():
    result = format_tool_error("bash", "command not found")
    assert result == "Error in bash: command not found"


def test_format_tool_error_truncates_multiline():
    result = format_tool_error("file_read", "line one\nline two\nline three")
    assert "line one" in result
    assert "\n" not in result


def test_format_tool_error_truncates_long_message():
    result = format_tool_error("bash", "x" * 300)
    assert len(result) < 250


# -- condense_for_voice --

def test_condense_strips_markdown_headers():
    assert "Summary" in condense_for_voice("## Summary\nSome content")
    assert "#" not in condense_for_voice("## Summary\nSome content")


def test_condense_strips_code_blocks():
    text = "Before\n```python\nprint('hi')\n```\nAfter"
    result = condense_for_voice(text)
    assert "```" not in result
    assert "After" in result


def test_condense_strips_inline_code():
    assert condense_for_voice("Use `foo` here") == "Use foo here"


def test_condense_strips_bullet_points():
    text = "- First item\n- Second item"
    result = condense_for_voice(text)
    assert "- " not in result
    assert "First item" in result


def test_condense_strips_bold():
    assert condense_for_voice("This is **important**") == "This is important"


def test_condense_strips_links():
    assert condense_for_voice("See [docs](http://example.com)") == "See docs"


def test_condense_truncates_long_text():
    text = "This is a sentence. " * 100
    result = condense_for_voice(text, max_chars=100)
    assert len(result) <= 104  # up to max_chars + "..."


def test_condense_preserves_short_text():
    assert condense_for_voice("Short and sweet.") == "Short and sweet."


def test_condense_empty_returns_empty():
    assert condense_for_voice("") == ""


def test_condense_truncates_at_sentence_boundary():
    text = "First sentence. Second sentence. Third sentence is much longer and goes on."
    result = condense_for_voice(text, max_chars=40)
    assert result.endswith(".")


async def test_reason_deeply_condenses_markdown():
    """reason_deeply strips markdown for voice output."""
    mock_result = AsyncMock()
    mock_result.final_output = "## Analysis\n- Point one\n- Point two\n**Conclusion**: yes"
    with patch("samantha.tools.Runner.run", new_callable=AsyncMock, return_value=mock_result):
        result = await _reason_deeply("Analyze this")
    assert "#" not in result
    assert "- " not in result
    assert "**" not in result
    assert "Conclusion" in result


# -- delegation telemetry --

async def test_reason_deeply_logs_success_telemetry(caplog):
    """Successful delegation emits start and success log messages with correlation ID, model, and duration."""
    import logging

    mock_result = SimpleNamespace(
        final_output="telemetry answer",
        last_response_id="resp_123",
        context_wrapper=SimpleNamespace(
            usage=Usage(
                requests=1,
                input_tokens=1000,
                input_tokens_details=InputTokensDetails(cached_tokens=100),
                output_tokens=200,
                output_tokens_details=OutputTokensDetails(reasoning_tokens=50),
                total_tokens=1200,
            )
        ),
    )
    with (
        caplog.at_level(logging.INFO, logger="samantha.tools"),
        patch("samantha.tools.Runner.run", new_callable=AsyncMock, return_value=mock_result),
    ):
        result = await _reason_deeply("telemetry test")
    assert result == "telemetry answer"
    log_text = caplog.text
    assert "reason_deeply start" in log_text
    assert "reason_deeply success" in log_text
    assert "cid=" in log_text
    assert "model=" in log_text
    assert "duration=" in log_text
    assert "response_id=resp_123" in log_text
    assert "requests=1" in log_text
    assert "input_tokens=1000" in log_text
    assert "cached_input_tokens=100" in log_text
    assert "output_tokens=200" in log_text
    assert "reasoning_tokens=50" in log_text
    assert "total_tokens=1200" in log_text
    assert "est_cost_usd=0.000628" in log_text


async def test_reason_deeply_logs_failure_telemetry(caplog):
    """Failed delegation emits failure log with correlation ID and duration."""
    import logging

    configure_tools(Config(safe_mode=False, delegation_timeout=5, delegation_max_retries=0))
    with (
        caplog.at_level(logging.INFO, logger="samantha.tools"),
        patch("samantha.tools.Runner.run", new_callable=AsyncMock, side_effect=RuntimeError("boom")),
    ):
        result = await _reason_deeply("fail task")
    assert result == DELEGATION_FALLBACK
    log_text = caplog.text
    assert "reason_deeply start" in log_text
    assert "reason_deeply failure" in log_text
    assert "cid=" in log_text
    assert "duration=" in log_text
    assert "failure_category=error" in log_text
    assert "error_type=RuntimeError" in log_text


async def test_reason_deeply_logs_timeout_category(caplog):
    """Timeout path emits explicit timeout telemetry."""
    import logging

    configure_tools(Config(safe_mode=False, delegation_timeout=1, delegation_max_retries=0))

    async def slow_run(*_args, **_kwargs):
        await asyncio.sleep(10)

    with (
        caplog.at_level(logging.INFO, logger="samantha.tools"),
        patch("samantha.tools.Runner.run", new_callable=AsyncMock, side_effect=slow_run),
    ):
        result = await _reason_deeply("slow task")

    assert result == DELEGATION_FALLBACK
    log_text = caplog.text
    assert "reason_deeply timeout" in log_text
    assert "failure_category=timeout" in log_text
    assert "error_type=TimeoutError" in log_text


def test_resolve_pricing_model_for_snapshot():
    assert resolve_pricing_model("gpt-5-mini-2025-08-07") == "gpt-5-mini"


def test_resolve_pricing_model_unknown():
    assert resolve_pricing_model("unknown-model") is None


def test_estimate_usage_cost_usd():
    usage = Usage(
        requests=1,
        input_tokens=1000,
        input_tokens_details=InputTokensDetails(cached_tokens=100),
        output_tokens=200,
        output_tokens_details=OutputTokensDetails(reasoning_tokens=50),
        total_tokens=1200,
    )
    assert estimate_usage_cost_usd("gpt-5-mini-2025-08-07", usage) == pytest.approx(0.0006275)


def test_usage_telemetry_fields_without_usage():
    assert usage_telemetry_fields("gpt-5-mini-2025-08-07", None) == {
        "requests": 0,
        "input_tokens": 0,
        "cached_input_tokens": 0,
        "output_tokens": 0,
        "reasoning_tokens": 0,
        "total_tokens": 0,
        "est_cost_usd": "unknown",
    }
