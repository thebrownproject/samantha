# Samantha - IPC Protocol

This document defines the local WebSocket protocol between the Swift app and the Python backend.

## Versioning

- Transport: local WebSocket on `ws://localhost:9090`
- Binary frames: PCM16 audio
- Text frames: JSON control and event messages
- Current protocol version: `1`

Every JSON text message in both directions must include:

```json
{
  "protocol_version": 1,
  "type": "..."
}
```

## Compatibility Policy

- Version `1` is the only supported protocol version right now.
- Messages missing `protocol_version` are rejected.
- Messages with an unsupported `protocol_version` are rejected.
- New message types or fields must not silently change the meaning of existing version `1` messages.
- If the wire contract changes incompatibly, bump `protocol_version`.

## Swift -> Python Messages

### Start listening

```json
{"protocol_version": 1, "type": "start_listening"}
```

### Stop listening

```json
{"protocol_version": 1, "type": "stop_listening"}
```

### Interrupt

```json
{"protocol_version": 1, "type": "interrupt"}
```

### Set voice

```json
{"protocol_version": 1, "type": "set_voice", "voice": "alloy"}
```

### Inject context

```json
{"protocol_version": 1, "type": "inject_context", "text": "User is tired today."}
```

### Approve tool call

```json
{"protocol_version": 1, "type": "approve_tool_call", "call_id": "call_123", "always": false}
```

### Reject tool call

```json
{"protocol_version": 1, "type": "reject_tool_call", "call_id": "call_123", "always": false}
```

### Get current state

```json
{"protocol_version": 1, "type": "get_state"}
```

## Python -> Swift Messages

### State change

```json
{"protocol_version": 1, "type": "state_change", "state": "idle"}
```

`state` is one of:

- `idle`
- `listening`
- `thinking`
- `speaking`
- `error`

### Transcript

```json
{"protocol_version": 1, "type": "transcript", "role": "assistant", "text": "Hello there.", "final": true}
```

### Tool start

```json
{"protocol_version": 1, "type": "tool_start", "name": "reason_deeply", "args": {"task": "..." }}
```

### Tool end

```json
{"protocol_version": 1, "type": "tool_end", "name": "reason_deeply", "result": "..." }
```

### Tool approval required

```json
{"protocol_version": 1, "type": "tool_approval_required", "name": "file_write", "call_id": "call_123", "args": {"path": "..."}}
```

### Clear playback

```json
{"protocol_version": 1, "type": "clear_playback"}
```

### Error

```json
{"protocol_version": 1, "type": "error", "message": "Unsupported protocol_version: 2. Supported versions: 1"}
```

## Validation Rules

- Audio frames are accepted only while the backend is in a listening turn.
- Invalid JSON is rejected with an `error` message.
- Missing or invalid required fields are rejected with an `error` message.
- Unknown message types are rejected with an `error` message.
- Outbound backend messages are versioned before being sent on the wire.

## Notes

- The backend is the source of truth for current app state.
- Binary audio frames are intentionally unversioned; versioning applies to JSON message envelopes.
- The widget-state mapping built on top of these messages is documented in `docs/frontend-handoff.md`.
