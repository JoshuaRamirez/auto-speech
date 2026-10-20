# ADR-019: Speech holds up under load

**Date:** 2026-10-20 · **Status:** accepted · **Orders:** the Master Desk #m1020-1403-894a and #m1020-1404-652f (his 14:02)

## Context

From 13:45 to 14:00 on 2026-10-20 the Mac's one-minute load went 75 → 116 → 272 on 16 cores (swap 5.9 of 7.2 GB).
Auto-speech dropped five lines: one playback timed out at 20:50:12Z, then four syntheses timed out at 20:52, 20:54,
20:56 and 21:00Z (82 s, 48 s, 30 s, 66 s) and were set aside. At 21:03:34Z a play "timed out after 23.09s" and cut his
line off mid-sentence: the budget was the clip's length plus 15 s, and under that load mpv took longer than that to
start and play an eight-second clip. His words: "figure out what went wrong and prevent it from happening" and "autoheal
in problems of the above … Playback must not time out on a clip that's still playing (the timeout measures a stall, not
the clip's length), and a cut-off line replays whole."

Two things went wrong. The budgets of ADR-015's era (`synthesis_timeout_factor`, `play_timeout_margin_seconds`) were
sized for a quiet machine and read a busy one as a hung one. And nothing gave the narrator's own work any priority over
the heavy work that was loading the machine (the Desk Keeper is putting that work at background QoS in `desk-heavy`).

## Decision

1. **Priority.** The daemon, at start, clears any Darwin background clamp it inherited
   (`setpriority(PRIO_DARWIN_PROCESS, 0, 0)`), restores nice 0 when the kernel allows and reports its nice when it
   cannot, and puts its main and speech-worker threads at `QOS_CLASS_USER_INTERACTIVE`. The processes it spawns, `say`
   and mpv, run under `/usr/sbin/taskpolicy -t 0 -l 0` (throughput and latency tier 0; `AUTO_SPEECH_QOS_BOOST=0` turns
   the wrap off). taskpolicy execs the command, so pids and the sink's interrupt are unchanged. A niced spawner is the
   one thing a process cannot undo for itself: `narrator_service_start.sh`, when it runs at nice above 0, starts the
   daemon through `launchctl submit` (label `dev.auto-speech.narrator`), which runs it at nice 0; a direct start is
   unchanged otherwise (`AUTO_SPEECH_START_VIA_LAUNCHD=never` keeps the direct start).
2. **A play ends on a stall, never on the wall clock while the clip advances.** mpv runs with an IPC socket per play;
   the sink polls `time-pos` and `pause` once a second and ends the play only after `play_stall_seconds` (default 15,
   `AUTO_SPEECH_PLAY_STALL_SECONDS`; 0 turns the watch off) with no progress while not paused, raised as
   `PlaybackError("Playback stalled …")`. The wall-clock budget counts only while no position has ever been read (mpv
   not yet started, or a build without IPC); once a position was read it never applies, including when mpv closes its
   socket as it finishes. The narrator's wall-clock budget is now twice the clip's length plus the margin.
3. **Budgets stretch with the load.** Synthesis and wall-clock budgets are multiplied by the one-minute load per core,
   never under 1, capped by `load_timeout_scale_cap` (default 4; 0 turns the stretch off).
4. **A timed-out line is retried whole, not dropped.** A synthesis timeout queues the words again; a stalled or
   timed-out play queues the finished WAV again (copied under `auto-speech-retry/`, so the cut-off line replays from
   the start without a second synthesis), at the line's own priority, with `retries` counted on the item. Before a
   retry plays, the worker waits for the load per core to fall under `retry_load_per_core` (default 2), up to
   `retry_wait_seconds` (default 180), then goes ahead; an interrupt or a preemption during the wait puts the line back
   unchanged. Only past `timeout_retries` (default 3) is the line set aside on the missed list as "timed out".

## Consequences

- Under load his lines arrive late rather than not at all, and a cut-off line is heard whole; the missed list keeps
  only what three retries could not play.
- The sink depends on mpv's IPC (`--input-ipc-server`); without it the wall clock still ends a hung play.
- The retry's wait holds the worker while the load is high, so lines behind it wait too; a P1 interrupt or P2 line
  still comes first.
- Nothing here is a gate: a priority call that fails is logged and skipped; tests stay silent (`--ao=null`) and the
  load test runs its burners at nice 19 for about fifteen seconds (`tests/test_speech_under_load.py`).
