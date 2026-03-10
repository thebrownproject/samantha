"""End-to-end integration tests for the Samantha backend.

Tests that require OPENAI_API_KEY are marked with @pytest.mark.e2e
and skipped by default. Run with: pytest -m e2e
"""

import asyncio
import json
import os
from types import SimpleNamespace

import pytest
from websockets.asyncio.client import connect

from samantha.agents import create_voice_agent
from samantha.config import Config
from samantha.events import AppState, EventDispatcher
from samantha.memory import MemoryStore
from samantha.session_manager import SessionManager
from samantha.tools import configure_memory, configure_tools, register_tools
from samantha.ws_server import ConnectionState, start_server


def has_api_key():
    return bool(os.environ.get("OPENAI_API_KEY"))


# -- Offline integration tests (no API key needed) --


async def test_full_backend_init(tmp_path):
    """Test that all components initialize without errors."""
    cfg = Config(data_dir=tmp_path / ".samantha", ws_port=9199)
    configure_tools(cfg)

    store = MemoryStore(db_path=tmp_path / "memory.db")
    await store.initialize()
    configure_memory(store)

    agent, runner_config = create_voice_agent(cfg)
    assert agent.name == "samantha"
    assert "model_settings" in runner_config

    ws = await start_server(cfg)
    assert ws.state == ConnectionState.DISCONNECTED
    _host, port = ws.address
    assert port > 0

    await ws.stop()
    await store.close()


async def test_ws_server_with_client(tmp_path):
    """Test WebSocket server accepts connection and handles messages."""
    cfg = Config(data_dir=tmp_path, ws_port=9198)
    ws = await start_server(cfg)
    host, port = ws.address

    async with connect(f"ws://{host}:{port}") as client:
        assert ws.state == ConnectionState.CONNECTED

        await client.send(json.dumps({"type": "start_listening"}))
        await asyncio.sleep(0.05)
        assert ws.listening is True

        audio_chunk = b"\x00\x01" * 480
        await client.send(audio_chunk)
        await asyncio.sleep(0.05)
        assert len(ws.received_audio) == 1

        await client.send(json.dumps({"type": "stop_listening"}))
        await asyncio.sleep(0.05)
        assert ws.listening is False

        await client.send(json.dumps({"type": "interrupt"}))
        await asyncio.sleep(0.05)
        assert ws.interrupt_count == 1

    await ws.stop()


async def test_memory_save_search_roundtrip(tmp_path):
    """Test saving and searching memory works end-to-end."""
    store = MemoryStore(db_path=tmp_path / "memory.db")
    await store.initialize()

    mem_id = await store.save("Fraser prefers dark mode in all applications", tags="preference,ui")
    assert mem_id > 0

    results = await store.search("dark mode preference")
    assert len(results) > 0
    assert any("dark mode" in r["content"] for r in results)

    await store.close()


async def test_memory_daily_log_roundtrip(tmp_path):
    """Test daily log append and retrieval."""
    store = MemoryStore(db_path=tmp_path / "memory.db")
    await store.initialize()

    log_id = await store.append_daily_log("Worked on Samantha backend today")
    assert log_id > 0

    entries = await store.get_daily_log()
    assert len(entries) == 1
    assert "Samantha" in entries[0]["entry"]

    mem_id = await store.promote_to_memory(log_id, tags="project,samantha")
    assert mem_id > 0

    results = await store.search("Samantha backend")
    assert len(results) > 0

    await store.close()


async def test_event_dispatcher_full_flow():
    """Test event dispatcher through a complete conversation cycle."""
    dispatcher = EventDispatcher()

    states = []
    dispatcher.on_state_change(lambda s: states.append(s))

    def evt(t, **kw):
        return SimpleNamespace(type=t, **kw)

    dispatcher.handle_event(evt("audio_interrupted"))  # -> LISTENING
    dispatcher.handle_event(evt("tool_start", tool=SimpleNamespace(name="memory_search")))  # -> THINKING
    dispatcher.handle_event(evt("tool_end", tool=SimpleNamespace(name="memory_search"), output="ok"))  # no change
    dispatcher.handle_event(evt("audio", data=b"\x00"))  # -> SPEAKING
    dispatcher.handle_event(evt("audio_interrupted"))  # -> LISTENING (barge-in)
    dispatcher.handle_event(evt("agent_end"))  # -> IDLE

    assert states == [
        AppState.LISTENING,
        AppState.THINKING,
        AppState.SPEAKING,
        AppState.LISTENING,
        AppState.IDLE,
    ]


async def test_session_manager_lifecycle():
    """Test session manager start/stop cycle."""
    dispatcher = EventDispatcher()
    mgr = SessionManager(dispatcher=dispatcher)
    assert not mgr.is_connected

    class FakeRunner:
        async def run(self):
            await asyncio.sleep(100)

        async def close(self):
            pass

    await mgr.start(FakeRunner())
    await asyncio.sleep(0.05)
    assert mgr.is_connected

    await mgr.stop()
    assert not mgr.is_connected


async def test_tools_register_all(tmp_path):
    """Test all tools register correctly with config."""
    cfg = Config(data_dir=tmp_path, safe_mode=False)
    tools = register_tools(cfg)
    tool_names = {t.name for t in tools}
    expected = {"safe_bash", "file_read", "file_write", "reason_deeply", "web_search", "memory_save", "memory_search", "daily_log_append", "daily_log_search"}
    assert expected.issubset(tool_names), f"Missing tools: {expected - tool_names}"


# -- Online integration tests (need OPENAI_API_KEY) --


@pytest.mark.e2e
@pytest.mark.skipif(not has_api_key(), reason="OPENAI_API_KEY not set")
async def test_delegation_smoke():
    """Smoke test: call reason_deeply with real API."""
    from samantha.tools import _reason_deeply

    cfg = Config(safe_mode=False)
    configure_tools(cfg)
    result = await _reason_deeply("What is 2 + 2? Reply with just the number.")
    assert "4" in result


@pytest.mark.e2e
@pytest.mark.skipif(not has_api_key(), reason="OPENAI_API_KEY not set")
async def test_web_search_smoke():
    """Smoke test: call web_search with real API."""
    from samantha.tools import _web_search

    cfg = Config(safe_mode=False)
    configure_tools(cfg)
    result = await _web_search("OpenAI agents SDK Python")
    assert len(result) > 0
