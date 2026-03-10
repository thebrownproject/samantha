# Samantha

A voice-first AI companion for macOS. Floating orb interface with natural speech, full computer access, and persistent memory. Built with OpenAI Agents SDK (Python) and Swift.

## Architecture

Read `docs/architecture.md` for the full technical design.  
Read `docs/spec.md` for the product specification.  
Read `docs/building-agents-reference.md` for implementation details used while building.

```
Swift app (floating orb, audio I/O, hotkey)
    |  WebSocket (localhost:9090)
    |  - Binary frames: PCM16 audio
    |  - Text frames: JSON control messages
    v
Python backend (OpenAI Agents SDK)
    |-- RealtimeAgent (voice session specialist)
    |-- Delegation tool -> gpt-5-mini-2025-08-07 (deep reasoning)
    |-- Tools: bash, file_read, file_write, web_search, memory
    |-- AppleScript MCP (calendar, reminders, finder, music, system)
    |-- Memory: SQLite + FTS5 + sqlite-vec (~/.samantha/)
```

## Project Structure

```
Samantha/
├── CLAUDE.md
├── docs/
│   ├── spec.md                         # Product specification
│   ├── architecture.md                 # Technical architecture
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
├── app/                                # Swift macOS app
│   ├── Samantha.xcodeproj
│   ├── Samantha/
│   │   ├── SamanthaApp.swift
│   │   ├── OrbWindow.swift
│   │   ├── OrbView.swift
│   │   ├── AudioManager.swift
│   │   ├── HotkeyManager.swift
│   │   ├── WebSocketClient.swift
│   │   ├── BackendManager.swift
│   │   ├── SettingsView.swift
│   │   ├── TranscriptOverlay.swift
│   │   └── KeychainHelper.swift
│   └── SamanthaTests/
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
2. `OrbWindow.swift` + `OrbView.swift`
3. `HotkeyManager.swift` - Option+S toggle
4. `AudioManager.swift` - Mic capture (24kHz PCM16 mono) + playback
5. `WebSocketClient.swift` - Connect to Python backend
6. `BackendManager.swift` - Launch Python as subprocess
7. Wire: hotkey -> capture -> WebSocket -> playback
8. `SettingsView.swift` - API key, voice, preferences
9. `TranscriptOverlay.swift` - Optional live transcript

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

## IPC Protocol

### Binary Frames (Audio)
- **Swift -> Python**: Raw PCM16 audio from mic (24kHz, mono)
- **Python -> Swift**: TTS audio for playback (24kHz, mono)

### Text Frames (Control)

Swift -> Python:
```json
{"type": "start_listening"}
{"type": "stop_listening"}
{"type": "interrupt"}
{"type": "set_voice", "voice": "alloy"}
{"type": "inject_context", "text": "..."}
```

Python -> Swift:
```json
{"type": "state_change", "state": "listening|thinking|speaking|idle|error"}
{"type": "transcript", "role": "user|assistant", "text": "...", "final": true}
{"type": "tool_start", "name": "reason_deeply", "args": {"task": "..."}}
{"type": "tool_end", "name": "reason_deeply", "result": "..."}
{"type": "error", "message": "..."}
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
