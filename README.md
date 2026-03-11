# Samantha

A voice-first AI companion for macOS. Floating presence widget, natural realtime conversation, persistent memory, and local computer tools.

**Core loop:** Press hotkey -> speak naturally -> Samantha listens in realtime -> uses tools and memory -> speaks back -> remembers what matters

**Mental model:** Samantha is a native desktop companion, not a chat window. Voice is primary. The floating presence widget is the interface.

---

## Architecture

```
macOS app (SwiftUI/AppKit, AVAudioEngine, hotkey, desktop context tools)
    │
    └── WebSocket (localhost:9090, PCM audio + JSON control/events)
            │
            └── Python backend (OpenAI Agents SDK)
                  - RealtimeAgent / RealtimeRunner
                  - Tool layer (bash, files, web search, visual context)
                  - Memory layer (SQLite + FTS5 + sqlite-vec)
                  - AppleScript MCP integration
```

## Codebases

| Directory | Stack | Purpose |
|-----------|-------|---------|
| `app/` | SwiftUI, AppKit, AVAudioEngine | Presence widget, audio I/O, hotkey loop, websocket client, desktop context tools |
| `backend/` | Python, OpenAI Agents SDK, websockets, SQLite | Realtime runtime, tool layer, memory, local IPC server |
| `docs/` | Markdown | Product spec, architecture, IPC contract, frontend handoff, design direction |
| `scripts/` | Shell | Local bootstrap and health checks |

## Current Status

- Backend realtime session runtime is wired and tested
- Local IPC protocol is versioned and documented
- First visual-context phase is implemented as:
  - `frontmost_app_context`
  - `capture_display`
- Backend-only integration coverage exists for the app-tool RPC path via the mock websocket harness
- Swift app source exists, but the checked-in Xcode project/workspace is still pending, so full macOS build verification is not yet complete

## Development

```bash
# Backend bootstrap
cd backend
./.venv/bin/ruff check samantha tests
./.venv/bin/python -m pytest -q
./.venv/bin/python -m pytest -q -m e2e

# Run backend
cd backend
./.venv/bin/samantha

# Mock websocket client
cd backend
./.venv/bin/python -m samantha.mock_client --get-state
./.venv/bin/python -m samantha.mock_client --auto-visual-context-tools --idle-timeout 2
```

## Docs

- [Product Spec](docs/spec.md)
- [Architecture](docs/architecture.md)
- [IPC Protocol](docs/ipc-protocol.md)
- [Frontend Handoff](docs/frontend-handoff.md)
- [Design Direction](docs/design-direction.md)
- [Implementation Reference](docs/building-agents-reference.md)

## Verification

Latest verified backend state in this repo:

- `ruff check samantha tests`
- `pytest -q` -> `304 passed`
- `pytest -q -m e2e` -> `3 passed, 301 deselected`

The remaining verification gap is the macOS app runtime/build path.
