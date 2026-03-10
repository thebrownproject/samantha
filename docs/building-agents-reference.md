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

## 5) Interruption Flows

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

## 6) Delegation Tool Pattern

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

## 7) Safety Baseline (Phase 1)

- Safe mode defaults ON for development.
- Validate every file path before file tool operations.
- Restrict shell commands to explicit allowlist patterns.
- Require confirmation for destructive operations.
- Fail fast with specific error messages.

## 8) Build Checklist

- [ ] Realtime session starts and streams audio both directions.
- [ ] `interrupt_response` works and local playback stops instantly.
- [ ] Manual `interrupt` button works every time.
- [ ] Delegation to `gpt-5-mini-2025-08-07` works and returns into voice flow.
- [ ] Safe mode blocks disallowed shell/file actions.
- [ ] Memory save/search tools pass tests.
- [ ] State machine transitions are deterministic under interruptions.

## 9) Recommended Test Cases

1. User interrupts assistant mid-sentence with speech.
2. User interrupts assistant with manual stop button.
3. User injects extra text context while tool call is running.
4. Delegation tool returns late while user starts a new turn.
5. Backend reconnect after temporary websocket failure.

## 10) Sources to Keep Handy

- Realtime API reference: `https://developers.openai.com/api/reference/resources/realtime`
- Agents Python realtime guide: `https://github.com/openai/openai-agents-python/blob/main/docs/realtime/guide.md`
- Agents Python realtime quickstart: `https://github.com/openai/openai-agents-python/blob/main/docs/realtime/quickstart.md`
- Agents Python realtime config: `https://github.com/openai/openai-agents-python/blob/main/src/agents/realtime/config.py`
- Agents JS voice handoff behavior: `https://github.com/openai/openai-agents-js/blob/main/docs/src/content/docs/guides/voice-agents/build.mdx`
