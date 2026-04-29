# Phase 15 Micro-Design — Autoplay-on-Stop hook

Implements [ADR-012](../decisions/ADR-012-stop-hook-autoplay.md).

## Scope
A Claude Code Stop hook that — when installed — auto-runs the same
backend the web app uses, on every assistant turn:
extract → `claude -p` rewrite → `speak.py --source-hash`. Hook
returns instantly; the slow work runs detached.

Opt-in install via `setup/install-hook.sh`; reversible.
Pauseable via `~/.claude/auto-speech.disabled` marker file, with
two slash commands `/autoplay-on` and `/autoplay-off` as ergonomic
toggles.

## M1 — Classes and scripts at this level

This phase is shell + small slash-command files. No new Python
classes. The Python audio stack (Phase 14) is reused unchanged.

| Artifact | Role |
|---|---|
| `plugin/scripts/shell/autoplay_hook.sh` | Stop hook entrypoint; fast checks; spawns worker; exits ~50 ms. |
| `plugin/scripts/shell/autoplay_worker.sh` | Detached background worker: rewrite + speak. |
| `setup/install-hook.sh` | Idempotent jq-based add of the Stop hook to `~/.claude/settings.json`. |
| `setup/uninstall-hook.sh` | Idempotent jq-based remove. |
| `plugin/commands/autoplay-on.md` | Slash command: `rm -f` the marker. |
| `plugin/commands/autoplay-off.md` | Slash command: `touch` the marker. |

## M2 — Semantics

### `autoplay_hook.sh`
```
1. consume Stop hook stdin (the JSON payload, ignored)
2. if marker file exists → exit 0
3. update beacon: touch /tmp/auto-speech-last-stop
4. spawn worker fully detached (setsid + nohup + & + disown):
     autoplay_worker.sh "$BEACON_MTIME"
5. exit 0
```

The beacon mtime captured at step 3 is passed to the worker so the
worker can detect "was I superseded by a newer Stop?" before
committing the mpv start.

### `autoplay_worker.sh`
```
1. record START_BEACON_MTIME (passed from the hook)
2. extract source via run_extract.sh --ordinal 1
   - non-zero exit → exit 0 silently
3. measure source length
   - < MIN_LEN (20 chars) → exit 0
4. compute source hash via compute_hash.sh
5. if cache HIT for hash:
     check_staleness; if stale → exit 0
     pipe "" into run_speak.sh --source-hash HASH (server-side cache hit
       short-circuits; mpv starts on the cached WAV)
     exit 0
6. cache MISS → invoke claude -p with the 12-rule prompt
   - non-zero exit, timeout, or empty output → exit 0 silently
7. check_staleness; if stale → exit 0
8. pipe rewrite into run_speak.sh --source-hash HASH
9. exit 0
```

`check_staleness`: compare `START_BEACON_MTIME` against current
`stat -f %m /tmp/auto-speech-last-stop`. If current is newer,
the worker is for an older message and bails.

### `setup/install-hook.sh`

Idempotent. Uses `jq` to safely edit `~/.claude/settings.json`:

```
HOOK_CMD="<absolute path to autoplay_hook.sh>"
existing_count = jq '.hooks.Stop // [] |
                     [.[] | .hooks[]? | select(.command == $cmd)] | length'
if existing_count == 0:
    jq '.hooks.Stop = ((.hooks.Stop // []) +
        [{ "hooks": [ {"type": "command", "command": $cmd} ] }])'
write back atomically (tmp + replace)
```

If `~/.claude/settings.json` doesn't exist, create with just the
hooks key. If `jq` isn't installed, fail with an actionable error
("brew install jq").

### `setup/uninstall-hook.sh`

Idempotent. jq filter removes any Stop hook block whose only
`command` is our hook path. If no matching block exists, exits 0
silently.

### Slash commands

`autoplay-on.md`:
```
---
description: Resume autoplay (remove disable marker).
---
Run: rm -f ~/.claude/auto-speech.disabled
Respond with one line: "autoplay enabled" (or "already enabled" if marker was absent).
```

`autoplay-off.md`:
```
---
description: Pause autoplay (create disable marker).
---
Run: touch ~/.claude/auto-speech.disabled
Respond with one line: "autoplay paused".
```

## M3 — Relationships

