# Samantha

A voice-first AI companion for macOS. Floating presence widget with natural speech, full computer access, and persistent memory. Built with OpenAI Agents SDK (Python) and Swift.

## Architecture

Read `docs/architecture.md` for the full technical design.
Read `docs/spec.md` for the product specification.
Read `docs/ipc-protocol.md` for the websocket contract and versioning rules.
Read `docs/frontend-handoff.md` for backend-event to widget-state mapping.
Read `docs/design-direction.md` for the visual and motion direction.
Read `docs/observability.md` for runtime signals and incident triage.
Read `docs/building-agents-reference.md` for implementation details used while building.

```
Swift app (floating presence widget, audio I/O, hotkey)
    |  WebSocket (localhost:9090)
    |  - Binary frames: PCM16 audio
    |  - Text frames: JSON control messages
    v
Python backend (OpenAI Agents SDK)
    |-- RealtimeAgent (voice session specialist)
    |-- Delegation tool -> gpt-5-mini-2025-08-07 (deep reasoning)
    |-- Tools: bash, file_read, file_write, web_search, frontmost_app_context, capture_display, memory
    |-- AppleScript MCP (calendar, reminders, finder, music, system)
    |-- Memory: SQLite + FTS5 + sqlite-vec (~/.samantha/)
```

## Project Structure

```
Samantha/
├── AGENTS.md
├── docs/
│   ├── spec.md                         # Product specification
│   ├── architecture.md                 # Technical architecture
│   ├── ipc-protocol.md                 # WebSocket contract and versioning
│   ├── frontend-handoff.md             # Backend events -> widget/audio/transcript behavior
│   ├── design-direction.md             # Visual and motion direction
│   ├── observability.md                # Runtime signals and incident triage
│   └── building-agents-reference.md    # Build-time implementation reference
├── backend/                            # Python
│   ├── pyproject.toml
│   ├── samantha/
│   │   ├── __init__.py
│   │   ├── main.py                     # Entry point: start WebSocket server + agents
│   │   ├── agents.py                   # RealtimeAgent definitions + delegation behavior
│   │   ├── tools.py                    # bash, file_read, file_write, web_search, delegation
│   │   ├── memory.py                   # SQLite + FTS5 + sqlite-vec memory system
│   │   ├── ws_server.py                # WebSocket server (audio + control IPC)
│   │   ├── config.py                   # Settings management (~/.samantha/config.json)
│   │   └── prompts.py                  # System prompts
│   └── tests/
├── app/                                # Swift macOS app sources
│   └── Samantha/
│       ├── SamanthaApp.swift
│       ├── OrbWindow.swift
│       ├── OrbView.swift
│       ├── AudioManager.swift
│       ├── HotkeyManager.swift
│       ├── WebSocketClient.swift
│       ├── DesktopContextToolExecutor.swift
│       ├── SettingsView.swift
│       ├── TranscriptOverlay.swift
│       └── KeychainHelper.swift
└── scripts/
    └── dev.sh
```

## Build Sequence

Build in this order. Phase 1 is fully testable without Swift.

### Phase 1: Python Backend
1. `pyproject.toml` with dependencies (openai-agents, websockets, sqlite-vec, sentence-transformers, mcp)
2. `samantha/prompts.py` - System prompts
3. `samantha/tools.py` - Include safe wrappers for bash/file tools and reasoning delegation
4. `samantha/agents.py` - RealtimeAgent + optional realtime specialist handoffs
5. Configure realtime session (`model_name=gpt-realtime`, `turn_detection.interrupt_response=true`)
6. Add delegation tool path for `gpt-5-mini-2025-08-07`
7. `samantha/memory.py` - SQLite + FTS5 + sqlite-vec, memory_search/memory_save tools
8. `samantha/config.py` - Settings in `~/.samantha/config.json`
9. `samantha/ws_server.py` - WebSocket server for Swift IPC
10. Tests for memory, tools, interruption behavior, and delegation

