# ADR-012 — Autoplay-on-Stop via Claude Code hook (opt-in, global, detached)

**Status:** Accepted
**Date:** 2026-04-29
**Depends on:** ADR-008 (cache), ADR-009 (mpv controller),
ADR-011 (CLI rewriter).

## Context
The web app (Phase 14) and the slash command `/speak` are pull-mode: the
user has to *ask* for audio. While working in Claude Code, the user wants
audio of every assistant response without having to type anything.

Claude Code exposes a **`Stop` hook** that fires after each assistant
turn completes. Hooks are shell commands; they can't invoke an in-session
Claude. So they can't do the rewrite the same way `/speak` does. But they
*can* invoke `claude -p`, which is precisely the rewriter we already
built for Phase 14.

## Decision

**Add an opt-in, global Stop hook that auto-runs the same backend the
web app uses: extract the just-finished assistant turn → `claude -p`
rewrite → `speak.py --source-hash`.** All TTS, caching, and playback
go through the existing pipeline + cache + mpv controller — no new
code paths in the audio stack.

### Three properties locked in
1. **Opt-in.** Default state is "uninstalled, disabled."
   `setup/install-hook.sh` registers the hook in `~/.claude/settings.json`.
   `setup/uninstall-hook.sh` removes it. **Idempotent both ways.**

2. **Global.** Lives in `~/.claude/settings.json`, applies to every
   project. The user almost never wants per-project autoplay logic.

3. **Disable marker for fast pause.** Hook checks for
   `~/.claude/auto-speech.disabled` at the very top. If present → exit 0
   without running. `/autoplay-on` and `/autoplay-off` slash commands
   create/remove the marker in one click. This is a vastly better UX
   than uninstall/reinstall.

### Detached execution

The Stop hook runs **synchronously** from Claude Code's perspective —
the hook subprocess blocks the user's next prompt until it returns.
Rewriting + TTS + mpv start can take 6–30 s. We refuse to make the
user wait that long.

**Solution:** the hook script does only fast checks (~50 ms), then
spawns an independent worker via `setsid` + `&` + `disown`, and exits
immediately. The worker runs the full rewrite + speak pipeline in the
background. Claude Code returns control to the user instantly; audio
appears a few seconds later.

### What the worker does

```
1. Acquire a per-message advisory lock so two near-simultaneous Stop
   events don't both rewrite the same source.
2. Compute source hash (source ‖ \0 ‖ voice:speed). Same key shape
   /speak uses → SHARED CACHE with the slash command and the web app
   (rewrite-on mode).
3. Cache lookup. Hit → speak.py --source-hash with empty stdin (the
   pipeline short-circuits to mpv on the cached WAV). No claude -p,
   no TTS, just playback.
4. Cache miss → claude -p rewrite → speak.py --source-hash with
   rewrite on stdin. Pipeline generates the audio, promotes to cache,
   mpv starts.
```

### Edge cases handled
- **Trivial messages.** Source under 20 chars → exit 0 (skip
  autoplay). Avoids speaking one-line "ok" / "done" replies.
- **No qualifying message.** Should never happen on Stop, but
  `extract_message.py` exits 2 if so → hook exits 0 silently.
