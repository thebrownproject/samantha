# Samantha - Design Direction

This document defines the visual and motion language for Samantha's desktop presence.

## Design Goal

Samantha should feel alive, calm, intimate, and immediately responsive.

The UI is not meant to feel like a chatbot window, dashboard, or technical audio tool. It should feel like a persistent presence on the desktop that reacts naturally to conversation.

## Core Visual Direction

- The primary UI is a floating presence widget, not a literal orb.
- The widget should look like a warm orange continuous loop, similar to a rounded rectangular string or ribbon.
- The shape should feel elegant and recognizable even when frozen.
- The desktop around it should stay quiet. The widget is the focus.

## What To Avoid

- Generic glossy orb UI
- Raw waveform or equalizer-style visuals
- Busy assistant dashboards
- Cartoon face/avatar treatments
- Chaotic motion at high speaking volume

## Emotional Qualities

The motion and styling should read as:

- warm
- attentive
- restrained
- intimate
- alive

It should not read as:

- robotic
- flashy
- gamified
- cute
- overly technical

## Color and Material

- Primary accent: warm amber / burnt orange
- Avoid neon orange
- Use a soft glow rather than a hard bloom
- Keep the background treatment translucent and minimal
- Error states can shift warmer/redder, but should not feel alarming unless the system is truly broken

## Motion System

Motion should come from three inputs:

1. state
2. intent
3. smoothed audio energy

Raw audio should not directly drive the shape. The widget should have its own identity and inertia.

### State behaviors

- `idle`: very slow breathing, almost still
- `listening`: slightly wider, more tension, subtle readiness
- `thinking`: contained internal drift, folds, or twists
- `speaking`: asymmetric deformation driven by smoothed speech energy
- `interrupted`: immediate snap or collapse, then quick recovery into listening
- `error`: damped motion and warmer color shift

### Motion rules

- Preserve an overall stable silhouette in every state
- Favor spring-like motion over linear animation
- Keep transitions fast enough to feel responsive, but never twitchy
- Use audio as modulation, not as the entire animation system
- Interruptions should feel immediate

## Presence Rules

- The widget should feel alive even in silence
- It should remain legible at a glance from across the room
- The motion should suggest attention rather than decoration
- Transcript UI, if shown, is secondary to the presence widget

## App-Level Implications

- The main app window should prioritize the widget over traditional UI chrome
- Hotkey activation and interruption should visibly affect the widget immediately
- Playback, listening, thinking, and tool activity should each have a distinct but related motion signature
- Exact event-to-widget mapping should follow `docs/frontend-handoff.md`

## Implementation Guidance

- Use a single continuous path rather than multiple disconnected pieces
- Morph control points smoothly instead of swapping between unrelated shapes
- Low-pass or smooth speech energy before applying it to the path
- Keep a baseline idle animation running so the widget never feels dead
- The UI should still look intentional at 60fps on a laptop without excessive GPU work

## Inspiration Notes

The emotional target is Samantha in "Her": calm, present, and natural.

Reference products such as OS One are useful for understanding the minimal, voice-first interaction style, but Samantha should not copy another app directly. The goal is a distinct desktop-native presence with better tool use, memory, and system integration.
