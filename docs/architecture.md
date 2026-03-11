# Samantha - Technical Architecture

## System Overview

```
+----------------------------------------------------------+
|  Swift macOS App (Samantha.app)                          |
|                                                          |
|  +------------------+  +-----------------------------+   |
|  | Floating Presence|  | Audio I/O                   |   |
|  | Widget UI        |  | - AVAudioEngine capture     |   |
|  | (NSPanel,        |  | - 24kHz PCM16 mono          |   |
|  |  SwiftUI overlay)|  | - Audio playback            |   |
|  +------------------+  | - Local barge-in detection   |   |
|                        +-----------------------------+   |
|  +------------------+                                    |
|  | Hotkey Manager   |  +-----------------------------+   |
|  | (Option+S)       |  | Settings UI                 |   |
|  +------------------+  | (SwiftUI sheet)             |   |
|                        +-----------------------------+   |
|  +------------------+                                    |
|  | IPC Client       |                                    |
|  | (WebSocket to    |                                    |
|  |  Python backend) |                                    |
|  +------------------+                                    |
+----------------------------------------------------------+
            |  WebSocket (ws://localhost:9090)
            |  - Binary frames: PCM audio (both directions)
            |  - Text frames: JSON control messages
            v
+----------------------------------------------------------+
|  Python Backend (subprocess, bundled in .app)            |
|                                                          |
|  +--------------------------------------------------+   |
|  | WebSocket Server (audio + control bridge)         |   |
|  +--------------------------------------------------+   |
|            |                                             |
|  +--------------------------------------------------+   |
|  | OpenAI Agents SDK Realtime                        |   |
|  |                                                   |   |
|  |  RealtimeAgent (voice session specialist)         |   |
|  |  - Session model: gpt-realtime*                   |   |
|  |  - Tools: memory_search, calendar, delegation     |   |
|  |  - Optional realtime handoffs to specialists      |   |
|  +--------------------------------------------------+   |
|            |                                             |
|  +--------------------------------------------------+   |
|  | Delegation Tool Layer                              |   |
|  | - Calls reasoning model: gpt-5-mini-2025-08-07    |   |
|  | - Returns compact result to active voice session   |   |
|  +--------------------------------------------------+   |
|            |                                             |
|  +--------------------------------------------------+   |
|  | Tool Layer                                          |  |
|  | - AppleScript MCP tools                             |  |
|  | - bash, file_read, file_write, web_search          |  |
|  | - memory_save, memory_search                        |  |
|  +--------------------------------------------------+   |
|            |                                             |
|  +--------------------------------------------------+   |
|  | Memory Layer                                        |  |
|  | ~/.samantha/ (profile, preferences, daily, db)     |  |
|  +--------------------------------------------------+   |
+----------------------------------------------------------+
```

## Key Constraints from Realtime + Agents SDK

1. Realtime model choice is configured at session level, not per `RealtimeAgent`.
2. Realtime handoffs are for switching realtime specialists on the same live session.
3. Voice can be set per agent/session, but cannot be changed after the session has already produced spoken audio.
4. For a different reasoning model during voice interaction, use delegation through tools/backchannel calls.

## Model Allocation

### Live voice loop
- Model: `gpt-realtime` (default) or a `gpt-realtime-mini*` snapshot for lower cost
- Modalities: audio input + audio output + text events
- Turn handling: `turn_detection` with `interrupt_response=true`

### Deep reasoning / long analysis
- Model: `gpt-5-mini-2025-08-07`
- Invocation style: non-realtime tool call from backend
- Output handling: summarize result and feed back into realtime session as assistant response context

## Component Details

### 1. Swift App (Frontend)

**Framework:** SwiftUI + AppKit hybrid  
**Target:** macOS 14+  
**Distribution:** DMG or Homebrew cask

#### Floating Presence Widget
- `NSPanel` with `.nonactivating` style
- `level: .floating` (always on top)
- Transparent background, SwiftUI overlay
- Draggable (save position to UserDefaults)
- Visual and motion behavior should follow `docs/design-direction.md`
- Primary shape is a continuous warm-orange loop rather than a generic orb
- States:
  - Idle
  - Listening
  - Thinking
  - Speaking
  - Error

Widget behavior rules:

- Motion is driven by app state plus smoothed speech energy
- The widget should not render as a literal waveform or equalizer
- Interruptions should cause an immediate visible state change
- The silhouette should remain recognizable in every state

#### Audio pipeline
- AVAudioEngine for mic capture and playback
- 24kHz PCM16 mono audio
- Swift streams mic chunks to Python over local WebSocket
- Python streams output audio chunks back to Swift
- Swift stops playback immediately on local barge-in signals

### 2. Python Backend

**Runtime:** Python 3.11+  
**Key dependency:** `openai-agents`  
**Packaging:** bundled in app or embedded framework

#### Startup flow
1. Swift launches backend subprocess.
2. Backend starts WebSocket server on `localhost:9090`.
3. Backend initializes memory, tool registry, MCP, and realtime runner.
4. Backend reports `idle` state to Swift.

#### Realtime session configuration (target)

