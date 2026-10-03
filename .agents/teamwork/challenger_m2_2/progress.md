# Progress — Challenger M2.2

- Last visited: 2026-10-03T19:08:00Z
- Status: Stress Testing Complete — Preparing Handoff Report
- Current Step: Finalizing handoff report and briefing.

## Executed Stress Tests
1. `tests/test_socket_server_stress.py`:
   - `test_ungraceful_sigkill_crash_recovers_stale_socket`: PASSED (stale socket reclaimed cleanly after SIGKILL).
   - `test_repeated_ungraceful_kill_rebind_cycles`: PASSED (5 consecutive ungraceful kill/rebind cycles verified).
   - `test_stale_corrupted_or_non_socket_file_reclaimed`: PASSED (reclaims garbage regular files and dangling symlinks).
   - `test_sequential_burst_250_requests_enforces_drop_oldest_cap`: PASSED (queue capped at 32, exactly 218 dropped, latest 32 items preserved in FIFO order, RSS delta < 2MB).
   - `test_client_abrupt_disconnect_during_backpressure_flood`: PASSED (50 SO_LINGER 0 resets tolerated without thread crashes).
   - `test_simultaneous_socket_and_jsonl_event_ingestion`: PASSED (40 socket + 40 JSONL events ingested concurrently, 80 items synthesized).
   - `test_mixed_phase_and_string_drop_oldest_under_backpressure`: PASSED (drop-oldest sheds both Phase and str items safely).

## Identified Defects
1. **Listen Backlog Starvation under High-Frequency Flood / Concurrency**:
   - `_DaemonSocketServer` defaults to `request_queue_size = 5`.
   - Rapid sequential or concurrent bursts of 200+ requests cause listen queue overflow and immediate `ConnectionRefusedError: [Errno 61] Connection refused`.
   - `speak.py` lacks retry mechanism with backoff on connection refusal.
