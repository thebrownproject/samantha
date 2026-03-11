# First Mac Session

This is the concrete bring-up checklist for the first real macOS build of Samantha.

The goal of the first session is not to finish the app. The goal is to get a buildable shell running with:

- floating presence widget
- websocket connection to the Python backend
- mic capture
- playback
- one verified visual-context path

## Tracking

The current Beads epic for this work is `sam-nze`.

- `sam-nze.1` Create the Xcode project, app target, and smoke test target
- `sam-nze.2` Wire the checked-in Swift sources, plist, entitlements, and KeyboardShortcuts package
- `sam-nze.3` Fix and verify WebSocket lifecycle and backend handshake behavior
- `sam-nze.4` Verify the audio capture and playback loop and tighten AudioManager teardown behavior
- `sam-nze.5` Validate `frontmost_app_context` and `capture_display` on real macOS permissions
- `sam-nze.6` Run the first Mac session checklist end to end and record findings
- `sam-nze.7` Clean app docs and backlog after the first Mac run

If this repo is freshly cloned onto the Mac and `bd` does not yet show these issues locally, rehydrate the tracker from the repo-backed JSONL before starting work:

```bash
bd init --from-jsonl --force --prefix sam
```

Use the issue descriptions plus this document as the portable source of truth for clone-critical context. The current repo-backed Beads export restores issues and dependencies correctly, but not issue comment bodies.

## 1. Create the Xcode project

Create a new macOS App project in Xcode with:

- Product Name: `Samantha`
- Interface: `SwiftUI`
- Language: `Swift`
- Bundle Identifier: `com.thebrownproject.samantha`
- Minimum macOS: `14.0`

Then wire the checked-in source tree into the project:

- add files from `app/Samantha/`
- use [Info.plist](/home/fraser/thebrownproject/samantha/app/Samantha/Info.plist)
- use [Samantha.entitlements](/home/fraser/thebrownproject/samantha/app/Samantha/Samantha.entitlements)

Create a test target immediately, even if it only contains one smoke test at first.

## 2. Target settings

Set these project/target basics:

- app target name: `Samantha`
- signing enabled with your normal Apple development team
- `Info.plist File` -> `app/Samantha/Info.plist`
- `Code Signing Entitlements` -> `app/Samantha/Samantha.entitlements`

Current checked-in plist keys already cover:

- `LSUIElement`
- `NSMicrophoneUsageDescription`
- `NSAppleEventsUsageDescription`

Current checked-in entitlements already cover:

- `com.apple.security.network.client`
- `com.apple.security.device.audio-input`

Inference: for this first dev-phase app, the checked-in entitlements are fine because app sandbox is off. If you later sandbox the app, revisit the entitlement set and automation permissions carefully.

## 3. Add packages

Add the package dependencies the source already expects:

- `KeyboardShortcuts`

Do not add more packages yet unless the build demands them.

## 4. First compile order

Build in this order so failures are easy to isolate:

1. compile the app with the orb window only
2. compile `WebSocketClient.swift`
3. compile `AudioManager.swift`
4. compile `SettingsView.swift` and `KeychainHelper.swift`
5. compile `DesktopContextToolExecutor.swift`
6. run the app with the Python backend already started manually

Do not start by trying to bundle Python into the app. Use the backend in dev mode first.

## 5. Dev runtime setup

Before launching the app from Xcode:

```bash
cd /home/fraser/thebrownproject/samantha/backend
./.venv/bin/samantha
```

The first app-side target is simple:

- app launches
- orb appears
- websocket connects to `ws://localhost:9090`
- `get_state` succeeds

Only after that should you verify mic and playback.

Settings source of truth for the first Mac run:

- backend runtime settings remain canonical in `~/.samantha/config.json`
- the app can cache UI state locally, but it must sync `voice`, `safe_mode`, `confirm_destructive`, and `memory_enabled` back to backend config rather than letting `UserDefaults` drift on their own
- the OpenAI API key can stay in macOS Keychain, but the launched backend process still needs it in `OPENAI_API_KEY`
- `set_voice` should be treated as a next-session change unless the backend explicitly tears down and recreates the realtime session

## 6. Permissions to verify

### Microphone

Expected behavior:

- prompt appears on first mic capture
- app receives live mic data

This is backed by the `NSMicrophoneUsageDescription` key and the audio-input entitlement.

### Apple Events

Expected behavior:

- prompt appears when Samantha first queries Safari, Chrome, Finder, or another AppleScript-backed app
- `frontmost_app_context` can return a browser URL or Finder path when available

This is backed by `NSAppleEventsUsageDescription`.

### Screen Recording

Expected behavior:

- first call to `capture_display` triggers the system screen-recording permission flow
- after permission is granted, screenshots contain the real display instead of a restricted result

Inference from current Apple docs and platform behavior:

- screen recording permission is TCC-driven at runtime
- you should verify it by actually calling `capture_display` from the running app
- the app should be tested as a real `.app` bundle, not just as an arbitrary command-line executable

### Accessibility

Do not request this yet.

It is not required for the current first phase:

- `frontmost_app_context`
- `capture_display`

Save Accessibility permission for later computer-use work.

## 7. First runtime checks

Run these checks in order:

1. launch app from Xcode
2. confirm orb window appears and stays floating
3. confirm websocket connects without crashing
4. press hotkey and verify microphone prompt
5. speak a short turn and confirm backend receives audio
6. confirm assistant audio plays back
7. interrupt assistant playback and confirm it stops immediately
8. call `frontmost_app_context` while Safari or Finder is frontmost
9. call `capture_display` and verify the screen-recording prompt path

## 8. Known code issues to check during first compile

### `WebSocketClient.swift`

[Fixed] The false-connected bug has been resolved. Connection state is now deferred to `URLSessionWebSocketDelegate.didOpenWithProtocol` instead of being set immediately after `resume()`. The client also sets `maximumMessageSize` to 20 MB to support large `capture_display` payloads.

### `AudioManager.swift`

[Fixed] Engine churn from `stopInputCapture()` has been addressed. Input capture stop is now separated from full engine teardown.

## 9. What not to do on day one

- do not bundle Python yet
- do not sandbox the app yet
- do not add Accessibility yet
- do not build generalized computer use yet
- do not add more tools before the voice loop works

## 10. Definition of success for the first Mac session

The first Mac session is successful if all of this is true:

- the app builds in Xcode
- the orb launches
- websocket connects to the backend
- a mic turn reaches the backend
- assistant audio plays
- manual interrupt works
- `frontmost_app_context` works in at least one supported app
- `capture_display` works after permission is granted

Anything beyond that is second-session work.
