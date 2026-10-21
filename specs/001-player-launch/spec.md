# Feature Specification: Player launch — auto-speech drives the UI Utility's player

**Feature Branch**: `feat/player-launch`

**Created**: 2026-10-21

**Status**: Draft

**Input**: User description: "Auto-speech launches and drives the UI Utility's player by its published contract (1339 T09): launch and drive the UI Utility by its contract, behind a setting that is off by default, keeping the meeting hold, using silent mode in tests, and never touching the live narrator's launchd label. The live narrator is not restarted." His words (2026-10-20 13:38): "Each time the Desk speaks, a small player pops up and plays the line's WAV. Controls: play, pause, fast-forward, back to the beginning, rewind, with a scrubber. Below the controls, the transcript, highlighting the current position … A history button shows everything said before."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - The line just spoken appears in the player (Priority: P1)

He has turned the player on. A desk speaks a line; once he has heard it, the UI Utility's player
shows that line: its audio, ready to play again, and its words, with the current word highlighted
as it plays. It never steals focus from what he is doing.

**Why this priority**: It is the feature: the player pops up with the line and its transcript.

**Independent Test**: With the player setting on and a stand-in for the UI Utility listening on a
private socket, speak one line through the daemon's path; the stand-in receives a `hello` and an
`open` of the player with that line's WAV and its words.

**Acceptance Scenarios**:

1. **Given** the player setting is on, no hold stands and the UI Utility is installed, **When** a
   line has been played and exported to the history, **Then** the player is opened on that line's
   WAV with its words, title naming the sender, without taking focus.
2. **Given** the player is already showing an earlier line, **When** the next line is heard,
   **Then** the same player shows the new line (one player, not one window per line).
3. **Given** the UI Utility is not running, **When** a line is heard, **Then** it is launched in the
   background and then handed the line.
4. **Given** the player is showing a line, **When** the line is handed over, **Then** it
   [NEEDS CLARIFICATION: does the player play the line again on open (his "plays the line's WAV"), when the narrator has already played it aloud through mpv, or does it open ready to play?]

---

### User Story 2 - Off by default, silent during a hold, quiet when absent (Priority: P1)

Nothing pops up unless he turned it on, nothing pops up in a meeting or while another app's sound
holds speech, and nothing at all happens when the UI Utility is not installed.

**Why this priority**: His standing rules: nothing opens on its own; holds are honoured.

**Independent Test**: Exercise each gate with the stand-in and a counting launcher; no launch and
no socket traffic happen in any gated case.

**Acceptance Scenarios**:

1. **Given** the setting is off (the default), **When** a line is heard, **Then** nothing is
   launched and nothing is sent.
2. **Given** the setting is on and the meeting hold or the sound yield holds, **When** a line is
   heard after the hold, **Then** the player is handed that line only once it has played, and never
   while the hold stands; a hold that cannot be assessed counts as holding.
3. **Given** the setting is on and the UI Utility is not installed, **When** a line is heard,
   **Then** nothing is launched, the log says so once, and `history status` says so.

---

### User Story 3 - The player never slows or stops speech (Priority: P2)

Whatever the UI Utility does (slow to start, refusing a request, crashing, its socket gone), the
narrator speaks the next line on time.

**Why this priority**: Speech is the product; the player is an extra view of it.

**Independent Test**: A stand-in that never answers, one that answers errors, and no socket at all;
the daemon's line handling returns within its normal time in each case.

**Acceptance Scenarios**:

1. **Given** the UI Utility hangs, **When** lines are heard, **Then** each line's handling returns at
   once; the handing-over happens off the speech path and gives up after a short bound.
2. **Given** the UI Utility answers `ok:false`, **When** a line is handed over, **Then** the error
   code and detail are logged once per code and speech continues.
3. **Given** several lines are heard while the UI Utility is slow, **When** it becomes ready,
   **Then** it is handed the newest line, not a backlog.

---

### User Story 4 - The same switch on every surface (Priority: P3)

He or a desk turns the player on or off and asks what it is doing from the config file, the CLI or
the MCP tool, with one name.

**Why this priority**: Already built in ADR-020 (`[history] player`, `player-on`, `player-off`);
this feature only makes `history status` report the real state.

**Independent Test**: `auto-speech history status` and MCP `history(action="status")` report
off / on and installed / on and not installed / on and last hand-over failed.

**Acceptance Scenarios**:

1. **Given** the player is on and the UI Utility installed, **When** status is asked, **Then** it
   says the player opens after each line, and the last hand-over's outcome.

---

### Edge Cases

- The WAV was pruned between export and hand-over: the line is skipped, logged.
- Words are empty (a line with no text, e.g. a replay of a cached clip): the player still opens on
  the clip with an empty word list.
- The UI Utility closes the panel (the user closed it): the next line opens it again, since the
  setting is still on. [NEEDS CLARIFICATION: when he closes the player window himself, should the next line reopen it, or does closing it mean "not now" until some reset?]
- The contract version the utility speaks is not 1: nothing is opened, logged once.
- Tests never start the real UI Utility; a test that needs one uses silent mode on a private
  socket, so no window and no sound.
- A test never submits anything under the live narrator's launchd label or touches its socket.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST hand each heard line to the UI Utility's player when, and only when,
  the player setting is on, no hold stands, and the UI Utility is installed.
- **FR-002**: The hand-over MUST follow the published caller contract v1: `hello` (contract 1, with
  the `player` piece), then `open` of panel `player` with the line's WAV path and its words
  (`w`, `start`, `end`).
- **FR-003**: The system MUST launch the UI Utility when its socket does not answer, in the
  background (never taking focus), and then retry the hand-over within a bounded wait.
- **FR-004**: The system MUST reuse one player panel for successive lines.
- **FR-005**: The hand-over MUST run off the speech path, never delay or stop a line, and keep only
  the newest pending line.
- **FR-006**: Failures (no socket, refused request, timeout, wrong contract version) MUST be logged
  once per kind and shown in `history status`; they MUST NOT raise into the daemon.
- **FR-007**: Tests MUST NOT start the real UI Utility outside silent mode, and MUST NOT touch the
  live narrator's socket, pid file or launchd label.
- **FR-008**: The player setting keeps its one name (`[history] player`, `history player-on|off`,
  MCP `history` actions `player-on|player-off`) and stays off by default.

### Key Entities

- **Heard line**: a history entry (ADR-020): WAV path, sidecar with text, words with times, sender,
  spoken-at time.
- **Player panel**: the one UI Utility panel auto-speech owns, known by a fixed id.
- **Hand-over outcome**: what happened to the last line offered: off, held, not installed, opened,
  or failed with a code.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: With the player on, every heard line is shown in the player within 2 s of being
  exported when the UI Utility is running, and within 10 s when it has to be launched.
- **SC-002**: Zero player launches or hand-overs while the setting is off or a hold stands, across
  the test suite.
- **SC-003**: A hung or absent UI Utility adds no measurable delay to speech: a line's handling
  returns within 50 ms of the hand-over being queued.
- **SC-004**: The full suite stays green and silent, with no window opened and no test daemon under
  the live label.

## Assumptions

- The UI Utility is installed as `~/Applications/UI Utility.app` (no `ui-utility` command on PATH
  today); `open -g -a "UI Utility"` launches it in the background, per the contract.
- A history WAV is a format the player can load (the contract asks for "a local audio file").
- Word times are the sidecar's estimate (ADR-020); the player takes them as given.
- The sidecar text may carry say markup (`[[rate 180]]`); stripping it is a separate follow-up the
  Desk has queued after this merge.
- The live narrator is not restarted by this work; it picks the feature up when the Desk restarts it.
