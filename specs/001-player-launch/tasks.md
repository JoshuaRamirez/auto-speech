---
description: "Tasks for 001-player-launch: auto-speech drives the UI Utility's player (1339 T09)"
---

# Tasks: Player launch — auto-speech drives the UI Utility's player

**Input**: `specs/001-player-launch/` (spec.md, plan.md, research.md, data-model.md,
contracts/player-handover.md, quickstart.md); the contract itself is
`~/Developer/UI-Utility/docs/external/CONTRACT.md` v1 rev 1.1 (read only).
**Tests**: requested (spec FR-007, SC-002/003/004; the order: "tests silent, only through
tests/run_all.sh, green"). Tests are plain-Python files run by `tests/run_all.sh`; never run a test
outside it without its `AUTO_SPEECH_*` variables, `AUTO_SPEECH_MPV_AO=null`,
`AUTO_SPEECH_START_VIA_LAUNCHD=never` and (new here) `UI_UTILITY_SOCK`, `UI_UTILITY_SILENT=1`,
`AUTO_SPEECH_PLAYER_LAUNCH=never`.

**Where things are today** (main 425142d, worktree `.worktrees/player-launch`, branch
`feat/player-launch`):
- `plugin/scripts/python/speech_player.py`: `PlayerLauncher(store=None, *, holding=None,
  installed=utility_installed, log=None)`; `after_line(line, settings=None) -> str` returns
  `OFF | HELD | NOT_INSTALLED | CONTRACT_PENDING`; `note(settings=None) -> str`; `_say_once`;
  `utility_installed()` (PATH `ui-utility` or `UI Utility.app` in /Applications or
  ~/Applications); `describe(launcher, settings)`.
- `plugin/scripts/python/narrator_service.py`: `_heard()` calls `self._player.after_line(line)`
  after `history.export(...)`; `self._player = PlayerLauncher(holding=self._hold_stands, log=_log)`;
  `_stop_socket_server()` is on the shutdown path.
- `HistoryLine` (speech_history.py): `.id`, `.stem`, `.wav` (Path), `.sidecar` (dict with `text`,
  `spoken_at`, `sender.name`, `word_timings.words` = list of `{w,start,end}`).
- `cli.py` ~line 694 and `mcp_server.py` ~line 1541 pass
  `player_note=lambda settings: PlayerLauncher(store).note(settings)` to `run_action`.
- `tests/test_speech_history.py` `TestPlayerLauncher` (~line 324) has 4 tests; the last,
  `test_installed_but_not_wired_says_so`, expects `CONTRACT_PENDING` and goes away.
- `tests/run_all.sh`: exports near line 38–47; the `CHEAP=(…)` list holds test file names.
- `state_paths.state_path(name)` gives a path in the state dir (`AUTO_SPEECH_TMP_ROOT` or /tmp).

## Format: `[ID] [P?] [Story] Description`

## Phase 1: Setup

- [ ] T001 Add the suite's UI Utility isolation to `tests/run_all.sh`, after the
  `AUTO_SPEECH_START_VIA_LAUNCHD=never` export: `export UI_UTILITY_SOCK="$STATE_DIR/ui-utility.sock"`,
  `export UI_UTILITY_SILENT=1`, `export AUTO_SPEECH_PLAYER_LAUNCH=never`, each echoed as an
  `[isolation]` line like its neighbours. Comment: no test starts the real UI Utility (a silent copy
  would stay as his copy: contract "A copy already running keeps its mode").

## Phase 2: Foundational — the contract client (blocks US1–US3)

- [ ] T002 [P] Write the stand-in server `tests/ui_utility_standin.py` (not a test file; imported
  by tests): class `StandIn(path, *, script=None)` starting a thread with a Unix stream socket
  server at `path`; per connection reads JSON lines, records each request dict in `self.requests`
  (thread-safe list), and answers by `script(request) -> dict | None` (None = never answer). The default
  script: `hello` → `{"ok":true,"contract":1,"pieces":["player"]}`, `open` →
  `{"ok":true,"id":<id or "player-1">}`, else `{"ok":true}`; it echoes `req`. Methods: `start()`,
  `stop()` (closes sockets, removes the file), `send_event(obj)` to every open connection,
  `drop_connections()`, `wait_for(cmd, n=1, timeout=2.0) -> list[dict]`.
- [ ] T003 Write `plugin/scripts/python/ui_utility_client.py`:
  - `CONTRACT = 1`, `PANEL = "player"`, `DEFAULT_SOCK = Path.home()/".local/run/ui-utility.sock"`,
    `def socket_path() -> Path` (`UI_UTILITY_SOCK` if set, else DEFAULT_SOCK).
  - `class ContractError(Exception)` with `.kind` in `{"no socket","refused","timeout","wrong contract","closed"}` and `.detail`.
  - `class UiUtilityClient(path=None, *, reply_timeout=5.0, on_event=None)`: `connect()` (AF_UNIX
    stream; raises `ContractError("no socket")` on FileNotFoundError/ConnectionRefusedError/OSError);
    starts a daemon reader thread that reads lines, `json.loads`, and routes: a dict with `"req"`
    matching a pending id → that request's slot; a dict with `"event"` → `on_event(evt)` if given
    (exceptions swallowed); anything else ignored; EOF/error → marks closed and wakes every
    pending request with `ContractError("closed")`.
  - `request(obj) -> dict`: adds `"v":1` and `"req":"as-<n>"`, writes one line (`\n`), waits up to
    `reply_timeout` for its reply; timeout → `ContractError("timeout")`; reply `ok` false →
    `ContractError("refused", f"{error}: {detail}")`; returns the reply.
  - `hello()`: request `{"cmd":"hello"}`; unless `contract == 1` and `"player" in pieces` raise
    `ContractError("wrong contract", …)`.
  - `open_player(*, panel_id, clip, words, title, play=False)`: request
    `{"cmd":"open","panel":"player","id":panel_id,"clip":{"path":str(clip)},"text":{"words":words},"title":title,"play":play}`.
  - `close()` (idempotent; shuts the socket; the reader ends), `connected` property.
  - `def launch_command(*, silent: bool, sock: str|None, which=shutil.which) -> list[str]`:
    `["ui-utility","launch"]` (+ `"--silent"`) when `which("ui-utility")`; else
    `["open","-g","-a","UI Utility"]` + `["--env","UI_UTILITY_SILENT=1"]` when silent +
    `["--env",f"UI_UTILITY_SOCK={sock}"]` when sock.
  - `def launch(*, run=subprocess.run, env=os.environ) -> list[str] | None`: returns None and runs
    nothing when `env.get("AUTO_SPEECH_PLAYER_LAUNCH") == "never"`; else builds the command with
    `silent = env.get("UI_UTILITY_SILENT") == "1"`, `sock = env.get("UI_UTILITY_SOCK")`, runs it with
    `stdin=DEVNULL, capture_output=True, timeout=10, check=False`, returns the argv.
- [ ] T004 [P] Write `tests/test_ui_utility_client.py` (plain-Python file with a `TESTS` list and a
  `__main__` runner like `tests/test_narrator_start_label.py`) against the stand-in, socket in
  `tempfile.mkdtemp()`: hello ok; hello with contract 2 → `wrong contract`; open sends exactly the
  contract-use fields (`v`, `cmd`, `req`, `panel`, `id`, `clip.path`, `text.words`, `title`,
  `play` false); `ok:false` → `refused` with `"no-panel: …"` detail; never-answering script →
  `timeout` within `reply_timeout` (set 0.3) + 0.5 s; an event between request and reply reaches
  `on_event` and the reply still matches; server drops the connection → a pending request raises
  `closed` and `connected` is False; no socket → `no socket`; `launch_command` for PATH/no-PATH ×
  silent/not × sock/not; `launch()` with `AUTO_SPEECH_PLAYER_LAUNCH=never` runs nothing (a `run`
  stub that fails the test if called).
- [ ] T005 Add `test_ui_utility_client.py` to `CHEAP=(…)` in `tests/run_all.sh`, after
  `test_speech_history.py`.

## Phase 3: User Story 1 — the line just spoken appears in the player (P1) 🎯 MVP

**Goal**: with the setting on, no hold, installed: each heard line is opened in the one player
panel, ready to play, without focus. **Independent test**: stand-in on a private socket, launcher
with `installed=lambda: True`, export a line, `after_line` → `"queued"`, the stand-in sees `hello`
then `open` with that WAV and words.

- [ ] T006 [US1] In `plugin/scripts/python/speech_player.py` add outcomes `QUEUED = "queued"`,
  `OPENED = "opened"`, `SKIPPED = "skipped"`, `NO_SOCKET = "no socket"`, `REFUSED = "refused"`,
  `TIMEOUT = "timeout"`, `WRONG_CONTRACT = "wrong contract"` (data-model.md); keep `OFF`, `HELD`,
  `NOT_INSTALLED`; remove `CONTRACT_PENDING` and its text. `PANEL_ID = "auto-speech"`,
  `LAUNCH_WAIT_SECONDS = 10.0`, `OUTCOME_FILE = state_path("auto-speech-player.json")`.
- [ ] T007 [US1] Extend `PlayerLauncher.__init__` with keyword args `client_factory=UiUtilityClient`,
  `launch=ui_utility_client.launch`, `outcome_file=None` (default `OUTCOME_FILE`),
  `sleep=time.sleep`, `clock=time.monotonic`, `start_worker=True`. State: a `threading.Condition`,
  `self._pending: Optional[HistoryLine]` (one slot, newest wins), `self._client=None`,
  `self._greeted=False`, `self._closed=False`; the worker thread (daemon, name
  `player-launcher`) starts lazily on the first queued line.
- [ ] T008 [US1] `after_line(line, settings=None)`: keep the existing gates in order (off/None
  line → OFF; holding() true or raising → HELD; not installed → NOT_INSTALLED, said once); then put
  `line` in the slot (replacing any older), notify, return `QUEUED`. No I/O on this path.
- [ ] T009 [US1] The worker loop `_run()`: wait for a line; take it; `_hand_over(line)`; record the
  outcome; repeat until `close()`. `_hand_over(line)`:
  1. WAV missing → `SKIPPED`.
  2. holding() true/raising → `HELD` (the hold, again, just before showing anything; research R6).
  3. ensure a greeted client: if none or not connected → `client_factory(on_event=self._on_event)`;
     `connect()`; on `ContractError("no socket")` → `launch()`; if it returned None →
     `NO_SOCKET`; else retry `connect()` every 0.25 s until `LAUNCH_WAIT_SECONDS` → `NO_SOCKET`;
     then `hello()` (→ `WRONG_CONTRACT` on that error; drop the client).
  4. `open_player(panel_id=PANEL_ID, clip=line.wav, words=sidecar["word_timings"]["words"] or [],
     title=f"{sender name or 'auto-speech'} · {HH:MM:SS of spoken_at}", play=False)` → `OPENED`.
  5. `ContractError` kinds map to outcomes (`refused` keeps the client; `timeout`/`closed` drop it).
  Any other exception → logged, outcome `REFUSED` with the repr, client dropped. Never raises.
- [ ] T010 [US1] Outcome recording `_record(outcome, detail, line)`: write
  `{"outcome","detail","at" (local ISO, seconds),"line_id"}` to the outcome file by tmp + rename;
  log via `_say_once(outcome, msg)` for failure kinds (once per kind), and log `opened` lines
  at most once (first success). Errors writing the file are logged, never raised.
- [ ] T011 [US1] `close()`: set closed, notify, close the client; idempotent. In
  `plugin/scripts/python/narrator_service.py`, call `self._player.close()` (guarded with
  `getattr`, exceptions logged) next to where `_stop_socket_server()` runs on shutdown.
- [ ] T012 [US1] Tests in `tests/test_speech_history.py` `TestPlayerLauncher` (replace
  `test_installed_but_not_wired_says_so`): with a `StandIn` on a temp socket and
  `client_factory=lambda on_event: UiUtilityClient(sock, reply_timeout=1.0, on_event=on_event)`,
  `launch=lambda: None`, `installed=lambda: True`, `outcome_file` in the temp dir: one line →
  `QUEUED`, stand-in gets `hello` then `open` with `clip.path == str(line.wav)`, the sidecar's words,
  `play` False, `id` "auto-speech"; outcome file says `opened`. Two lines → two `open`s, one
  `hello`, same `id`.
- [ ] T013 [US1] Test: no socket at first, `launch` stub starts the stand-in and returns an argv →
  `opened`; `launch` returns None and no socket → `no socket` in the outcome file.

## Phase 4: User Story 2 — off by default, silent during a hold, quiet when absent (P1)

**Goal**: no launch and no traffic when off, held or not installed. **Independent test**: counting
`launch` and a stand-in: zero requests in each gated case.

- [ ] T014 [US2] Keep/adjust the existing tests `test_off_by_default`, `test_never_during_a_hold`,
  `test_nothing_while_the_utility_is_not_installed` (now with a stand-in and counting `launch`,
  asserting zero requests and zero launches).
- [ ] T015 [US2] Test: a hold that starts after `after_line` queued the line but before the worker
  runs → outcome `held`, no `open` sent (use `start_worker=False`, call `_hand_over` directly with a
  `holding` that turns true).

## Phase 5: User Story 3 — the player never slows or stops speech (P2)

**Goal**: `after_line` returns at once; failures logged once; newest line wins. **Independent
test**: a never-answering stand-in; time `after_line`.

- [ ] T016 [US3] Test: stand-in script that never answers; `after_line` returns `QUEUED` in
  < 50 ms each for 5 lines; the outcome becomes `timeout`; logs hold one `timeout` line.
- [ ] T017 [US3] Test: refusing stand-in (`open` → `ok:false, error:"bad-value"`) → outcome
  `refused`, detail starts `bad-value:`; logged once over 3 lines.
- [ ] T018 [US3] Test: with `start_worker=False`, queue 3 lines, then run one `_hand_over` cycle via
  the worker's take → only the newest line's WAV is opened.
- [ ] T019 [US3] Test: stand-in drops the connection after the first `open`; the next line
  reconnects, says `hello` again, opens → `opened`.
- [ ] T020 [P] [US3] Test in `tests/test_narrator_service.py`: the daemon's `_heard` with a player
  whose `after_line` sleeps 2 s is not what the daemon calls (it calls the real launcher, which
  queues): use `PlayerLauncher(installed=lambda: True, client_factory=<never-answering>, …)` with
  the history player setting on, and assert `_heard` returns in < 50 ms.

## Phase 6: User Story 4 — status on every surface (P3)

- [ ] T021 [US4] `PlayerLauncher.note(settings)`: off → `""`; not installed → "the UI Utility is
  not installed; nothing opens"; installed → "opens after each line" + ("; last: <outcome>
  <HH:MM:SS>" + (": <detail>" if detail) when the outcome file exists). `cli.py` and
  `mcp_server.py` keep calling `PlayerLauncher(store).note(settings)` — construct it with
  `start_worker=False` there so a status call starts no thread.
- [ ] T022 [US4] Test: `run_action("status", …)` with `player_note` from a launcher whose outcome
  file says `refused` shows "player after each line: on (opens after each line; last: refused …)".

## Phase 7: Polish & cross-cutting

- [ ] T023 [P] `docs/decisions/ADR-021-player-launch.md`: context (his 13:38 words, ADR-020's
  pending step, the contract v1 rev 1.1 hand-over #m1021-0025-f8be), decision (research R1–R8),
  clarify answers (play false; one panel; held connection; stand-in tests), consequences,
  invariants I-21.1 nothing opens while off/held/not installed; I-21.2 `after_line` does no I/O;
  I-21.3 no test launches the real app; I-21.4 `open` always has `play:false`.
- [ ] T024 [P] Update ADR-020's "player launcher" paragraph to point to ADR-021 (the pending step is
  done); `config/history.toml.example` `player` comment (ready to play, never plays on its own, one
  window); `CHANGELOG.md` [Unreleased] Added entry; `FEATURES.md` speech history section;
  `docs/OPERATIONS.md` history paragraph (`history player-on`, the outcome file, `UI_UTILITY_SOCK`).
- [ ] T025 Run `nice -n 10 bash tests/run_all.sh` (through `desk-heavy`) until green; fix what
  fails by making the work succeed.
- [ ] T026 Self-review the branch diff against main (correctness, threads, the contract subset,
  constitution I–V); fix; re-run the suite if code changed.
- [ ] T027 Push `feat/player-launch` to `origin` and open the PR on JoshuaRamirez/auto-speech
  (`gh pr create`), review it on GitHub's diff, fix, merge `--no-ff` on green (locally `git merge
  --no-ff` on main then push main, or `gh pr merge --merge`), and record the PR, merge commit and
  run count in TRIAL-LOG.md.
  **Before pushing, check `git rev-list --count origin/main..main`**: on 2026-10-21 01:37 local main
  was 76 commits ahead of `origin/main` (68f3635), so a PR on this branch carries all 76 (speech
  under load, the history, the narrator label fix…). That goes to the Desk as a question with the
  status block before anything is pushed (default: push only once the Desk says the 76 may go out
  with it).

## Dependencies & order

- T001 → all tests. T002, T003 → T004 → T005. Phase 2 → Phases 3–6.
- US1 (T006–T013) before US2/US3 tests (they reuse its seams). US4 (T021–T022) after T010.
- Polish after all stories.

## Parallel opportunities

- T002 and T003 (different files); T004 once both exist; T020 alongside T016–T019; T023 and T024.

## Implementation strategy

MVP = Phases 1–3 (the line appears in the player). Then US2 (gates, mostly existing), US3
(robustness), US4 (status), polish. Commit after each phase by named files.
