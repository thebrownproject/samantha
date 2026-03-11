"""Tool definitions: bash, file_read, file_write, reason_deeply, web_search, memory."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import shlex
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

import openai
from agents import Agent, Runner, Usage, function_tool

from samantha.config import Config
from samantha.memory import MemoryStore
from samantha.prompts import DELEGATION_PROMPT

logger = logging.getLogger(__name__)

_cfg: Config = Config()
_memory: MemoryStore | None = None
_openai_client: openai.AsyncOpenAI | None = None


def format_tool_error(tool_name: str, error: str) -> str:
    """Consistent, voice-friendly error message for any tool failure."""
    short = error.split("\n")[0].strip()[:200]
    logger.error("Tool %s failed: %s", tool_name, error)
    return f"Error in {tool_name}: {short}"


_RE_CODE_BLOCK = re.compile(r"```[\s\S]*?```")
_RE_INLINE_CODE = re.compile(r"`([^`]+)`")
_RE_HEADING = re.compile(r"^#{1,6}\s+", re.MULTILINE)
_RE_LIST_ITEM = re.compile(r"^[\s]*[-*]\s+", re.MULTILINE)
_RE_BOLD = re.compile(r"\*{1,2}([^*]+)\*{1,2}")
_RE_LINK = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_RE_BLANK_LINES = re.compile(r"\n{2,}")


def condense_for_voice(text: str, max_chars: int = 500) -> str:
    """Strip markdown/code artifacts and truncate for spoken delivery."""
    out = _RE_CODE_BLOCK.sub("", text)
    out = _RE_INLINE_CODE.sub(r"\1", out)
    out = _RE_HEADING.sub("", out)
    out = _RE_LIST_ITEM.sub("", out)
    out = _RE_BOLD.sub(r"\1", out)
    out = _RE_LINK.sub(r"\1", out)
    out = _RE_BLANK_LINES.sub(" ", out)
    out = out.strip()
    if len(out) <= max_chars:
        return out
    cut = out[:max_chars].rsplit(". ", 1)
    return (cut[0] + ".") if len(cut) > 1 else out[:max_chars] + "..."


DANGEROUS_PATTERNS = ["rm -rf /", "rm -rf /*", "mkfs.", "dd if=", ":(){", "fork bomb"]
PROTECTED_PREFIXES = ["/etc", "/usr", "/bin", "/sbin", "/System", "/Library", "/boot", "/proc"]
MAX_OUTPUT = 10_240
MAX_READ = 1_048_576


def _is_dangerous(command: str) -> bool:
    return any(p in command.strip() for p in DANGEROUS_PATTERNS)


def _check_path(path: Path, *, write: bool = False) -> str | None:
    """Validate resolved path against safety boundaries. Returns error string or None."""
    home = Path.home()
    resolved = path.resolve()
    if _cfg.safe_mode and not resolved.is_relative_to(home):
        return f"Blocked: path {resolved} is outside home directory in safe mode"
    if write:
        for prefix in PROTECTED_PREFIXES:
            if resolved.is_relative_to(prefix):
                return f"Blocked: cannot write to protected path {prefix}"
        ssh_dir = home / ".ssh"
        gnupg_dir = home / ".gnupg"
        if resolved.is_relative_to(ssh_dir) or resolved.is_relative_to(gnupg_dir):
            return "Blocked: cannot write to sensitive directory"
    return None


async def _safe_bash(command: str) -> str:
    if _is_dangerous(command):
        return format_tool_error("bash", "dangerous command pattern detected")

    if _cfg.safe_mode:
        try:
            base_cmd = shlex.split(command)[0]
        except ValueError:
            return format_tool_error("bash", "malformed command")
        base_cmd = Path(base_cmd).name
        if not _cfg.bash_allowlist:
            return format_tool_error("bash", "bash_allowlist is empty in safe mode")
        if base_cmd not in _cfg.bash_allowlist:
            return format_tool_error("bash", f"'{base_cmd}' not in bash allowlist")

    try:
        proc = await asyncio.create_subprocess_shell(
            command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
    except TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
            await proc.wait()
        return format_tool_error("bash", "command timed out after 30s")
    except OSError as e:
        return format_tool_error("bash", str(e))

    output = (stdout + stderr).decode(errors="replace")
    if len(output) > MAX_OUTPUT:
        output = output[:MAX_OUTPUT] + f"\n... truncated ({len(output)} bytes total)"
    return output or "(no output)"


async def _file_read(path: str) -> str:
    resolved = Path(path).resolve()
    if err := _check_path(resolved):
        return format_tool_error("file_read", err)
    if not resolved.exists():
        return format_tool_error("file_read", f"file not found: {resolved}")
    if resolved.is_dir():
        return format_tool_error("file_read", f"path is a directory: {resolved}")
    size = resolved.stat().st_size
    if size > MAX_READ:
        return format_tool_error("file_read", f"file too large ({size} bytes)")
    try:
        return resolved.read_text(errors="replace")
    except PermissionError:
        return format_tool_error("file_read", f"permission denied: {resolved}")


async def _file_write(path: str, content: str) -> str:
    resolved = Path(path).resolve()
    if err := _check_path(resolved, write=True):
        return format_tool_error("file_write", err)
    try:
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(content)
    except PermissionError:
        return format_tool_error("file_write", f"permission denied: {resolved}")
    except OSError as e:
        return format_tool_error("file_write", str(e))
    return f"Wrote {len(content)} bytes to {resolved}"


async def _needs_approval_check(_ctx, _args, _call_id) -> bool:
    """Approval gate -- returns True when config.confirm_destructive is enabled."""
    return _cfg.confirm_destructive


@function_tool(needs_approval=_needs_approval_check)
async def safe_bash(command: str) -> str:
    """Execute a shell command with safety controls and timeout."""
    return await _safe_bash(command)


@function_tool
async def file_read(path: str) -> str:
    """Read a file and return its contents."""
    return await _file_read(path)


@function_tool(needs_approval=_needs_approval_check)
async def file_write(path: str, content: str) -> str:
    """Write content to a file, creating parent directories as needed."""
    return await _file_write(path, content)


MAX_DELEGATION_OUTPUT = 2048
DELEGATION_FALLBACK = (
    "I wasn't able to think that through deeply right now. "
    "Let me try to help directly."
)

MAX_BACKOFF_DELAY = 30.0


@dataclass(frozen=True, slots=True)
class ModelPricing:
    input_per_million_usd: float
    cached_input_per_million_usd: float
    output_per_million_usd: float


DELEGATION_MODEL_PRICING: dict[str, ModelPricing] = {
    # Official OpenAI API pricing as of 2026-03-11.
    "gpt-5-mini": ModelPricing(
        input_per_million_usd=0.250,
        cached_input_per_million_usd=0.025,
        output_per_million_usd=2.000,
    ),
}


def resolve_pricing_model(model_name: str) -> str | None:
    for base_model in DELEGATION_MODEL_PRICING:
        if model_name == base_model or model_name.startswith(f"{base_model}-"):
            return base_model
    return None


def estimate_usage_cost_usd(model_name: str, usage: Usage | None) -> float | None:
    if usage is None:
        return None

    pricing_key = resolve_pricing_model(model_name)
    if pricing_key is None:
        return None

    pricing = DELEGATION_MODEL_PRICING[pricing_key]
    input_tokens = max(usage.input_tokens or 0, 0)
    cached_input_tokens = min(
        max(usage.input_tokens_details.cached_tokens or 0, 0),
        input_tokens,
    )
    billable_input_tokens = max(input_tokens - cached_input_tokens, 0)
    output_tokens = max(usage.output_tokens or 0, 0)

    return (
        billable_input_tokens * pricing.input_per_million_usd
        + cached_input_tokens * pricing.cached_input_per_million_usd
        + output_tokens * pricing.output_per_million_usd
    ) / 1_000_000


def usage_telemetry_fields(model_name: str, usage: Usage | None) -> dict[str, int | str]:
    if usage is None:
        return {
            "requests": 0,
            "input_tokens": 0,
            "cached_input_tokens": 0,
            "output_tokens": 0,
            "reasoning_tokens": 0,
            "total_tokens": 0,
            "est_cost_usd": "unknown",
        }

    cost = estimate_usage_cost_usd(model_name, usage)
    return {
        "requests": usage.requests or 0,
        "input_tokens": usage.input_tokens or 0,
        "cached_input_tokens": usage.input_tokens_details.cached_tokens or 0,
        "output_tokens": usage.output_tokens or 0,
        "reasoning_tokens": usage.output_tokens_details.reasoning_tokens or 0,
        "total_tokens": usage.total_tokens or 0,
        "est_cost_usd": "unknown" if cost is None else f"{cost:.6f}",
    }


async def _reason_deeply(task: str) -> str:
    correlation_id = uuid.uuid4().hex[:12]
    model = _cfg.reasoning_model
    agent = Agent(
        name="reasoning_specialist",
        model=model,
        instructions=DELEGATION_PROMPT,
    )
    last_err: Exception | None = None
    attempts = 1 + _cfg.delegation_max_retries
    t_start = time.monotonic()

    logger.info("reason_deeply start cid=%s model=%s", correlation_id, model)

    for attempt in range(attempts):
        try:
            result = await asyncio.wait_for(
                Runner.run(agent, input=task, max_turns=1),
                timeout=_cfg.delegation_timeout,
            )
            output = str(result.final_output)
            usage = getattr(result.context_wrapper, "usage", None)
            telemetry = usage_telemetry_fields(model, usage if isinstance(usage, Usage) else None)
            if len(output) > MAX_DELEGATION_OUTPUT:
                output = output[:MAX_DELEGATION_OUTPUT] + "..."
            duration = time.monotonic() - t_start
            logger.info(
                "reason_deeply success cid=%s model=%s duration=%.2fs response_id=%s requests=%d "
                "input_tokens=%d cached_input_tokens=%d output_tokens=%d reasoning_tokens=%d "
                "total_tokens=%d est_cost_usd=%s",
                correlation_id,
                model,
                duration,
                result.last_response_id or "-",
                telemetry["requests"],
                telemetry["input_tokens"],
                telemetry["cached_input_tokens"],
                telemetry["output_tokens"],
                telemetry["reasoning_tokens"],
                telemetry["total_tokens"],
                telemetry["est_cost_usd"],
            )
            return condense_for_voice(output)
        except TimeoutError:
            last_err = TimeoutError(
                f"delegation timed out after {_cfg.delegation_timeout}s"
            )
            logger.warning(
                "reason_deeply timeout cid=%s model=%s attempt=%d/%d failure_category=timeout",
                correlation_id,
                model,
                attempt + 1,
                attempts,
            )
        except Exception as exc:
            last_err = exc
            logger.warning(
                "reason_deeply error cid=%s model=%s attempt=%d/%d failure_category=error "
                "error_type=%s error=%s",
                correlation_id,
                model,
                attempt + 1,
                attempts,
                type(exc).__name__,
                exc,
            )
        if attempt < attempts - 1:
            await asyncio.sleep(min(1.0 * (2 ** attempt), MAX_BACKOFF_DELAY))

    duration = time.monotonic() - t_start
    logger.error(
        "reason_deeply failure cid=%s model=%s duration=%.2fs failure_category=%s error_type=%s error=%s",
        correlation_id,
        model,
        duration,
        "timeout" if isinstance(last_err, TimeoutError) else "error",
        type(last_err).__name__ if last_err is not None else "UnknownError",
        last_err,
    )
    return DELEGATION_FALLBACK


@function_tool
async def reason_deeply(task: str) -> str:
    """Delegate complex reasoning to a specialist. Use for multi-step analysis, math, code review, planning, or comparisons that need deeper thought. Returns a concise answer for voice delivery."""
    return await _reason_deeply(task)


def _get_openai_client() -> openai.AsyncOpenAI:
    global _openai_client
    if _openai_client is None:
        _openai_client = openai.AsyncOpenAI()
    return _openai_client


async def _web_search(query: str) -> str:
    """Search the web via OpenAI Responses API and return structured results."""
    client = _get_openai_client()
    try:
        response = await client.responses.create(
            model="gpt-4o-mini",
            tools=[{"type": "web_search"}],
            input=query,
        )
    except Exception as exc:
        return format_tool_error("web_search", str(exc))

    lines: list[str] = []
    for item in response.output:
        if item.type == "message":
            for block in item.content:
                if hasattr(block, "text"):
                    lines.append(block.text)
                if hasattr(block, "annotations"):
                    for ann in block.annotations:
                        if hasattr(ann, "url") and hasattr(ann, "title"):
                            lines.append(f"  [{ann.title}]({ann.url})")

    if not lines:
        return f"No results found for: {query}"
    return "\n".join(lines)


@function_tool
async def web_search(query: str) -> str:
    """Search the web and return relevant results with titles, snippets, and URLs."""
    return await _web_search(query)


@function_tool
async def memory_save(content: str, tags: str = "") -> str:
    """Save a fact or observation to long-term memory. Use tags (comma-separated) for categorization."""
    if _memory is None:
        return format_tool_error("memory_save", "memory store not initialized")
    try:
        tag_str = tags if tags else None
        mem_id = await _memory.save(content, tags=tag_str)
        return f"Saved memory #{mem_id}"
    except Exception as exc:
        return format_tool_error("memory_save", str(exc))


@function_tool
async def memory_search(query: str) -> str:
    """Search long-term memory for relevant facts and context."""
    if _memory is None:
        return format_tool_error("memory_search", "memory store not initialized")
    try:
        results = await _memory.search(query)
        if not results:
            return "No memories found."
        lines = []
        for r in results:
            tags_part = f" [{r['tags']}]" if r.get("tags") else ""
            lines.append(f"- {r['content']}{tags_part} (score: {r['score']})")
        return "\n".join(lines)
    except Exception as exc:
        return format_tool_error("memory_search", str(exc))


@function_tool
async def daily_log_append(entry: str) -> str:
    """Append an observation or event to today's daily log."""
    if _memory is None:
        return format_tool_error("daily_log_append", "memory store not initialized")
    try:
        log_id = await _memory.append_daily_log(entry)
        return f"Logged entry #{log_id}"
    except Exception as exc:
        return format_tool_error("daily_log_append", str(exc))


@function_tool
async def daily_log_search(date: str = "") -> str:
    """Retrieve daily log entries for a given date (YYYY-MM-DD). Defaults to today."""
    if _memory is None:
        return format_tool_error("daily_log_search", "memory store not initialized")
    try:
        date_str = date if date else None
        entries = await _memory.get_daily_log(date_str)
        if not entries:
            return "No log entries found."
        lines = [f"- [{e['created_at']}] {e['entry']}" for e in entries]
        return "\n".join(lines)
    except Exception as exc:
        return format_tool_error("daily_log_search", str(exc))


def configure_tools(config: Config) -> None:
    """Set module-level config used by tools at runtime."""
    global _cfg
    _cfg = config


def configure_memory(store: MemoryStore) -> None:
    """Set module-level memory store used by memory tools."""
    global _memory
    _memory = store


def register_tools(config: Config | None = None) -> list:
    """Register and return all available tools."""
    if config:
        configure_tools(config)
    return [
        safe_bash, file_read, file_write, reason_deeply, web_search,
        memory_save, memory_search, daily_log_append, daily_log_search,
    ]