```
Claude Code finishes turn
    │
    ▼
Stop hook runs autoplay_hook.sh (fast)
    │
    ├── marker present? → exit 0
    │
    ├── update beacon file
    │
    └── spawn detached → autoplay_worker.sh
                            │
                            ├── extract message via run_extract.sh
                            ├── compute hash via compute_hash.sh
                            ├── cache hit? → run_speak.sh (mpv plays cached)
                            └── cache miss
                                  ├── claude -p rewrite
                                  ├── stale? bail
                                  └── pipe rewrite to run_speak.sh
                                        (PipelineOrchestrator: chunk →
                                         producer → concat → cache promote
                                         → mpv start)
```

`run_speak.sh` is the existing Phase 11 wrapper. No changes needed.

## M4 — Implementation details

### Beacon staleness check
```
START_MTIME=$1                       # passed from hook
CUR_MTIME=$(stat -f %m /tmp/auto-speech-last-stop 2>/dev/null || echo 0)
if [[ "$CUR_MTIME" -gt "$START_MTIME" ]]; then
    exit 0   # superseded
fi
```

`stat -f %m` is BSD/macOS syntax. Linux would use `stat -c %Y`. We're
macOS-only per the project scope.

### Detaching the worker
```
nohup setsid bash "$WORKER" "$BEACON_MTIME" >/dev/null 2>&1 < /dev/null &
disown
```

`nohup` ignores SIGHUP, `setsid` makes a new session (orphan from the
parent's process group), `&` backgrounds, `disown` removes from the
shell's job table. Belt-and-suspenders against any mechanism Claude
Code might use to clean up its hook subtree.

### Logging
The hook logs to stderr (Claude Code captures briefly).
The worker logs to a fixed file `/tmp/auto-speech-autoplay.log`
(append, rotating manually if it grows — out of scope for v0.1).
This makes post-hoc debugging trivial: "audio came on but I don't
know why" → tail the log.

### `MIN_LEN` threshold
Set to 20 chars. Rationale: skips trivial responses ("ok", "done",
"got it", "yes") that no one needs to hear. Crossing the threshold
once gets you the rest. Tuneable via env var
`AUTO_SPEECH_AUTOPLAY_MIN_LEN`.

### Skip-magic-phrase (deferred)
A future small upgrade: if the source contains a leading `[silent]`
marker, the worker exits 0. Not in v0.1.

## Failure modes

| Mode | Handling |
|---|---|
| `claude` not on PATH | Worker logs and exits 0. No audio. User can `/speak` manually. |
| `claude -p` times out | Worker logs and exits 0. |
| `extract_message.py` fails | Worker exits 0 silently. |
| `~/.claude/settings.json` malformed before install | install-hook.sh refuses to edit; reports error. |
| Multiple installs of same hook | jq idempotency: count check first. |

## Invariants (beyond ADR-012's)

- **I-15.7 Single hook entry per install.** After install, settings.json
  has exactly one Stop block whose only command is our path. After
  uninstall, none.
- **I-15.8 No blocking.** The hook runs and exits in well under
  100 ms; observable mpv start latency is fully attributable to
  rewrite + TTS.

## Check gate

The natural check gate (a real Claude Code Stop event) requires a
fresh session. We approximate locally:

1. **Install idempotency.** Run `install-hook.sh` twice; inspect
   `~/.claude/settings.json`; exactly one matching entry.
2. **Uninstall idempotency.** Run `uninstall-hook.sh` twice;
   no matching entry, no error.
3. **Marker fast-path.** `touch ~/.claude/auto-speech.disabled`;
   simulate Stop by running `autoplay_hook.sh` with stdin = a fake
   JSON; observe immediate exit, no worker spawned, log empty.
4. **Worker on cache hit.** Place a known cache entry; pipe its
   source through `autoplay_worker.sh`; mpv starts within ~1 s
   on the cached WAV.
5. **Worker on cache miss + rewrite.** Pipe a fresh source through
   `autoplay_worker.sh`; observe `claude -p` invocation in the log;
   mpv starts on a new audio after ~6–14 s.
6. **Stale-skip.** Update the beacon mtime mid-worker; observe the
   worker bailing before mpv start.

## Out of scope
- Real end-to-end "fire on Claude turn completion" — requires
  starting a fresh Claude Code session. Documented procedure at the
  end of this file; user-validated.
- API-based rewriter (Phase 16 candidate).
- Per-project marker.

## Real-session validation procedure

After install:
1. `/autoplay-on` (no-op if marker absent).
2. Ask Claude any question with a substantive answer.
3. Audio should begin within ~6–14 s of Claude finishing.
4. Subsequent identical sources hit the cache and play in ~200 ms.
5. `/autoplay-off` to silence; `/autoplay-on` to resume.
6. Run `setup/uninstall-hook.sh` to fully remove.
