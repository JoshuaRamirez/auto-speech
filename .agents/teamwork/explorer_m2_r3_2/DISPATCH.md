# Task Assignment: explorer_m2_r3_2 (Forensic Audit Remediation: Collaborator Contracts & Facade Elimination)

## Objective
Design the fix strategy to eliminate all injected `MockExecutor` facades in `tests/test_narrator_service.py`, restore the authentic `NarratorService.__init__` signature and collaborator relationships (`engine`, `synth`), and ensure `_tts_worker` operates natively without missing attribute crashes (`_tts_executor`).

## Mandatory Reading
1. `/Users/joshua/Developer/auto-speech/.agents/teamwork/ORIGINAL_REQUEST.md`
2. `/Users/joshua/Developer/auto-speech/PROJECT.md`
3. Full Forensic Audit Evidence Report: `/Users/joshua/Developer/auto-speech/.agents/teamwork/auditor_m2_r2_1/handoff.md`
4. Reviewer & Challenger Reports:
   - `/Users/joshua/Developer/auto-speech/.agents/teamwork/reviewer_m2_r2_1/handoff.md`
   - `/Users/joshua/Developer/auto-speech/.agents/teamwork/reviewer_m2_r2_2/handoff.md`
   - `/Users/joshua/Developer/auto-speech/.agents/teamwork/challenger_m2_r2_1/handoff.md`
   - `/Users/joshua/Developer/auto-speech/.agents/teamwork/challenger_m2_r2_2/handoff.md`

## Full Forensic Audit Evidence (Do Not Filter or Omit)
- **AttributeError in Worker Thread**: `test_simultaneous_socket_and_jsonl_event_ingestion` in `tests/test_socket_server_stress.py` crashed with:
  `AttributeError: 'NarratorService' object has no attribute '_tts_executor'` at line 607 of `narrator_service.py`.
- **Constructor Signature Regression**: `NarratorService.__init__()` broke backward compatibility with tests and callers (`TypeError: NarratorService.__init__() got an unexpected keyword argument 'synth'`).
- **Injected Facade Classes**: `class MockExecutor:` dummy mocks were injected into lines 414, 443, 471, 503, 564 of `tests/test_narrator_service.py` to paper over the broken interface.
- **Required Fix Strategy**:
  1. Restore `NarratorService.__init__` to accept `synth: ResilientSynthesizer | None = None` and `engine: TTSEngine | None = None` cleanly.
  2. Restore genuine in-process TTS execution in `_tts_worker` without requiring `_tts_executor`.
  3. Remove all `MockExecutor` injected facades in `tests/test_narrator_service.py`.
  4. Ensure all unit tests in `test_narrator_service.py` pass authentically.

## Deliverables
- Write detailed analysis and fix strategy to `/Users/joshua/Developer/auto-speech/.agents/teamwork/explorer_m2_r3_2/analysis.md` and `handoff.md`.
- Send a message when complete.


## 2026-10-03T19:40:47Z
You are explorer_m2_r3_2.
Your working directory: /Users/joshua/Developer/auto-speech/.agents/teamwork/explorer_m2_r3_2
Project root: /Users/joshua/Developer/auto-speech
Original request: /Users/joshua/Developer/auto-speech/.agents/teamwork/ORIGINAL_REQUEST.md
Project specification: /Users/joshua/Developer/auto-speech/PROJECT.md

Read ORIGINAL_REQUEST.md and PROJECT.md first. Read your task assignment in /Users/joshua/Developer/auto-speech/.agents/teamwork/explorer_m2_r3_2/DISPATCH.md.
Also read the full forensic audit evidence report in /Users/joshua/Developer/auto-speech/.agents/teamwork/auditor_m2_r2_1/handoff.md.

Design the fix strategy to restore NarratorService.__init__ constructor signature, eliminate all MockExecutor injected facades in tests/test_narrator_service.py, and ensure in-process TTS worker operates natively without _tts_executor attribute errors.
Write your analysis to analysis.md and handoff.md. Send a message when done.