### Phase 2: Swift App
1. Xcode project, LSUIElement, SwiftUI
2. `OrbWindow.swift` + `OrbView.swift` - implement the presence widget from `docs/design-direction.md` and `docs/frontend-handoff.md`
3. `HotkeyManager.swift` - Option+S toggle
4. `AudioManager.swift` - Mic capture (24kHz PCM16 mono) + playback
5. `WebSocketClient.swift` - Connect to Python backend
6. `BackendManager.swift` - Launch Python as subprocess
7. Wire: hotkey -> capture -> WebSocket -> playback
8. `SettingsView.swift` - API key, voice, preferences
9. `TranscriptOverlay.swift` - Optional live transcript following `docs/frontend-handoff.md`

### Phase 3: Polish
1. Error recovery and reconnect behavior
2. Launch at login
3. App bundling (embed Python)
4. Optional auto-update support

## Tech Stack

### Python Backend
- **openai-agents** - RealtimeAgent, RealtimeRunner, RealtimeSession
- **websockets** - WebSocket server for Swift IPC
- **sqlite-vec** - Vector similarity search extension for SQLite
- **sentence-transformers** - Local embeddings (all-MiniLM-L6-v2, 384 dims)
- **mcp** - MCP client for AppleScript tool server

### Swift App
- **SwiftUI + AppKit** - UI framework
- **AVAudioEngine** - Audio capture and playback
- **KeyboardShortcuts** (SPM) - Global hotkey registration
- **LaunchAtLogin** (SPM) - Login item support
- **Security.framework** - Keychain for API key storage

## Key Design Decisions

1. **Keep local Swift <-> Python WebSocket IPC** for clear process boundaries and low-latency bidirectional audio/control transport.
2. **Use realtime model for live voice** (`gpt-realtime*`) and keep interruption behavior native to realtime session settings.
3. **Use `gpt-5-mini-2025-08-07` through delegated tools** for heavier reasoning, not as the direct speech model.
4. **Local-first memory** in `~/.samantha/` with no cloud account dependency.
5. **AppleScript via MCP** to reuse mature system-control integrations.
6. **Push-to-talk first** for V1 scope and reliability.
7. **First-phase macOS visual context stays narrow**: `frontmost_app_context` plus `capture_display`, with more invasive computer-use features deferred.

## IPC Protocol

### Binary Frames (Audio)
- **Swift -> Python**: Raw PCM16 audio from mic (24kHz, mono)
- **Python -> Swift**: TTS audio for playback (24kHz, mono)

### Text Frames (Control)

Swift -> Python:
```json
{"protocol_version": 1, "type": "start_listening"}
{"protocol_version": 1, "type": "stop_listening"}
{"protocol_version": 1, "type": "interrupt"}
{"protocol_version": 1, "type": "set_voice", "voice": "alloy"}
{"protocol_version": 1, "type": "inject_context", "text": "..."}
{"protocol_version": 1, "type": "approve_tool_call", "call_id": "call_123", "always": false}
{"protocol_version": 1, "type": "reject_tool_call", "call_id": "call_123", "always": false}
```

Python -> Swift:
```json
{"protocol_version": 1, "type": "state_change", "state": "listening|thinking|speaking|idle|error"}
{"protocol_version": 1, "type": "transcript", "role": "user|assistant", "text": "...", "final": true}
{"protocol_version": 1, "type": "tool_start", "name": "reason_deeply", "args": {"task": "..."}}
{"protocol_version": 1, "type": "tool_end", "name": "reason_deeply", "result": "..."}
{"protocol_version": 1, "type": "tool_approval_required", "name": "file_write", "call_id": "call_123", "args": {"path": "..."}}
{"protocol_version": 1, "type": "clear_playback"}
{"protocol_version": 1, "type": "error", "message": "..."}
```

## Data Storage