```python
runner = RealtimeRunner(
    starting_agent=voice_agent,
    config={
        "model_settings": {
            "model_name": "gpt-realtime",
            "audio": {
                "input": {
                    "format": "pcm16",
                    "transcription": {"model": "gpt-4o-mini-transcribe"},
                    "turn_detection": {
                        "type": "semantic_vad",
                        "interrupt_response": True,
                    },
                },
                "output": {"format": "pcm16", "voice": "ash"},
            },
        },
        "async_tool_calls": True,
    },
)
```

#### Delegation pattern to `gpt-5-mini-2025-08-07`

```python
@function_tool
def reason_deeply(task: str) -> str:
    """Run deeper reasoning outside the realtime session model."""
    # Use standard non-realtime model invocation here.
    # Return concise result for speech output.
    ...
```

This tool can be called by the active realtime agent when it needs deeper reasoning.

### 3. Interruption semantics

#### Automatic (preferred)
- Configure `turn_detection.interrupt_response=true`.
- When user speech starts, realtime session can interrupt current response.
- Swift must still stop local playback immediately.

#### Manual (fallback)
- Swift sends `interrupt` control message.
- Backend calls `session.interrupt()`.
- If needed, backend can send raw `response.cancel`.
- Swift clears local playback buffers and resumes capture.

## IPC Protocol (Swift <-> Python)

### Binary frames (audio)
- Swift -> Python: raw PCM16 audio chunks
- Python -> Swift: raw PCM16 output chunks

### Text frames (control)

Swift -> Python:
```json
{"type": "start_listening"}
{"type": "stop_listening"}
{"type": "interrupt"}
{"type": "set_voice", "voice": "alloy"}
{"type": "inject_context", "text": "..."}
{"type": "approve_tool_call", "call_id": "call_123", "always": false}
{"type": "reject_tool_call", "call_id": "call_123", "always": false}
{"type": "get_state"}
```

Python -> Swift:
```json
{"type": "state_change", "state": "listening|thinking|speaking|idle|error"}
{"type": "transcript", "role": "user|assistant", "text": "...", "final": true}
{"type": "tool_start", "name": "reason_deeply", "args": {"task": "..."}}
{"type": "tool_end", "name": "reason_deeply", "result": "..."}
{"type": "tool_approval_required", "name": "file_write", "call_id": "call_123", "args": {"path": "..."}}
{"type": "clear_playback"}
{"type": "error", "message": "..."}
```

## Safety model

- Safe mode is a Phase 1 baseline, not a polish item.
- `bash` and file tools require explicit allow/deny policy.
- Destructive actions require confirmation unless explicitly disabled.
- Tool inputs are validated and fail fast on invalid paths/commands.

## Build Sequence

### Phase 1: Python backend first
1. Set up project + dependencies (`openai-agents`, `websockets`, `sqlite-vec`, `sentence-transformers`, `mcp`).
2. Implement prompts and base `RealtimeAgent`.
3. Implement safety baseline (`safe_mode`, command allowlist, file path validation).
4. Implement core tools (`bash`, `file_read`, `file_write`, `web_search`, memory tools).
5. Add realtime session config (`gpt-realtime`, turn detection, interruption behavior).
6. Implement delegation tool to `gpt-5-mini-2025-08-07`.
7. Integrate AppleScript MCP tools.
8. Add WebSocket bridge server for Swift.
9. Add tests for tools, memory, interruption, and delegation.

### Phase 2: Swift app
1. Create app shell (LSUIElement + SwiftUI).
2. Build widget window/view states based on `docs/design-direction.md`.
3. Add hotkey manager.
4. Implement capture/playback audio path.
5. Add WebSocket client and backend lifecycle manager.
6. Wire control flow: hotkey -> audio capture -> IPC -> playback.
7. Add settings, keychain API key, transcript overlay.

### Phase 3: Polish
1. Error recovery and reconnect behavior.
2. Launch at login.
3. Bundling and packaging.
4. Optional auto-update.

## Dependencies

### Python
```text
openai-agents          # RealtimeAgent/Runner/Session + tools
websockets             # Local Swift <-> Python IPC
sqlite-vec             # Vector similarity in SQLite
sentence-transformers  # Local embeddings (all-MiniLM-L6-v2)
mcp                    # AppleScript MCP integration
```

### Swift
```text
KeyboardShortcuts      # Global hotkey
LaunchAtLogin          # Login item support
Sparkle                # Optional auto-updates
```

## Data Storage

```text
~/.samantha/
├── profile.md
├── preferences.md
├── memory.db
├── daily/
└── config.json
```

## Reference Projects

| Project | Location | What to borrow |
|---------|----------|----------------|
| Rubber Duck | `~/repos/rubber-duck/` | Swift audio, hotkey, VAD, barge-in, orb UI |
| HAL-OS | `/mnt/c/Users/frase/OneDrive/HAL-OS/` | Memory architecture, profile/preferences, SQLite search |
| Macaw | `~/thebrownproject/macaw/` | Audio playback backends, daemon pattern |
| Looped | `~/thebrownproject/looped/` | WebSocket protocol patterns, state management |

## Open Questions

1. Packaging path: py2app vs embedded runtime vs external Python requirement.
2. Cost controls: usage budget and per-day limits for realtime and reasoning calls.
3. Proactive behavior: when should Samantha speak unprompted.
4. Optional future wake-word mode after push-to-talk V1 is stable.
