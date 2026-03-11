# Samantha - Observability and Triage

This document defines the backend runtime signals, standard log fields, and the first-pass incident triage workflow for Samantha development and beta testing.

## Standard Log Fields

Use these identifiers consistently when adding or reviewing logs:

- `session_id`: lifecycle of one backend realtime runtime session
- `turn_id`: one user turn inside a session
- `call_id`: tool approval call identifier from the Agents SDK
- `cid`: delegated reasoning correlation identifier in `reason_deeply`
- `model`: model name for delegated calls or realtime session logs
- `duration`: total duration of an operation
- `latency`: time to first meaningful output, usually first audio
- `failure_category`: stable failure bucket such as `timeout` or `error`
- `error_type`: exception class when relevant

## Key Runtime Signals

### Realtime session health

- `Realtime session connected session_id=...`
- `Session disconnected: ...`
- `Session reconnected (attempt N)`
- `Reconnecting in ...`
- `Max reconnect attempts ... exceeded`

### Turn lifecycle

- `Turn started session_id=... turn_id=...`
- `First audio session_id=... turn_id=... latency=...`
- `Turn finished session_id=... turn_id=... outcome=... duration=... first_audio_latency=...`
- `Turn interrupted session_id=... turn_id=... mode=manual|vad`

### Tool approval flow

- `Tool approval required call_id=... tool=...`
- `Approving tool call ...`
- `Rejecting tool call ...`

### Delegation flow

- `reason_deeply start cid=... model=...`
- `reason_deeply success ...`
- `reason_deeply timeout ... failure_category=timeout`
- `reason_deeply error ... failure_category=error`
- `reason_deeply failure ...`

## Primary Health Checks

When validating the backend, check these in order:

1. Websocket server starts and binds on the expected port
2. Realtime session connects successfully
3. `start_listening` creates a new turn and logs a `turn_id`
4. Assistant output produces a `First audio` log within an acceptable time
5. Turn completion returns to `idle` cleanly
6. Interrupts produce immediate playback clearing and interruption logs
7. Tool approvals surface `tool_approval_required` and approve/reject decisions

## Common Failure Scenarios

### 1. Backend starts but no response arrives

Check:

- `OPENAI_API_KEY` is present
- `Realtime session connected` appears
- the client sent `protocol_version: 1`
- the backend actually received audio while `listening` was true

Likely causes:

- API key missing
- websocket client sent malformed control messages
- audio was sent before `start_listening`
- realtime connection failed before the turn began

### 2. Assistant audio never starts

Check:

- `Turn started`
- `First audio`
- any `error` events from the runtime
- delegation/tool logs that might indicate a long tool wait

Likely causes:

- upstream model delay
- tool call blocked on approval
- interruption occurred before audio started

### 3. Playback is not cleared on interruption

Check:

- `Turn interrupted ... mode=vad|manual`
- outbound `clear_playback` websocket event
- app/client handling of `clear_playback`

Likely causes:

- client failed to honor `clear_playback`
- interruption event never reached the runtime
- audio was already complete before interruption fired

### 4. Tool call hangs waiting for approval

Check:

- `tool_approval_required` event reached the client
- client responded with `approve_tool_call` or `reject_tool_call`
- backend log shows the approve/reject decision

Likely causes:

- client did not surface the approval prompt
- wrong `call_id`
- protocol mismatch

### 5. `capture_display` disconnects the WebSocket

Check:

- message size of the `app_tool_result` payload (base64 screenshots can be 5-10 MB)
- `max_size` on the Python `websockets.serve()` call (must be >= 20 MB)
- `maximumMessageSize` on the Swift `URLSessionWebSocketTask` (must match)
- backend logs for connection-closed errors around the tool call

Likely causes:

- WebSocket frame exceeds the server or client max message size
- large retina screenshots producing oversized base64 payloads

### 6. Repeated session reconnects

Check:

- `Session disconnected`
- reconnect attempt count
- max retries reached

Likely causes:

- upstream connectivity instability
- invalid realtime session state
- repeated runtime exception in the session loop

## Recovery Procedures

### Local development recovery

1. Restart the backend
2. Reconnect the client or mock harness
3. Request `get_state`
4. Run a short validation turn
5. Confirm `Turn started`, `First audio`, and `Turn finished`

### Approval-flow recovery

1. Confirm the pending `call_id`
2. Re-send the explicit approval or rejection
3. If the session is stale, restart the backend and retry the turn

### Realtime-session recovery

1. Inspect disconnect logs and reconnect attempts
2. If retries are exhausted, restart the backend
3. Re-run the minimal realtime smoke test

## Tested Paths

These workflows are covered by automated tests:

- websocket contract validation
- runtime turn lifecycle logging
- interruption behavior
- session reconnect behavior
- delegation telemetry
- realtime smoke tests

Relevant suites:

- `backend/tests/test_ws_server.py`
- `backend/tests/test_runtime.py`
- `backend/tests/test_session_manager.py`
- `backend/tests/test_tools.py`
- `backend/tests/test_e2e.py`
