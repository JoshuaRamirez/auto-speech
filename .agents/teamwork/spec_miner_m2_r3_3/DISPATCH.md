# Task Assignment: spec_miner_m2_r3_3 (Forensic Audit Remediation: Workspace Hygiene & Test Runner Health)

## Objective
Design the plan to purge all 14 untracked scratch patch scripts in the project root, repair the SyntaxError / import placement in `tests/test_socket_server_stress.py`, and resolve all 21 `ruff` lint errors across production and test code.

## Mandatory Reading
1. `/Users/joshua/Developer/auto-speech/.agents/teamwork/ORIGINAL_REQUEST.md`
2. `/Users/joshua/Developer/auto-speech/PROJECT.md`
3. Full Forensic Audit Evidence Report: `/Users/joshua/Developer/auto-speech/.agents/teamwork/auditor_m2_r2_1/handoff.md`

## Full Forensic Audit Evidence (Do Not Filter or Omit)
- **Scratch Patch Scripts in Root**: 14 untracked patch scripts (`fix_exit.py`, `fix_narrator.py`, `fix_seek.py`, `fix_stress_test.py`, `fix_test.py`, `fix_test_import.py`, `fix_test_import_top.py`, `fix_test_narrator.py`, `fix_test_tts.py`, `fix_web_server.py`, `patch_others.py`, `patch_stress.py`, `patch_stress_proper.py`, `unpatch.py`) violate clean repository layout.
- **Stress Test SyntaxError**: `tests/test_socket_server_stress.py` has a misplaced `from __future__ import annotations` statement causing SyntaxError / ImportError under standard unittest discovery.
- **21 Lint Violations**: Unused imports (`socket`, `resilient_synthesizer.ResilientSynthesizer`, `tts_engine.TTSEngine`), misplaced future imports, and out-of-order imports fail `ruff check`.

## Deliverables
- List all scratch files to delete.
- Specify exact fixes for `tests/test_socket_server_stress.py` and lint errors.
- Write detailed findings and remediation plan to `/Users/joshua/Developer/auto-speech/.agents/teamwork/spec_miner_m2_r3_3/analysis.md` and `handoff.md`.
- Send a message when complete.

## 2026-10-03T19:40:47Z
You are spec_miner_m2_r3_3.
Your working directory: /Users/joshua/Developer/auto-speech/.agents/teamwork/spec_miner_m2_r3_3
Project root: /Users/joshua/Developer/auto-speech
Original request: /Users/joshua/Developer/auto-speech/.agents/teamwork/ORIGINAL_REQUEST.md
Project specification: /Users/joshua/Developer/auto-speech/PROJECT.md

Read ORIGINAL_REQUEST.md and PROJECT.md first. Read your task assignment in /Users/joshua/Developer/auto-speech/.agents/teamwork/spec_miner_m2_r3_3/DISPATCH.md.
Also read the full forensic audit evidence report in /Users/joshua/Developer/auto-speech/.agents/teamwork/auditor_m2_r2_1/handoff.md.

Design the cleanup plan to remove all 14 scratch patch scripts (fix_*.py, patch_*.py, unpatch.py) from the root directory, fix SyntaxErrors / import placements in tests/test_socket_server_stress.py, and eliminate all 21 ruff lint errors.
Write your analysis to analysis.md and handoff.md. Send a message when done.
