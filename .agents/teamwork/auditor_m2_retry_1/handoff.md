# Forensic Audit Report: Milestone M2 Bugfixes Retry

**Work Product**: `/Users/joshua/Developer/auto-speech-worktree` (`plugin/scripts/python/mcp_server.py`, `tests/test_mcp_server.py`)  
**Profile**: General Project (Development Mode per `ORIGINAL_REQUEST.md` §2026-10-06T20:47:50Z)  
**Auditor**: `teamwork_preview_auditor` (`auditor_m2_retry_1`)  
**Timestamp**: 2026-10-06T22:58:00Z  
**Verdict**: **CLEAN**

---

### Phase Results
- **Phase 1.1: Hardcoded Test Output Detection**: PASS — No hardcoded test responses, hardcoded hashes, fake mock bypasses, or environment flags (`PYTEST`, `TESTING`) detected in production code.
- **Phase 1.2: Facade & Dummy Implementation Detection**: PASS — Both `_call_autoplay_turn` and `_call_session_configure` execute genuine validation, state checking, and filesystem/client mutations.
- **Phase 1.3: Pre-populated Verification Artifact Detection**: PASS — Zero stale or pre-populated log/attestation artifacts in the worktree.
- **Phase 2.1: Behavioral Verification & Bugfix Efficacy**: PASS — Empirically verified that foreign session fallbacks in `_call_autoplay_turn` are blocked with `isError: True`, and `_call_session_configure` properly validates subsystems and routes actions without dictionary collisions.
- **Phase 2.2: Test Suite Authenticity & Non-Tautological Assertions**: PASS — The 6 new tests in `tests/test_mcp_server.py` assert concrete negative boundary rejections and positive state changes on disk; no tautological (`assert True`) or self-certifying tests.
- **Phase 2.3: Build & Regression Test Execution**: PASS — `tests/test_mcp_server.py` passed 58/58 tests cleanly; `tests/run_all.sh` passed 50/50 test suites; `ruff check` reported 0 violations.
- **Phase 2.4: Adversarial Edge Case & Boundary Stress-Testing**: PASS — Independent stress-testing across bad inputs (integers, empty strings, whitespace, bogus subsystems, missing values, unhandled actions) all rejected with defensive errors.

---

## 1. Observation

### Obs 1: Ground-Truth User Constraints & Scope
- **Ground-Truth Source**: `/Users/joshua/Developer/auto-speech/.agents/teamwork/ORIGINAL_REQUEST.md` (header `## 2026-10-06T20:47:50Z`) and `/Users/joshua/Developer/auto-speech/PROJECT.md`.
- **Integrity Mode**: `development` mode.
- **Audit Target**: Bugfixes in `/Users/joshua/Developer/auto-speech-worktree`:
  1. `plugin/scripts/python/mcp_server.py`: `_call_autoplay_turn` and `_call_session_configure`.
  2. `tests/test_mcp_server.py`: test additions.

### Obs 2: Code Diff in `plugin/scripts/python/mcp_server.py`
1. **`_call_autoplay_turn` validation & transcript matching**:
   - Lines 517–523: Type checking and whitespace stripping for `session_id`:
     ```python
     session_id = args.get("session_id")
     if session_id is not None and not isinstance(session_id, str):
         return _ok(req_id, _tool_result("session_id must be a string", is_error=True))
     if session_id is not None and not session_id.strip():
         return _ok(
             req_id, _tool_result("session_id must be a non-empty string", is_error=True)
         )
     ```
   - Lines 581–589: Transcript matching validation preventing foreign fallback:
     ```python
     if session_id and (t_path.name != f"{session_id}.jsonl" and t_path.stem != session_id):
         return _ok(
             req_id,
             _tool_result(
                 f"session transcript not found for session_id: {session_id}",
                 is_error=True,
             ),
         )
     ```