```
~/.samantha/
├── profile.md
├── preferences.md
├── memory.db
├── daily/
└── config.json
```

## Conventions

- Python: PEP 8, type hints, async/await throughout
- Swift: SwiftUI, @MainActor for UI, structured concurrency
- Keep it simple. Minimal abstractions. Ship fast.
- No emojis in code or docs

<!-- BEGIN BEADS INTEGRATION -->
## Issue Tracking with bd (beads)

**IMPORTANT**: This project uses **bd (beads)** for ALL issue tracking. Do NOT use markdown TODOs, task lists, or other tracking methods.

### Why bd?

- Dependency-aware: Track blockers and relationships between issues
- Git-friendly: Auto-syncs to JSONL for version control
- Agent-optimized: JSON output, ready work detection, discovered-from links
- Prevents duplicate tracking systems and confusion

### Quick Start

**Check for ready work:**

```bash
bd ready --json
```

**Create new issues:**

```bash
bd create "Issue title" --description="Detailed context" -t bug|feature|task -p 0-4 --json
bd create "Issue title" --description="What this issue is about" -p 1 --deps discovered-from:bd-123 --json
```

**Claim and update:**

```bash
bd update bd-42 --status in_progress --json
bd update bd-42 --priority 1 --json
```

**Complete work:**

```bash
bd close bd-42 --reason "Completed" --json
```

### Issue Types

- `bug` - Something broken
- `feature` - New functionality
- `task` - Work item (tests, docs, refactoring)
- `epic` - Large feature with subtasks
- `chore` - Maintenance (dependencies, tooling)

### Priorities

- `0` - Critical (security, data loss, broken builds)
- `1` - High (major features, important bugs)
- `2` - Medium (default, nice-to-have)
- `3` - Low (polish, optimization)
- `4` - Backlog (future ideas)

### Workflow for AI Agents

1. **Check ready work**: `bd ready` shows unblocked issues
2. **Claim your task**: `bd update <id> --status in_progress`
3. **Work on it**: Implement, test, document
4. **Discover new work?** Create linked issue:
   - `bd create "Found bug" --description="Details about what was found" -p 1 --deps discovered-from:<parent-id>`
5. **Complete**: `bd close <id> --reason "Done"`

### Auto-Sync

bd automatically syncs with git:

- Exports to `.beads/issues.jsonl` after changes (5s debounce)
- Imports from JSONL when newer (e.g., after `git pull`)
- No manual export/import needed!

### Important Rules

- ✅ Use bd for ALL task tracking
- ✅ Always use `--json` flag for programmatic use
- ✅ Link discovered work with `discovered-from` dependencies
- ✅ Check `bd ready` before asking "what should I work on?"
- ❌ Do NOT create markdown TODO lists
- ❌ Do NOT use external issue trackers
- ❌ Do NOT duplicate tracking systems

For more details, see README.md and docs/QUICKSTART.md.

<!-- END BEADS INTEGRATION -->

## Landing the Plane (Session Completion)

**When ending a work session**, you MUST complete ALL steps below. Work is NOT complete until `git push` succeeds.

**MANDATORY WORKFLOW:**

1. **File issues for remaining work** - Create issues for anything that needs follow-up
2. **Run quality gates** (if code changed) - Tests, linters, builds
3. **Update issue status** - Close finished work, update in-progress items
4. **PUSH TO REMOTE** - This is MANDATORY:
   ```bash
   git pull --rebase
   bd sync
   git push
   git status  # MUST show "up to date with origin"
   ```
5. **Clean up** - Clear stashes, prune remote branches
6. **Verify** - All changes committed AND pushed
7. **Hand off** - Provide context for next session

**CRITICAL RULES:**
- Work is NOT complete until `git push` succeeds
- NEVER stop before pushing - that leaves work stranded locally
- NEVER say "ready to push when you are" - YOU must push
- If push fails, resolve and retry until it succeeds
