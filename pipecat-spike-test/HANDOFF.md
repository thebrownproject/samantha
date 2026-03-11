# Pipecat Spike - Handoff Notes

## Context

This spike evaluates migrating Samantha's backend from the OpenAI Agents SDK to Pipecat. The goal is provider flexibility and cost reduction (10-50x cheaper on the orchestrated path). Read the other docs in this folder for full details:

- `spec.md` -- scope, success criteria, what changes vs stays the same
- `architecture.md` -- current vs proposed architecture, component mapping, migration phases
- `provider-comparison.md` -- pricing tables, recommended builds, tradeoffs
- `findings.md` -- deep technical research on Pipecat core, Flows, transport, tool calling

## Key Decision: Two Paths

- **Path A**: OpenAI Realtime through Pipecat (same cost, same latency, but now swappable)
- **Path B**: Deepgram STT + Claude/GPT via OpenRouter + Cartesia TTS (10-50x cheaper, +200-400ms latency)

Start with Path A for feature parity, then build Path B to test cost/latency tradeoff.

## Cloned Repos

- `/Users/fraserbrown/repos/pipecat` -- Pipecat core framework
- `/Users/fraserbrown/repos/pipecat-flows` -- Structured conversation state machine add-on

## Most Important Files in Pipecat Repo

| File | Why |
|------|-----|
| `examples/foundational/19-openai-realtime-beta.py` | OpenAI Realtime + function calling through Pipecat -- closest to our current setup |
| `examples/foundational/06-listen-and-respond.py` | Basic orchestrated STT+LLM+TTS pipeline |
| `examples/foundational/14-function-calling.py` | Tool calling registration pattern |
| `examples/foundational/07-interruptible.py` | Interruption handling |
| `examples/foundational/37-mem0.py` | Memory integration pattern |
| `src/pipecat/transports/websocket/fastapi.py` | FastAPI WebSocket transport (our integration point) |
| `src/pipecat/services/openai/realtime/llm.py` | OpenAI Realtime service implementation |
| `src/pipecat/services/llm_service.py` | Base LLM service with tool calling logic |

## Biggest Integration Challenge

The **custom FrameSerializer**. Pipecat's FastAPI WebSocket transport expects a serializer to convert between Pipecat frames and wire format. We need one that maps our existing IPC protocol (protocol_version 1 JSON messages) to Pipecat frames so the Swift app works unchanged.

Messages to handle:
- `start_listening` / `stop_listening` -> pipeline control frames
- `interrupt` -> `InterruptionFrame`
- `state_change` -> custom frame or transport event
- `transcript` -> `TranscriptionFrame` metadata
- `tool_start` / `tool_end` / `tool_approval_required` -> custom frames
- `clear_playback` -> `InterruptionFrame` or transport signal
- `app_tool_call` / `app_tool_result` -> custom RPC through transport
- Binary PCM audio -> `InputAudioRawFrame` / `OutputAudioRawFrame`

## Open Questions for Spike

1. Can the FastAPI WebSocket transport coexist with our `app_tool_call` RPC pattern? (backend asks Swift to capture screen, waits for response)
2. Does Pipecat's OpenAI Realtime service support the `needs_approval` pattern for tool calls? Or do we reimplement with Flows?
3. What's the actual measured latency for Path B with Deepgram + Claude + Cartesia?
4. Can we hot-swap between Path A and Path B at runtime based on config?

## Quick Win Before the Spike

Swap `model_name` from `"gpt-realtime"` to `"gpt-realtime-mini"` in `backend/samantha/config.py` for an immediate 69% cost reduction with zero code changes. This is independent of the Pipecat migration.

## Current Backend State

All code is committed and pushed to main. The backend is working end-to-end:
- Voice conversation works
- All 12 tools work (bash, file_read, file_write, applescript, web_search, frontmost_app_context, capture_display, memory_save, memory_search, reason_deeply)
- Barge-in interruption works
- Tool approval flow works
- capture_display WebSocket crash is fixed (max_size=20MB)
- MCP has been fully removed, replaced with direct osascript tool
- Default voice is sage, transcription pinned to English
- All tests pass

## Session Bugs Still Open

- Calendar AppleScript queries sometimes timeout (model writes inefficient scripts, 30s limit)
- capture_display vision API call can be slow (OpenAI gpt-4o-mini vision)
- Model occasionally monologues when it should be concise
