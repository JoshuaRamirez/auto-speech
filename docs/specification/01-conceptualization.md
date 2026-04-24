# 01 — Conceptualization

## Vision

A single user, working in Claude Code on an Apple Silicon Mac, can issue a slash
command that takes a prior Claude response and speaks it aloud using a locally-hosted
TTS model — with audio playback beginning within a few seconds regardless of the
response's total length, and continuing without stalls until the full response has
been read. No cloud TTS. No data leaves the machine.

## Problem statement

Long Claude responses are expensive to read, especially during tasks where the user's
eyes or hands are occupied. The user wants to *hear* Claude read its own answer back,
verbatim in meaning but transformed into a form suitable for spoken delivery. Existing
cloud TTS either costs money per character, leaks conversation content to third
parties, or both. A local model on the user's hardware removes all three costs.

## Scope

### In scope
- Slash command `/speak [n]` inside Claude Code sessions.
- Default (no arg): speak the most recent assistant message.
- With arg `n` ≥ 1: speak the `n`th-most-recent assistant message.
- Audio-friendly rewrite that preserves semantic content (no summarization).
- Fibonacci-scaled buffered generation + playback.
- Local TTS on Apple Silicon (Kokoro via mlx-audio as initial engine).
- Voice calibration (one-time, per voice) that measures characters-per-second.
- Graceful degradation to single-shot generation for short responses.

### Out of scope (v1)
- Non-macOS platforms.
- Non-English voices.
- Resuming / scrubbing / pausing partway through playback (fire-and-forget only).
- Voice cloning.
- Text other than assistant messages (tool outputs, user messages, system messages).
- Speaking the current in-progress response (only prior, fully-written turns).

## Stakeholders & users
- **Primary user:** solo developer on M-series Mac with ≥ 32 GB RAM.
- **No other stakeholders.** Single-user tool; no multi-tenant concerns.

## Constraints

| Constraint | Source | Implication |
|---|---|---|
| Local only | user requirement | Choose a model that runs on-device. |
| Apple Silicon (M-series) | user's hardware | Prefer MLX-native models. |
| No detail loss in rewrite | user requirement | Rewrite transforms form, not content. |
| Works inside Claude Code session | user requirement | Must be callable as a slash command and read the session's own transcript. |
| Fast time-to-first-audio | user requirement | Fibonacci-scaled first chunk. |
| No audible gaps mid-response | user requirement | Generator must stay ahead of player. |

## Success criteria

A run of `/speak` on a typical 2–3 paragraph Claude response must:
1. Produce first audible output within ≤ 6 seconds of command invocation.
2. Play the full response without a single audible gap between chunks.
3. Faithfully convey every detail of the source response in spoken form.
4. Terminate cleanly (no orphan processes, no stray audio files retained).

## Non-functional requirements

| NFR | Target |
|---|---|
| First-audio latency | ≤ 6 s on M2 Pro / 32 GB. |
| Inter-chunk gap | 0 audible gap (< 100 ms transition). |
| Privacy | No network calls during speech path (calibration and rewrite may remain on-device; Claude invocation inside the slash command *is* on-device from the user's perspective since Claude Code is their own session). |
| Installability | One-shot `setup/install.sh` with user confirmation for heavy downloads. |
| Debuggability | Every generated chunk visible as a WAV file on disk until playback consumes it; orchestrator logs every state transition. |
| Recoverability | A crashed generator leaves partial WAVs but no corrupt state; re-invoking the command starts fresh. |

## Assumptions
- User has Python 3.11+ and Homebrew available.
- `afplay` is available (macOS native).
- User's default shell is zsh.
- Claude Code writes session transcripts as JSONL under `~/.claude/projects/<slug>/<session-id>.jsonl`. (ADR-003 locks this in; if the format changes, the transcript locator must be updated.)

## Out-of-band dependencies
- `mlx`, `mlx-audio`, Kokoro model weights (downloaded once).
- `afplay` (shipped with macOS).

## See also
- [Context diagram](diagrams/context-diagram.mmd)
- [Glossary](glossary.md)