2. **`_call_session_configure` subsystem validation & action segregation**:
   - Lines 1002–1009: Strict `subsystem` whitelist:
     ```python
     if subsystem is not None and subsystem not in ("autoplay", "narrate"):
         return _ok(
             req_id,
             _tool_result(
                 f"invalid subsystem: {subsystem!r} (expected autoplay, narrate)",
                 is_error=True,
             ),
         )
     ```
   - Lines 1011–1039: Subsystem `autoplay` action segregation:
     - `action in ("on", "off", "status")` -> routes to `autoplay`
     - `action == "mode"` -> requires non-empty `value`, translates size presets (`small`/`medium`/`large`) to `summary` mode with `size`, or sets custom mode.
     - `action == "scope"` -> requires non-empty `value`, routes to `scope`.
     - invalid action -> returns `is_error=True`: `f"invalid autoplay action: {action!r} (expected on, off, status, mode, scope)"`.
   - Lines 1040–1050: Subsystem `narrate` action segregation:
     - `action in ("on", "off", "status")` -> routes to `narrate`.
     - invalid action -> returns `is_error=True`: `f"invalid narrate action: {action!r} (expected on, off, status)"`.
   - Lines 1051–1077: Subsystem `None` fallback handling:
     - `action in ("mode", "scope")` with required `value` validation.
     - unhandled action -> returns `is_error=True`: `f"unhandled action {action!r} without valid subsystem"`.

### Obs 3: Test Additions in `tests/test_mcp_server.py`
Six new tests were added and registered in `main()`:
1. `test_autoplay_turn_nonexistent_session_id_returns_tool_error`:
   - Verifies that when `locator.locate` returns an unrelated transcript, `_call_autoplay_turn` rejects it with `isError: True` and does not call `speak()`.
2. `test_autoplay_turn_matching_session_id_succeeds`:
   - Verifies that when `locator.locate` returns a matching `{session_id}.jsonl`, `_call_autoplay_turn` succeeds and speaks.
3. `test_autoplay_turn_invalid_session_id_validation`:
   - Verifies rejection of integer `session_id`, empty `""`, and whitespace `"   "`.
4. `test_autoplay_turn_nonexistent_session_id_with_real_locator_returns_tool_error`:
   - Integration test with the real `TranscriptLocator` instance reproducing the fallback scenario and confirming rejection at the tool boundary.
5. `test_session_configure_subsystem_and_action_validation`:
   - Verifies negative boundary rejections for `subsystem="bogus"`, unhandled standalone actions, invalid actions under autoplay/narrate, and missing `value` for mode/scope.
6. `test_session_configure_alternative_dispatch_routing`:
   - Verifies positive state mutation for alternative dispatch:
     - `subsystem="autoplay", action="on"` -> creates disk marker.
     - `subsystem="autoplay", action="mode", value="raw"` -> writes `autoplay.toml`.
     - `subsystem="autoplay", action="scope", value="solo"` -> sets `SoloScope`.
     - `subsystem="autoplay", action="scope", value="all"` -> clears `SoloScope`.
     - `subsystem="narrate", action="on"` -> creates narrate session marker.
     - standalone `action="mode"` and `action="scope"` -> updates config and scope.

### Obs 4: Empirical Test Execution
1. **MCP Server Unit Suite**:
   - Command: `.venv/bin/python tests/test_mcp_server.py`
   - Output: `mcp_server: 58 tests passed`, exit code 0.
2. **Lint & Code Quality**:
   - Command: `.venv/bin/ruff check plugin/scripts/python/mcp_server.py tests/test_mcp_server.py`
   - Output: `All checks passed!`, exit code 0.
3. **Full Project Test Suite**:
   - Command: `bash tests/run_all.sh`
   - Output: `ran: 50, failed: 0, all tests passed`, exit code 0.
4. **Independent Empirical Adversarial Stress Test**:
   - Tested integer and collection types for `session_id`.
   - Tested foreign session transcript interception with live `TranscriptLocator`.
   - Tested invalid subsystems (`"bogus"`, `"auto_play"`, `"narrator"`, `99`, `True`).
   - Tested invalid actions (`"start"`, `"stop"`, `"restart"`, `"delete"`, `"mode"` under narrate).
   - Tested missing values for mode and scope across `None`, `""`, and `"   "`.
   - Tested unhandled actions without subsystem.
   - Output: `ALL EMPIRICAL STRESS TESTS PASSED SUCCESSFULLY!`, exit code 0.

---

## 2. Logic Chain

1. **Defect Remediation in `_call_autoplay_turn` (Obs 2.1, Obs 3.1, Obs 3.4)**:
   The defect where `TranscriptLocator`'s fallback to the newest project transcript caused unrelated session text to be spoken is prevented by explicitly validating `if session_id and (t_path.name != f"{session_id}.jsonl" and t_path.stem != session_id)`. The check directly references the caller's target session ID. The fix contains no mocks, no hardcoded session IDs, and properly returns `isError: True`.
