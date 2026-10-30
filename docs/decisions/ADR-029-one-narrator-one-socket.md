# ADR-029: one narrator, one socket

Status: accepted, 2026-10-30 (#m1030-1044-b20b).

## Context
On 2026-10-30 at 10:53 the narrator was restarted (`auto-speech narrate stop`,
then `narrator_service_start.sh`). Two narrators started in the same second,
pids 11644 and 11649. The log at 17:53:26Z: both "started"; one "socket
server listening"; the other "failed to start socket server … [Errno 48]
Address already in use", then "shutdown". On its way out the loser ran
`_cleanup_socket_file`, which removed `/tmp/auto-speech-daemon.sock`: the
live narrator's socket. The survivor ran on with no way in. The pid file
named it, `narrate status` read "daemon alive: False", `auto-speech queue`
read "not running", and no desk line could arrive until a restart by hand.

Three faults lined up:

1. **The start check was not atomic.** The start script and the daemon's
   `main()` both check the pid file, but the daemon writes it only in
   `run()`, after building the service. Two starts in that window both pass.
   A replay of two concurrent runs of the start script (the new test) spawns
   two narrators on the old script. The start script is reached from the
   restart itself, from every `DaemonClient` call that finds no socket file
   (`ensure_daemon_running`, used by the CLI, the MCP server and `speak.py`),
   and from the narrator and autoplay hooks. Which of them made the second
   start at 10:53 is not on record (sysobs' process log begins at 17:56Z).
2. **A narrator removed a socket it had not bound.** Clean-up unlinked the
   path whatever file was there.
3. **A start removed a live socket as "stale".** `_start_socket_server`, and
   `_DaemonSocketServer.server_bind` again, unlinked any file at the path
   before binding. A second narrator reaching bind after the first would take
   the path, leaving the first running and unreachable (what the old code did
   in the new test: both stayed alive).

## Decision
1. **A lifetime start lock.** `main()` takes an exclusive, non-blocking
   `flock` on `auto-speech-narrator-daemon.lock` in the state folder before
   it touches anything, and holds it for the narrator's life (released once
   `run()` returns, before the wait for stuck threads). A start that cannot
   take it prints "narrator daemon already running", logs "not started:
   another narrator holds the start lock", and exits 1 with the pid file and
   the socket as the winner has them. The kernel drops the lock when its
   holder dies, so a stale pid file is still reclaimed on the next start
   (OPERATIONS.md, "Stale 'already running'"). The pid-identity check stays
   behind the lock, for a narrator from before it.
2. **A narrator removes only the socket file it bound.** `server_bind`
   records the bound file's `(st_dev, st_ino)`; clean-up (stop and atexit)
   unlinks the path only when it is still that file (`unlink_if_bound`). A
   narrator that never bound removes nothing.
3. **A live socket is never stale.** Before unlinking an existing file at
   the path, a start connects to it. If something answers, the start fails
   with EADDRINUSE and leaves the file. A file nobody answers on is
   reclaimed as before.
4. **The start script serializes its check-and-spawn** under a lock
   directory (`auto-speech-narrator-start.lock.d`; macOS has no `flock(1)`),
   held until the new pid file appears. A second start waits, then finds
   "already running". It also keeps a second niced start from
   `launchctl remove`-ing the job the first had just submitted. A lock left
   by a dead start is reclaimed by its owner's pid; after 10 s a start goes
   ahead, and the narrator's own lock still holds.

Why both locks: the narrator's lock is the guarantee, since every start path
(the script, launchd, a hand-run `narrator_service.py`) ends in `main()`. The
script lock gives the losing caller the right answer ("already running", the
winner's pid) and keeps the launchd path from restarting a narrator that just
started.

## Tests
`tests/test_one_narrator_one_socket.py`: two narrators reach `main()`
together on a private state folder and socket: one serves, the other exits
1, the socket answers `status`, and the pid file names the one that serves.
Also: a live server's socket survives a narrator that fails to bind, a
socket that replaced a narrator's own survives its stop, a stale socket is
reclaimed, the lock refuses a second holder, and two concurrent runs of the
start script spawn one narrator.
