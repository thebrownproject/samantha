# Samantha - Building Agents Reference

This document is the implementation-time reference for agent behavior, model routing, realtime events, and interruption handling.

## 1) Canonical Model Plan

- Live voice session model: `gpt-realtime` (or `gpt-realtime-mini*` snapshot)
- Delegated reasoning model: `gpt-5-mini-2025-08-07`
- Speech transcription model: `gpt-4o-mini-transcribe` (session audio input transcription)

## 2) Why this split

- Realtime session model is set at session level.
- Realtime handoffs are for realtime specialists on one live session.
- If you need a stronger reasoning model, delegate through tools and return a concise summary to the realtime session.

## 3) Realtime Session Baseline Config

```python
realtime_config = {
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
            "output": {
                "format": "pcm16",
                "voice": "ash",
            },
        },
        "tool_choice": "auto",
    },
    "async_tool_calls": True,
}
```

## 4) Event Mapping (Realtime API -> Samantha app state)

- `input_audio_buffer.speech_started` -> Swift should stop local playback now
- `response.output_audio.delta` -> state: `speaking`
- `response.output_audio.done` + `response.done` -> transition toward `idle`
- `response.function_call_arguments.delta/done` -> backend tool call decode
- `conversation.item.input_audio_transcription.delta/completed` -> user transcript updates
- `error` -> state: `error`

## 5) Presence Widget Rules

The app should render a floating presence widget rather than a generic orb. The detailed visual system lives in `docs/design-direction.md`.
The exact websocket-event to widget-state mapping lives in `docs/frontend-handoff.md`.

Implementation rules:

- Use one continuous loop-like shape as the core widget
- Drive motion from state plus smoothed audio energy, not a raw waveform
- Keep the silhouette recognizable across `idle`, `listening`, `thinking`, `speaking`, and `error`
- Interruptions should visibly snap the widget back to listening immediately
- Transcript UI is secondary to the presence widget

## 6) Interruption Flows

### Automatic barge-in
1. User speaks while assistant is speaking.
2. Realtime VAD detects speech start.
3. Backend receives speech-start event.
4. Swift stops audio playback immediately.
5. New user turn continues from the active session.

### Manual interruption
1. User taps stop / hotkey.
2. Swift sends `{"type":"interrupt"}` to backend.
3. Backend calls `session.interrupt()`.
4. Backend can optionally send raw `response.cancel` for explicit control.
5. Swift clears local playback queue and resumes user capture.

### Tool approvals
1. Backend emits `{"type":"tool_approval_required", ...}` when a destructive tool needs approval.
2. Swift responds with `{"type":"approve_tool_call","call_id":"..."}` or `{"type":"reject_tool_call","call_id":"..."}`.
3. Backend resumes or rejects the pending tool call and logs the decision.

## 7) Delegation Tool Pattern

Use this when the active realtime agent needs deeper reasoning.

```python
@function_tool
def reason_deeply(task: str) -> str:
    """Calls gpt-5-mini-2025-08-07 and returns a concise answer."""
    ...
```

Guidelines:
- Keep returned text concise for voice playback.
- Include enough structure for follow-up actions.
- Log delegation start/end via IPC `tool_start`/`tool_end` events.

## 8) Safety Baseline (Phase 1)

- Safe mode defaults ON for development.
- Validate every file path before file tool operations.
- Restrict shell commands to explicit allowlist patterns.
- Require confirmation for destructive operations.
- Fail fast with specific error messages.

## 9) Build Checklist

- [ ] Realtime session starts and streams audio both directions.
- [ ] `interrupt_response` works and local playback stops instantly.
- [ ] Manual `interrupt` button works every time.
- [ ] Delegation to `gpt-5-mini-2025-08-07` works and returns into voice flow.
- [ ] Safe mode blocks disallowed shell/file actions.
- [ ] Memory save/search tools pass tests.
- [ ] State machine transitions are deterministic under interruptions.
- [ ] Presence widget motion matches the design direction and does not degrade into a waveform/equalizer look.

## 10) Recommended Test Cases

1. User interrupts assistant mid-sentence with speech.
2. User interrupts assistant with manual stop button.
3. User injects extra text context while tool call is running.
4. Delegation tool returns late while user starts a new turn.
5. Backend reconnect after temporary websocket failure.
6. Widget visibly snaps from `speaking` to `listening` on interruption.

## 11) Mock Client Harness

Before the macOS app is available, use the backend mock client harness to exercise the websocket contract directly:

```bash
cd backend && .venv/bin/python -m samantha.mock_client --get-state
cd backend && .venv/bin/python -m samantha.mock_client --start-listening --audio-file /path/to/sample.pcm --stop-listening
cd backend && .venv/bin/python -m samantha.mock_client --auto-approve --idle-timeout 5
```

The harness speaks protocol version `1`, prints JSON/audio events, and can auto-approve or auto-reject tool approval prompts.

## 12) Structured Tool and Log Contracts

### `web_search`

`web_search` returns a JSON string with this shape:

```json
{"query":"...","summary":"...","results":[{"title":"...","url":"https://..."}],"error":"..."}
```

Rules:

- `query` is always present.
- `summary` is always present and may be empty.
- `results` is always present and may be empty.
- `error` is present only when the search fails.
- Callers that need structure should `json.loads(...)` the tool result before inspection.

### Daily log entries

The realtime runtime writes structured JSON strings into the daily log so later memory promotion and inspection remain machine-readable.

Conversation turn:

```json
{"kind":"conversation_turn","role":"user|assistant","text":"...","final":true}
```

Memory promotion signal:

```json
{"kind":"memory_promotion_signal","tool":"memory_save","content":"...","tags":"comma,separated"}
```

### Approval semantics

- Destructive tools can emit `tool_approval_required`.
- The client must answer with the exact `call_id`.
- `always=true` maps to a persistent approval preference at the SDK/session layer when supported.
- The frontend behavior for approval prompts is defined in `docs/frontend-handoff.md`.

## 13) Sources to Keep Handy

- Agents Python realtime guide: `https://openai.github.io/openai-agents-python/realtime/guide/`
- Agents Python realtime transport: `https://openai.github.io/openai-agents-python/realtime/transport/`
- Agents Python `RealtimeSession` reference: `https://openai.github.io/openai-agents-python/ref/realtime/session/`
- Agents Python human-in-the-loop guide: `https://openai.github.io/openai-agents-python/human_in_the_loop/`
- Realtime client events reference: `https://platform.openai.com/docs/api-reference/realtime-client-events`
- Realtime VAD guide: `https://platform.openai.com/docs/guides/realtime-vad`