2. **Defect Remediation in `_call_session_configure` (Obs 2.2, Obs 3.5, Obs 3.6)**:
   The defect where invalid subsystems silently succeeded by falling through to the status reporter is prevented by the strict check `if subsystem is not None and subsystem not in ("autoplay", "narrate")`. The parameter collision where `action="mode"` set `args["autoplay"] = "mode"` is prevented by segregating action dispatch into distinct branches that map `action="mode"` to `args["mode"]` and `args["size"]`.
3. **Absence of Facades or Mock Leakage (Obs 2, Obs 4)**:
   Static analysis and execution verify that all logic in `mcp_server.py` is computational, performs defensive parameter checks, and modifies real disk files / calls real client objects. There are zero production mock references or test-detection bypass flags.
4. **Authenticity of Test Additions (Obs 3, Obs 4.1)**:
   The 6 added tests are non-tautological. They assert disk file existence, TOML configuration content, `SoloScope` internal state, exact error strings, and mock call counts. They include an end-to-end integration test with the real `TranscriptLocator` confirming bug reproduction and resolution.
5. **System Invariant Preservation (Obs 4.3)**:
   The full regression suite `bash tests/run_all.sh` executed all 50 test suites cleanly with 0 failures, proving that the bugfixes did not break any existing invariants or client workflows.

---

## 3. Caveats

- `systemone` local daemon was not running (`[Errno 61] Connection refused`); all verifications were conducted through empirical Python scripts, unit tests, full test suites, and static analysis.
- No other caveats.

---

## 4. Conclusion

The bugfixes in `plugin/scripts/python/mcp_server.py` and `tests/test_mcp_server.py` in `/Users/joshua/Developer/auto-speech-worktree` are genuine, effective, and free of facades, hardcoded outputs, or mock bypasses. All tests pass authentically, and full codebase regression tests are 100% green.

The verdict is **CLEAN**.

---

## 5. Verification Method

To independently reproduce this forensic audit:

1. **Run MCP Server Unit Suite**:
   ```bash
   cd /Users/joshua/Developer/auto-speech-worktree
   .venv/bin/python tests/test_mcp_server.py
   ```
   *Expected*: `mcp_server: 58 tests passed`, exit code 0.

2. **Run Ruff Linter**:
   ```bash
   cd /Users/joshua/Developer/auto-speech-worktree
   .venv/bin/ruff check plugin/scripts/python/mcp_server.py tests/test_mcp_server.py
   ```
   *Expected*: `All checks passed!`, exit code 0.

3. **Run Independent Adversarial Stress Test**:
   ```bash
   cd /Users/joshua/Developer/auto-speech-worktree
   PYTHONPATH=plugin/scripts/python .venv/bin/python -c '
   import json, tempfile
   from pathlib import Path
   from mcp_server import McpServer
   from transcript_locator import TranscriptLocator
   from unittest.mock import patch

   tmp = Path(tempfile.mkdtemp())
   proj = tmp / ".claude" / "projects" / "-test-proj"
   proj.mkdir(parents=True, exist_ok=True)
   (proj / "foreign.jsonl").write_text("dummy\n")

   loc = TranscriptLocator()
   loc.PROJECTS_ROOT = tmp / ".claude" / "projects"
   server = McpServer(home=tmp, locator=loc)

   with patch("pathlib.Path.cwd", return_value=Path("/test/proj")):
       res = server.handle_line(json.dumps({
           "jsonrpc": "2.0", "id": 1, "method": "tools/call",
           "params": {"name": "autoplay_turn", "arguments": {"session_id": "missing-sess"}}
       }))["result"]
       assert res["isError"] is True
       assert "session transcript not found" in res["content"][0]["text"]

   r2 = server.handle_line(json.dumps({
       "jsonrpc": "2.0", "id": 2, "method": "tools/call",
       "params": {"name": "session_configure", "arguments": {"subsystem": "bogus"}}
   }))["result"]
   assert r2["isError"] is True
   assert "invalid subsystem: '\''bogus'\''" in r2["content"][0]["text"]
   print("PASSED")
   '
   ```
   *Expected*: `PASSED`, exit code 0.

4. **Run Full Project Test Suite**:
   ```bash
   cd /Users/joshua/Developer/auto-speech-worktree
   bash tests/run_all.sh
   ```
   *Expected*: `ran: 50, failed: 0, all tests passed`, exit code 0.
