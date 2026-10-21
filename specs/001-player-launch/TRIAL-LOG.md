# Spec Kit trial log — 001-player-launch (1339 T09)

The Desk's order #m1021-0025-f8be (his 00:24 yes to one Spec Kit trial). Spec Kit 1.2.2
(`~/.local/bin/specify`), worktree `.worktrees/player-launch`, branch `feat/player-launch` on
main 425142d. Times are local (MST), to the second, from `date` at each step's start and end.

**How the steps were run**: the `/speckit-*` skills are not loaded in this session (they were
written into the worktree's `.claude/skills/` after the session started, and set
`disable-model-invocation: true`), so each step was run by hand: read its `SKILL.md` and followed
it in order. Every SKILL.md's extension-hook checks were skipped as written ("if
`.specify/extensions.yml` does not exist, skip silently"): none is installed.

## 0. init — 01:31:48 → 01:31:49
- Ran `specify init --here --force --integration claude --script sh --non-interactive --ignore-agent-tools`.
- Wrote `.specify/` (scripts, templates, workflow, memory/constitution.md template,
  integration manifests) and `.claude/skills/speckit-*/SKILL.md` (10 skills).
- `.claude/` is in the repo's `.gitignore`; the skills were added with `git add -f`.
- Commit 458d342.

## 0b. hooks off — 01:32:00 → 01:32:01
- `disable-model-invocation: false → true` in all 10 `.claude/skills/speckit-*/SKILL.md`.
- No extension installed: no `.specify/extensions.yml`. No settings file written by Spec Kit
  (no `.claude/settings*.json`).
- Commit ac5b6ef.

## 1. constitution — 01:32:04 → 01:32:29
- Wrote `.specify/memory/constitution.md` v1.0.0: five principles (his machine and silence;
  holds honoured; nothing opens on its own; one name on every surface; make the work succeed,
  hard checks last), standing limits, workflow, governance.
- Sources: the agent brief (`~/Developer/Auto-Speech-Dev/BRIEF.md` rules as carried in this
  session), the global CLAUDE.md doctrines, PROJECT.md, ADR-015/018/019/020. The repo has no
  CLAUDE.md of its own.
- Commit 3d7276c.

## 2. specify — 01:33:01 → 01:33:50
- Wrote `specs/001-player-launch/spec.md` (4 user stories, 8 FRs, 4 SCs, edge cases,
  assumptions) and `specs/001-player-launch/checklists/requirements.md`; set
  `.specify/feature.json` (git-ignored by Spec Kit, local only).
- Read first: the contract (CONTRACT.md v1 rev 1.1), ADR-020's player section and his 13:38
  words; checked that the UI Utility is installed (`~/Applications/UI Utility.app`, no
  `ui-utility` on PATH).
- 2 `[NEEDS CLARIFICATION]` markers left for clarify (see step 3).
- Deviation: the skill's step 8c presents markers to the user as a choice table and waits; per the
  order, none goes to him, so they were left for clarify to answer as an agent.

## 3. clarify — 01:34:06 → 01:34:30
- Ran `.specify/scripts/bash/check-prerequisites.sh --json --paths-only`. It reported
  `BRANCH: 001-player-launch` (it reads `.specify/feature.json`, not git) and the paths.
- Scanned the spec against the skill's taxonomy. Asked and answered 5 questions (the skill's
  maximum) myself, from the contract, the repo and the ADRs. None went to him. Integrated them
  into `spec.md` (a new `## Clarifications` section, user story 1 scenario 4, edge cases, FR-004,
  FR-007) and toggled one item in `checklists/requirements.md` (13/16 → 14/16).
- Deviation: the skill asks one question at a time and waits for the user's letter; here the
  agent answered each from the sources, as the order says.

### The [NEEDS CLARIFICATION] markers, verbatim
1. `[NEEDS CLARIFICATION: does the player play the line again on open (his "plays the line's WAV"), when the narrator has already played it aloud through mpv, or does it open ready to play?]`
2. `[NEEDS CLARIFICATION: when he closes the player window himself, should the next line reopen it, or does closing it mean "not now" until some reset?]`

### The questions clarify asked, verbatim, with the answer and its source
1. **Question:** Should the player play the line again when it opens, when the narrator has
   already played it aloud? (marker 1)
   **A:** No. It opens ready to play, at the start (`play` false); he presses play to hear it
   again.
   **Source:** ADR-020 exports a line only after the sink played it (I-20.1), so by then he has
   heard it; contract `open.play` defaults to false (rev 1.1); constitution I (no doubled sound).
   His 13:38 "plays the line's WAV" is kept as the player's play control.
   **REAL**: it changed the spec (scenario 1.4, FR-004) and fixes the plan's `open` request.
2. **Question:** When he closes the player window himself, should the next line open it again?
   (marker 2)
   **A:** Yes. The setting is the switch (`history player-off` turns it off); closing the window
   only puts that line away.
   **Source:** ADR-020's player section ("after each line"), `config/history.toml.example`
   (`player`: "Open the UI Utility's player on each line after it plays"); `closed` frees the id
   (contract, Events).
   **EMPTY**: the answer was already on disk.
3. **Question:** Does auto-speech keep its connection to the UI Utility open while the player shows
   a line, or connect once per line?
   **A:** It keeps one connection open for the panel's life, and reconnects and opens again on the
   next line if the connection is lost.
   **Source:** the contract says a connection that opened a panel receives its events, and says
   nothing about a panel outliving its connection; holding it is the reading that is safe either
   way.
   **REAL**: it changed FR-004 and the edge cases, and makes the plan a long-lived client on a
   worker thread rather than a connect-per-line call.
4. **Question:** Do the tests start the real UI Utility in its silent mode, or a stand-in?
   **A:** A stand-in that speaks the contract on a private socket; the real app is never started by
   a test. The suite also sets the contract's silent switch (`UI_UTILITY_SILENT=1`) and a private
   socket (`UI_UTILITY_SOCK`), so anything that did launch it would be silent and apart from his
   copy.
   **Source:** contract "Launch": "A copy already running keeps its mode". A silent copy
   started by a test would go on serving his live player with no window, and launching twice
   leaves one copy. Constitution I.
   **REAL**: it changed FR-007 and adds `run_all.sh` settings and a launch argument to the plan.
5. **Question:** Does this feature show the history of earlier lines in the player?
   **A:** No. Contract v1 has one piece, the player, with one clip; the history stays
   `auto-speech history list`, and the player shows the newest line.
   **Source:** contract `hello` → `"pieces":["player"]`, `open` takes one `clip`.
   **EMPTY**: the answer was already on disk (it added one out-of-scope edge case line).

## 4. plan — 01:34:48 → 01:36:07
- Ran `.specify/scripts/bash/setup-plan.sh --json`, which copied the plan template to `plan.md`.
- Wrote `plan.md` (summary, technical context, a Constitution Check against principles I to V,
  passing before and after design, structure), `research.md` (R1 to R8: transport, launch, off
  the speech path, connection life, the `open` request, the hold twice, status across processes,
  tests), `data-model.md` (heard line, outcomes, panel, connection),
  `contracts/player-handover.md` (the subset of contract v1 used), and `quickstart.md`.
- One finding from planning that the spec didn't hold: the CLI's `history status` runs in another
  process from the daemon, so the last outcome needs a state file (R7).
- The skill's "dispatch research agents" was done inline: every question was answered from the
  contract and the code, with no agents.
