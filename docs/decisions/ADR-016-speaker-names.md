# ADR-016 — Name the desk only when the speaker changes

**Status:** Accepted
**Date:** 2026-10-18
**Depends on:** ADR-014 (fire-and-forget speak), ADR-015 (sound yield and the missed list).

## Context
Several desks speak to Joshua through one auto-speech daemon: the Master
Desk, the Aspen Desk, the Development Desk, the Clerk and any other agent.
Each one opened its lines with its own name, so the Master Desk said
"Master Desk" on every line of a conversation where it was the only one
talking. His 19:06 (2026-10-18): "Is the say tool able to identify when
you're having a contiguous conversation with me so that you don't have to
keep saying master desk if you're the only one talking to me, and can it
identify when some other desk is talking to me? In which case it would
have to qualify what desk it is. I just need to know what desk it is when
who decides to talk to me changes."

Four things had to be decided: how the daemon knows who sent a line, how
a session becomes a name, when the name is said, and what happens to a
line whose audio is already cached and cannot take words in front.

## Decision

### Every line carries its session
The daemon already took `session_id` on `speak` and `play_cache`. Now every
path fills it: the CLI `speak` and the standalone `speak.py` default
`--session-id` from the shell's `CLAUDE_CODE_SESSION_ID`; the MCP `speak`
tool takes an optional `session_id` and otherwise uses the session that
launched the server; `playback_control` replays carry it too; narration
lines (P4) carry the session of the hook events they summarize. A line
with no session at all (a raw line on the legacy text wire from a shell
that is not Claude Code) is left exactly as it is and moves nothing.

### A session becomes a name through the desk agents' notes
Each agent note in the desk's `Agents/` folder carries `session:` (the
Claude Code session id) and, for the core desks, `name:` ("Master Desk").
`speaker.DeskNames` reads the front matter of every note, maps the session
to the note's `name:` or, failing that, the note's file name ("Aspen
Desk", "Development Desk", "Clerk - Desk"), and re-reads the folder every
30 s so a new or renamed agent is picked up without a restart. When two
notes share a session (a desk and its closed predecessor) the live one
wins, then the one that names itself, then alphabetical. A **personal**
note (section `[[Personal]]`, herdr session `personal`, or a "Personal -
…" name) is never read into the map, by the desk's own rule (card 1369):
only its front matter is looked at, to skip it. The folder is
`$AUTO_SPEECH_AGENTS_DIR`, else `$DESK_DIR/Agents`, else
`~/Desk/Agents`; `agents_dir` in the config overrides
it. `[speaker.names]` in the config names a session by hand and wins over
the notes. A session no note names is announced as **"Another desk"**:
he asked to know when the speaker changes, and an unnamed change is still
a change; the daemon log carries the session id so it can be named with
`auto-speech speaker name <session> <name>`.

### When the name is said
`speaker.Announcer` keeps the last session heard and when. A line opens
with "<name> here," when its session differs from that one, or when the
last line was heard more than the **quiet gap** ago (15 minutes by
default: long enough that one desk's working conversation never repeats
itself, short enough that a desk coming back after a break says who it is).
A line that already opens with "<name> here," for a desk the daemon knows
(its own name, any other note's name, or "Another desk") is not doubled;
it still counts as that desk speaking. Only a line that was actually heard
moves the last speaker: a line held for another app's sound and set aside
on the missed list (ADR-015) does not, so the next line from the same desk
still names it. The whole behaviour has one switch, `enabled`.

### Cached audio gets the name as its own short clip
A cache hit and a `play_cache` replay play audio synthesized before the
daemon saw the line, so the words cannot be put in front. When such a line
is to be named, the daemon synthesizes "<name> here." once per name into
`<state dir>/auto-speech-speaker/<name>.wav` and plays that clip, under
the same hold rules, immediately before the cached audio. A synthesized
line carries the prefix in its own text (one synthesis, natural prosody).
The prefix never changes the cache key: the cache stays keyed by the
original words.

### One verb in three places
`auto-speech speaker status | on | off | gap <seconds> | name <session>
<name> | forget <session> | who [session]`, the MCP tool
`speaker(action, seconds, session_id, name)` and
`~/.config/auto-speech/speaker.toml` (`[speaker] enabled,
quiet_gap_seconds, agents_dir` and `[speaker.names]`) share one
implementation, `speaker.run_action`, so they say the same thing. The
daemon re-reads the file when it changes; no restart. The daemon `status`
reply carries `speaker` (switch, gap, last speaker, when).

## Consequences
- One desk talking is heard as one voice without a name on every line; a
  change of desk is always named; after a quiet quarter hour the desk
  names itself again.
- Nothing is read from his personal workspace. Only agent-note front
  matter is read, to map or to skip.
- Lines from outside Claude Code (a plain shell) are untouched.
- A cached line costs one extra short clip the first time each name is
  needed; afterwards the clip is reused.
- Invariants: I-19.1 a line names its desk if and only if the desk differs
  from the last one heard or the quiet gap has passed; I-19.2 a line that
  names itself is never doubled; I-19.3 a line with no session moves
  nothing; I-19.4 no personal note is ever read past its front matter.

## Tests
`tests/test_speaker.py` (names from notes, personal skipped, settings,
the rule, the verb); `tests/test_narrator_service.py` (prefix in the
synthesized text on a change and after the gap, the clip before cached
audio and on the worker's play_cache path, a self-announced line, a line
set aside never moves the speaker, narration carries its session, status);
`tests/test_mcp_server.py` (speak carries the session from the call or
the environment; the `speaker` tool); `tests/test_cli_dispatcher.py`
(`speaker` subcommand; `speak` uses the shell session);
`tests/test_config_validation.py` (`[speaker]`).