- **Disable marker.** Hook exits 0 before doing any work.
- **Hook already running for prior message.** The new mpv session
  takes over (ADR-009's singleton invariant). The prior worker, if
  still rewriting, completes its rewrite, then promotes the cache
  entry, then tries to start mpv on top of the new session. Result:
  newer message wins playback, older message gets cached for free.
  Acceptable; minor wasted work.
- **No mpv running, second hook fires while first worker is still
  going.** Second worker sees no current session, starts mpv on its
  own audio when ready. Prior worker, when it gets to mpv-start, kills
  the second's mpv and starts on the prior message. Race condition,
  but bounded — last-writer-wins. To avoid this, we add a small
  worker-level "skip if newer Stop has fired since this worker
  started" check via mtime of a beacon file: each Stop bumps
  `/tmp/auto-speech-last-stop`; the worker compares its launch time
  against that file before final mpv-start, and bails if a newer
  Stop has occurred.

### Slash commands gain
- `/autoplay-on` — `rm -f ~/.claude/auto-speech.disabled`. One status line.
- `/autoplay-off` — `touch ~/.claude/auto-speech.disabled`. One status line.

These are intentionally trivial. Power users can also `touch`/`rm`
the file from any shell.

## Rationale

1. **Maximum code reuse.** The hook is a shell script that calls
   existing Python entry points. No new audio code.
2. **Same cache as the web app and the slash command.** Rewrite-mode
   keys collapse three surfaces' caches into one — most user-facing
   surface (web app) gets free hits from autoplay, and vice versa.
3. **No surprise on first install.** Opt-in install means a fresh
   clone never starts speaking your responses out loud.
4. **Pause without ceremony.** The disable marker means "I want
   silence right now" is one slash command, no settings.json edits,
   no service restarts.
5. **Detached worker is the only honest answer.** Synchronous hooks
   would block the user; sync rewrite + TTS is too slow to inflict on
   a writer's flow.

## Alternatives considered

- **Always-on, no toggle.** Rejected: users will sometimes want to
  read silently.
- **Per-project install.** Rejected: friction. Every new clone needs
  a setup step the user will forget. The disable marker covers
  per-context off-switching.
- **Hook does its own claude API call (skipping the CLI).** Reasonable
  but requires API key management. Phase 16 territory if the CLI
  startup tax becomes intolerable.
- **Synchronous hook execution.** Forces the user to wait. Not viable.
- **Anthropic API direct call instead of `claude -p`.** Same as
  ADR-011's runner-up. Same tradeoff stays — opt for the CLI to
  avoid key management; revisit if startup is too slow in practice.

## Consequences

- New scripts under `plugin/scripts/shell/`:
  - `autoplay_hook.sh` (Stop entrypoint; fast checks; spawn worker).
  - `autoplay_worker.sh` (the slow path; runs detached).
- New install/uninstall scripts under `setup/`:
  - `install-hook.sh` — uses `jq` to add a `Stop` hook entry into
    `~/.claude/settings.json` idempotently.
  - `uninstall-hook.sh` — removes it idempotently.
- New slash commands `/autoplay-on` and `/autoplay-off`.
- The disable marker convention is documented in
  `docs/operating-guide.md` (a small new file added in this phase).
- `setup/install.sh` checks for `jq` and installs via `brew` if absent.
- The worker checks the "last Stop beacon" file
  `/tmp/auto-speech-last-stop` to bail out of stale work.

## Invariants

- **I-15.1 Opt-in.** Without `setup/install-hook.sh` having run, no
  hook fires. The default Claude Code experience is unchanged.
- **I-15.2 Marker is the kill switch.** When
  `~/.claude/auto-speech.disabled` exists, the hook produces zero
  side effects.
- **I-15.3 Hook returns fast.** The hook's own runtime is bounded by
  its fast checks (~50 ms). Slow work is detached.
- **I-15.4 Same cache as the web app rewrite-on path.** A given
  source text + voice + speed maps to exactly one cache entry whether
  produced by autoplay, by the web app, or by `/speak`.
- **I-15.5 Worker bails on staleness.** A worker that's been
  superseded by a newer Stop event before it could start mpv must
  not start mpv (else the user hears the *previous* message after
  Claude has already moved on).
- **I-15.6 Idempotent install.** Running `install-hook.sh` twice
  yields exactly one hook entry. Running `uninstall-hook.sh` twice
  is a no-op the second time.

## Out of scope
- Per-project enable/disable beyond the global marker. (Marker check
  in cwd could be added later.)
- A "skip this one" shortcut — could add a magic phrase Claude can
  emit (e.g., `[silent]`) that the worker detects and skips. Future.
- Voice/speed changes from the toggle commands.
