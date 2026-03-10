# Samantha - Product Specification

## Vision

A native macOS AI companion with a voice-first interface. A floating orb that lives on your desktop, listens when activated, speaks back naturally, and has full access to your computer. Persistent memory means it knows who you are, what you're working on, and how you like things done.

Think Scarlett Johansson in "Her", but real, running locally on your Mac.

## Core Experience

1. A translucent floating orb sits on your desktop (always on top, draggable)
2. Press a hotkey (e.g., Option+S) or click the orb to activate
3. Speak naturally: "Hey, what meetings do I have tomorrow?"
4. Samantha responds conversationally with near-instant audio
5. She checks your calendar via AppleScript, reads back the results
6. You can interrupt her mid-sentence (barge-in) and she adapts
7. When idle, the orb pulses gently. When listening, it glows. When speaking, it animates.

## Core Capabilities

### Voice Conversation
- Natural, low-latency speech-to-speech via OpenAI Realtime API
- Sub-second response time for simple conversation
- Built-in VAD interruption with `turn_detection.interrupt_response=true`
- Manual interruption fallback (`interrupt` button/hotkey)
- Configurable voice selection
- Push-to-talk (hotkey) with option for always-listening mode later

### Computer Access
- **AppleScript MCP**: Calendar, Reminders, Finder, Music/Spotify, Notes, System
- **Bash tool**: Run shell commands (git, scripts, file operations)
- **File read/write**: Create, read, and edit files anywhere on the system
- **Web search**: Look things up on the internet
- **App control**: Open apps, switch windows, control playback

### Persistent Memory
- Remembers who you are (profile, preferences, relationships)
- Remembers past conversations (searchable history)
- Learns your patterns over time (work schedule, common requests)
- Two-layer memory: daily logs (append-only) + curated knowledge (promoted)
- SQLite + vector search for semantic recall

### Intelligence
- Live voice session uses a realtime audio model (`gpt-realtime*`)
- Deep reasoning is delegated to `gpt-5-mini-2025-08-07` through tool/backchannel calls
- Realtime handoffs are for switching specialists inside the same live session
- The user should not notice internal delegation boundaries

## Model Strategy (Locked)

- **Live voice model**: `gpt-realtime` (or a `gpt-realtime-mini*` snapshot for lower cost)
- **Reasoning model**: `gpt-5-mini-2025-08-07`
- **Constraint**: Model choice is session-level for realtime runs, so `gpt-5-mini-2025-08-07` is not the direct speech model in the active voice session

## User Personas

**Primary: Fraser (the builder)**
- Power user, developer, wants full computer control
- Values speed and directness over safety theatre
- Wants proactive assistance ("You have a meeting in 10 minutes")
- Uses it for: scheduling, file management, quick lookups, coding assistance, system control

**Secondary: General Mac user**
- Less technical, wants a helpful assistant
- Values safety guardrails (confirmation before destructive actions)
- Uses it for: calendar, reminders, music, notes, web search

## Non-Goals (V1)

- No iOS/mobile version
- No multi-user support
- No cloud sync (everything local)
- No screen/camera vision (future)
- No always-listening wake word (future, start with hotkey activation)
- No third-party integrations beyond AppleScript and bash

## Settings

- OpenAI API key (stored in macOS Keychain)
- Voice selection (from OpenAI Realtime voices)
- Hotkey configuration
- Launch at login toggle
- Safe mode toggle (restricts bash to allowlist)
- Confirmation prompts for destructive actions (on/off)
- Memory on/off
- Orb position, size, opacity

## Privacy

- All memory stored locally (`~/.samantha/`)
- API key in macOS Keychain
- Audio streamed to OpenAI for processing (not stored by us)
- No telemetry, no analytics, no accounts
- User can clear all memory at any time
