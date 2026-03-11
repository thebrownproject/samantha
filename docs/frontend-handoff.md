# Samantha - Frontend Handoff Spec

This document defines how the future Swift client should translate backend websocket traffic into widget state, audio behavior, transcript UI, and approval prompts.

## Source of Truth

- `docs/ipc-protocol.md` is the canonical wire contract.
- `docs/design-direction.md` is the canonical visual and motion language.
- Backend `state_change` messages are the source of truth for persistent widget state.
- Swift may use brief local presentation-only states such as `connecting`, `pressed`, or `interrupted`, but it must reconcile to the next backend `state_change`.
- Binary audio frames are transport payloads, not a separate state machine.

## Canonical Widget States

| Backend signal | Widget state | Visual behavior | Client behavior |
| --- | --- | --- | --- |
| `state_change: idle` | `idle` | slow breathing, nearly still | no playback, no approval prompt, transcript can fade |
| `state_change: listening` | `listening` | slightly wider loop, higher tension | mic capture should be active or ready |
| `state_change: thinking` | `thinking` | contained drift or folds | no assistant playback, tool/approval UI may be visible |
| `state_change: speaking` | `speaking` | asymmetric motion modulated by smoothed playback energy | play binary PCM audio immediately |
| `state_change: error` | `error` | damped motion, warmer/redder tint | stop playback, show error copy, allow retry |

## Transient Reactions

### `clear_playback`

- Stop local playback immediately. Do not wait for any other event.
- Trigger a short `interrupted` snap animation lasting roughly `120-250ms`.
- After that snap, settle into the latest canonical backend state.
- If a local manual interrupt already cleared playback, the later backend `clear_playback` should be idempotent.

### Local disconnects

- Treat websocket disconnect as a local transport condition, not a canonical Samantha state.
- Stop playback and mic capture immediately.
- Render a lightweight offline/error treatment until reconnect succeeds.
- On reconnect, send `get_state` before restoring normal controls.

## Event Handling Rules

### Binary audio frames

- Format: PCM16 mono at `24kHz`.
- Buffer and play frames gaplessly.
- Use smoothed playback energy to modulate the speaking animation.
- Do not render a literal waveform or equalizer.
- Audio arrival alone must not permanently override the canonical widget state.

### `transcript`

- `role` is always `user` or `assistant`.
- `final=false` updates the live transcript line for that role.
- `final=true` commits the line into transcript history and replaces any matching partial text.
- Empty or duplicate transcript messages should be ignored.
- The transcript overlay is secondary UI and should never replace the presence widget as the focal element.

### `tool_start` and `tool_end`

- These messages add secondary status UI only. They do not own the primary widget state.
- `tool_start` can surface a subtle label such as `Searching`, `Saving memory`, or `Writing file`.
- `tool_end` can replace that label with a brief result summary, then fade.
- If backend `state_change` says `thinking`, keep the widget in `thinking` even while tool status is shown.

### `tool_approval_required`

- Present an explicit approval prompt keyed by `call_id`.
- The prompt should include:
  - tool name
  - a concise argument summary
  - `Approve` and `Reject` actions
  - an optional `Always allow` / `Always reject` toggle that maps to the `always` flag
- Keep the widget in the backend-reported state, usually `thinking`.
- Do not infer approval completion locally; wait for subsequent backend events.

### `error`

- Surface the error message in compact text near the widget or transcript overlay.
- Stop local playback.
- Keep the UI recoverable: reconnect, retry, or dismiss paths should remain available.

## Client-Initiated Controls

### Start listening

1. Send `{"protocol_version": 1, "type": "start_listening"}`.
2. Begin mic capture immediately after the websocket write succeeds.
3. An optimistic `listening` animation is acceptable for up to `150ms`, but the next backend `state_change` wins.

### Stop listening

1. Send `{"protocol_version": 1, "type": "stop_listening"}`.
2. Stop feeding new mic audio after the control message.
3. Wait for backend `thinking`, `speaking`, or `idle` updates instead of guessing the next state.

### Manual interrupt

1. Clear local playback immediately.
2. Send `{"protocol_version": 1, "type": "interrupt"}`.
3. Keep capture ready for the next user turn.
4. Apply the transient `interrupted` animation even before the backend echoes `clear_playback`.

### Context injection

- Send `inject_context` only while connected.
- Treat this as a backend context update, not as a transcript line typed by the user.

### Voice changes

- Send `set_voice` while idle when possible.
- If changed mid-session, the UI should communicate that the new voice applies to the next session unless the backend explicitly restarts the session.

## Transcript and Overlay Behavior

- Keep one live partial line per role.
- When `final=true`, move that line into transcript history and clear the role's partial slot.
- User transcript can remain visible while Samantha is `thinking`.
- Assistant transcript can remain visible while Samantha is `speaking` and for a short fade after `idle`.
- Approval prompts take visual priority over transcript chrome, but not over the core presence widget.

## Approval-Flow Semantics

- The backend owns whether a tool call is pending approval.
- The client must respond with the exact `call_id` it received:

```json
{"protocol_version": 1, "type": "approve_tool_call", "call_id": "call_123", "always": false}
{"protocol_version": 1, "type": "reject_tool_call", "call_id": "call_123", "always": false}
```

- If the websocket reconnects, discard stale approval prompts and wait for the backend to re-emit any still-pending approval state.

## Implementation Checklist for Swift

- Request `get_state` on connect and reconnect.
- Treat backend `state_change` as canonical.
- Apply `clear_playback` immediately and idempotently.
- Keep transcript UI secondary to the widget.
- Keep tool status and approval UI additive rather than state-owning.
- Drive speaking animation from smoothed playback energy, not a raw waveform.
