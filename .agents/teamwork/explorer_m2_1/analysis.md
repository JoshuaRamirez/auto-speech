# Analysis Report: Daemon Socket Server Integration (Milestone M2.1)

**Investigator**: `explorer_m2_1`  
**Target File**: `plugin/scripts/python/narrator_service.py`  
**Status**: Read-only Investigation Complete  
**Date**: 2026-10-03  

---

## 1. Executive Summary

Milestone M2 establishes thin-client IPC between the CLI (`speak.py`) and the unified daemon (`narrator_service.py`) via a UNIX domain stream socket at `/tmp/auto-speech-daemon.sock`. This investigation specifies the full daemon socket server integration into `narrator_service.py`.

Key findings:
- **Server Class**: Use `socketserver.ThreadingUnixStreamServer` configured with `daemon_threads = True` and `allow_reuse_address = True`. This handles bursts of concurrent clients (tested in Tier 3 with 10 parallel threads) without blocking the accept loop or hanging process shutdown.
- **Socket Path**: Default to `/tmp/auto-speech-daemon.sock`, configurable via the `AUTO_SPEECH_DAEMON_SOCK` environment variable and constructor parameter `socket_path` in `NarratorService`.
- **Lifecycle & Cleanup**:
  - *Startup*: Check if the socket file exists (or is a stale symlink/socket) and safely unlink it via `unlink(missing_ok=True)` immediately before binding.
  - *Shutdown*: Cleanly shutdown and close the socket server, join the listener thread, and unlink the socket file in the `finally:` block of `NarratorService.run()`. Add an `atexit` fallback hook for emergency process exit.
- **Request Handling**: Stream reading in chunks (`recv(4096)`) until EOF (`b""`), UTF-8 decode with `errors="replace"`, strip whitespace, reject empty payloads, and enqueue non-empty text to `self._tts_queue` using thread-safe drop-oldest backpressure guarded by a threading lock.
- **Worker Affinity**: `_tts_worker` already supports processing `str` queue items directly by invoking `self._speak(phase)`. Incoming socket requests seamlessly integrate without altering MLX stream-affinity rules.
- **Idle Timer Integration**: Each incoming client request updates `self._last_event_ts = time.time()` to prevent premature idle shutdown during active speech sessions.

---

## 2. Codebase Baseline & Observations

### 2.1 Current Implementation State of `narrator_service.py`
- Post-M1, `narrator_service.py` runs in-process `TTSEngine` with `NativeAudioSink` on `_tts_worker` (Thread).
- `_tts_queue` is a bounded `queue.Queue(maxsize=self._max_queue)` (default: 32).
- `_tts_worker` contains:
  ```python
  if isinstance(phase, str):
      self._speak(phase)
  ```
  This indicates `_tts_worker` is already structured to accept raw `str` speech requests.
- The daemon loop runs `_tail_events()` on the main thread, monitoring `/tmp/auto-speech-narrator-events.jsonl` and calculating idle timeout (`time.time() - self._last_event_ts > self._idle_shutdown`).
- There is currently **no socket server** running in `narrator_service.py`. E2E tests in `tests/e2e/test_tier1_features.py:270` specifically assert:
  `"socketserver" in content or "socket.AF_UNIX" in content` and `"auto-speech-daemon.sock" in content`.

### 2.2 Test Suite Expectations
The E2E test suite (`tests/e2e/`) verifies:
1. **Tier 1 (Feature Coverage)**:
   - `test_tier1_r2_daemon_socket_enqueues_to_tts_queue`: Asserts `socketserver` or `socket.AF_UNIX` and `auto-speech-daemon.sock` are present in `narrator_service.py`.
   - `test_tier1_r2_socket_wire_protocol_stream_handling`: Asserts stream chunking and EOF termination over UNIX domain socket.
   - `test_tier1_r2_daemon_cleans_up_socket_file_on_shutdown`: Asserts socket file is unlinked on shutdown.
2. **Tier 2 (Boundaries & Edge Cases)**:
   - `test_tier2_r2_empty_stdin_ignored_by_daemon`: Whitespace/empty inputs do not enqueue items to `_tts_queue`.
   - `test_tier2_r2_special_characters_and_multiline_payload`: Emojis, quotes, metacharacters, and multiline UTF-8 text transmit faithfully.
   - `test_tier2_r2_large_socket_payload_chunking`: 128 KB text payloads are handled across multiple `recv()` calls without truncation.
   - `test_tier2_r2_abrupt_client_disconnect`: Client disconnection with `SO_LINGER 0` does not crash the server thread or leak exceptions.
   - `test_tier2_r2_stale_socket_file_cleanup_on_startup`: Pre-existing socket file is unlinked before `bind()`.
3. **Tier 3 (Combinations & Concurrency)**:
   - `test_tier3_concurrent_socket_and_jsonl_events`: Both socket client requests and JSONL event tailing write to `_tts_queue` concurrently.
   - `test_tier3_backpressure_queue_cap_with_socket_burst`: 45-item burst against 32-capacity queue drops the 13 oldest items without blocking clients.
   - `test_tier3_multi_client_concurrent_burst`: 10 simultaneous client threads run without deadlock or dropped connections.
4. **Tier 4 (Scenarios)**:
   - `test_tier4_scenario_full_user_session_lifecycle`: Full boot -> tool events -> `speak.py` CLI -> clean shutdown -> artifact verification.
   - `test_tier4_scenario_daemon_reboot_and_client_reconnection`: Daemon restart resiliency.

---

## 3. Detailed Architectural Specifications

### 3.1 Server Class: `socketserver.ThreadingUnixStreamServer`
- **Class Hierarchy**:
  `socketserver.ThreadingUnixStreamServer` subclasses `socketserver.ThreadingMixIn` and `socketserver.UnixStreamServer`.
- **Why NOT `UnixStreamServer`**:
  `UnixStreamServer` processes connections sequentially. If a client sends slowly, pauses before closing its write half, or if 10 clients connect concurrently (as in `test_tier3_multi_client_concurrent_burst`), a single-threaded server blocks incoming connections, causing CLI timeouts.
- **Thread Configuration**:
  ```python
  class _DaemonSocketServer(socketserver.ThreadingUnixStreamServer):
      daemon_threads = True
      allow_reuse_address = True

      def __init__(
          self,
          server_address: str | Path,
          RequestHandlerClass: type[socketserver.BaseRequestHandler],
          service: NarratorService,
      ) -> None:
          self.service = service
          super().__init__(str(server_address), RequestHandlerClass)
  ```
  - `daemon_threads = True`: Worker threads spawned by `ThreadingMixIn` are daemon threads, preventing them from preventing clean daemon process exit.
  - `allow_reuse_address = True`: Standard practice across socket servers.

### 3.2 Socket Path Resolution
Socket path resolution follows a strict 3-tier hierarchy:
1. **Explicit constructor argument**: `NarratorService(socket_path=...)` (allows unit test dependency injection).
2. **Environment variable override**: `os.environ.get("AUTO_SPEECH_DAEMON_SOCK")` (required by `IsolatedEnvironment` sandboxes in `tests/e2e/harness.py`).
3. **Default path**: `/tmp/auto-speech-daemon.sock` (production default defined in `PROJECT.md`).

Module-level definition:
```python
DEFAULT_SOCKET_PATH = Path("/tmp/auto-speech-daemon.sock")
SOCKET_FILE = Path(
    os.environ.get("AUTO_SPEECH_DAEMON_SOCK", str(DEFAULT_SOCKET_PATH))
)
```

In `NarratorService.__init__`:
```python
if socket_path is not None:
    self._socket_path = Path(socket_path)
else:
    self._socket_path = Path(
        os.environ.get("AUTO_SPEECH_DAEMON_SOCK", str(DEFAULT_SOCKET_PATH))
    )
```

### 3.3 Lifecycle & Cleanup Mechanics

#### Startup Unlink (Stale Socket Recovery)
Before calling `super().__init__(str(server_address), RequestHandlerClass)` (which invokes `socket.bind()`):
```python
def _safe_unlink_socket(path: Path) -> None:
    try:
        if path.exists() or path.is_symlink():
            path.unlink(missing_ok=True)
    except OSError as exc:
        _log(f"warning: failed to unlink socket path {path}: {exc}")
```
- Ensures that if the daemon crashed previously or a stale socket / regular file exists on disk, `bind()` does not fail with `OSError: [Errno 48] Address already in use`.
- Directory safety: Ensure parent directory exists (`path.parent.mkdir(parents=True, exist_ok=True)`).

#### Shutdown Unlink
During daemon termination:
1. Call `server.shutdown()` on the `_DaemonSocketServer` instance.
2. Call `server.server_close()` to close the listening socket file descriptor.
3. Join the listener thread (`self._socket_thread.join(timeout=2.0)`).
4. Unlink the socket file (`self._socket_path.unlink(missing_ok=True)`).
5. Clean up any `atexit` registration.

#### Integration in `NarratorService.run()`
```python
    try:
        self._start_socket_server()
        self._tail_events()
    finally:
        self._stop_socket_server()
        # Drain / terminate _tts_worker
        try:
            self._tts_queue.put_nowait(None)
        except queue.Full:
            ...
        tts_thread.join(timeout=5.0)
        try:
            PID_FILE.unlink()
        except OSError:
            pass
        self._fsm.transition(NOT_RUNNING)
        _log("shutdown")
```

#### Order of Shutdown Rationale
The socket server MUST be stopped *before* the `_tts_worker` thread:
1. Stopping the socket server first guarantees no new incoming requests arrive while the queue is being finalized.
2. Any in-flight requests that completed `handle()` before `shutdown()` returned are already in `_tts_queue`.
3. The sentinel `None` is then placed into `_tts_queue`, allowing `_tts_worker` to finish processing queued speech items before joining.

### 3.4 Request Handler & Wire Protocol

#### Protocol Specification
- **Client**: Connects to `self._socket_path`, sends UTF-8 encoded text in one or more chunks, and closes its write end via `sock.shutdown(socket.SHUT_WR)` or `sock.close()`.
- **Server**: Reads chunks of up to 4096 bytes until EOF (`b""`), decodes text, strips whitespace, enqueues non-empty strings, and sends an optional acknowledgment (`b"OK\n"`).

#### Implementation Details
```python
class _DaemonRequestHandler(socketserver.BaseRequestHandler):
    server: _DaemonSocketServer

    def handle(self) -> None:
        chunks: list[bytes] = []
        while True:
            try:
                data = self.request.recv(4096)
                if not data:
                    break
                chunks.append(data)
            except (ConnectionResetError, BrokenPipeError, OSError):
                break

        if not chunks:
            return

        try:
            text = b"".join(chunks).decode("utf-8", errors="replace")
        except Exception:
            return

        cleaned = text.strip()
        if not cleaned:
            return

        service: NarratorService | None = getattr(self.server, "service", None)
        if service is not None:
            service.enqueue_text(cleaned)

        try:
            self.request.sendall(b"OK\n")
        except (ConnectionResetError, BrokenPipeError, OSError):
            pass
```

Key features:
1. **Chunk accumulation**: Handles arbitrary-sized payloads (including >128 KB) by buffering chunks until EOF.
2. **Resilience to abrupt disconnect**: Protects both `recv()` and `sendall()` with `(ConnectionResetError, BrokenPipeError, OSError)` try-except blocks.
3. **Encoding safety**: Uses `errors="replace"` to ensure malformed byte sequences do not crash the handler thread.
4. **Whitespace filtering**: Validates `cleaned = text.strip()`; if empty, no item is added to `_tts_queue`.

### 3.5 Queue Enqueueing & Concurrency Protection

Because requests can now arrive concurrently from multiple socket client threads while `_tail_events` is actively enqueueing phase events on the main thread, queue operations must be thread-safe.

#### Shared Queue Lock
Add `self._queue_lock = threading.Lock()` to `NarratorService.__init__`.

#### Enqueue Method
```python
def enqueue_text(self, text: str) -> None:
    """Enqueue a text request from the UNIX domain socket into _tts_queue."""
    text = text.strip()
    if not text:
        return

    self._last_event_ts = time.time()
    lock = getattr(self, "_queue_lock", None)
    if lock is not None:
        with lock:
            self._enqueue_item(text)
    else:
        self._enqueue_item(text)

def _enqueue_item(self, item: Phase | str | dict) -> None:
    """Thread-safe bounded put with drop-oldest backpressure."""
    try:
        self._tts_queue.put_nowait(item)
        self._update_depth(self._tts_queue.qsize())
        return
    except queue.Full:
        pass

    try:
        dropped = self._tts_queue.get_nowait()
        self._tts_queue.task_done()
        self._dropped_phases += 1
        tag = getattr(getattr(dropped, "category", None), "value", type(dropped).__name__)
        _log(
            f"queue full (max={self._max_queue}); dropped oldest "
            f"item={tag} total_dropped={self._dropped_phases}"
        )
    except queue.Empty:
        pass

    try:
        self._tts_queue.put_nowait(item)
    except queue.Full:
        self._dropped_phases += 1
        _log(
            f"queue still full after shed; dropped new item "
            f"total_dropped={self._dropped_phases}"
        )
    self._update_depth(self._tts_queue.qsize())
```

#### Refactoring `_enqueue_phase`
`_enqueue_phase(self, phase: Phase)` can simply acquire `self._queue_lock` (if present) and delegate to `self._enqueue_item(phase)`.

#### Backward Compatibility with Unit Tests
In `tests/test_narrator_service.py:28`, `_bare_service` instantiates `NarratorService.__new__(NarratorService)` without calling `__init__`, defining only `_max_queue`, `_tts_queue`, and `_dropped_phases`.
Checking `lock = getattr(self, "_queue_lock", None)` ensures that all 21 unit tests in `test_narrator_service.py` continue to pass without modification.

---

## 4. Proposed Code Changes for Implementer

### 4.1 Imports to add to `narrator_service.py`
```python
import atexit
import socket
import socketserver
```

### 4.2 Module Constants
```python
DEFAULT_SOCKET_PATH = Path("/tmp/auto-speech-daemon.sock")
SOCKET_FILE = Path(
    os.environ.get("AUTO_SPEECH_DAEMON_SOCK", str(DEFAULT_SOCKET_PATH))
)
```

### 4.3 `_DaemonSocketServer` and `_DaemonRequestHandler`
Place immediately before `NarratorService` class definition:

```python
class _DaemonRequestHandler(socketserver.BaseRequestHandler):
    """Handles incoming client requests on the UNIX domain stream socket."""

    server: _DaemonSocketServer

    def handle(self) -> None:
        chunks: list[bytes] = []
        while True:
            try:
                data = self.request.recv(4096)
                if not data:
                    break
                chunks.append(data)
            except (ConnectionResetError, BrokenPipeError, OSError):
                break

        if not chunks:
            return

        try:
            text = b"".join(chunks).decode("utf-8", errors="replace")
        except Exception:
            return

        cleaned = text.strip()
        if not cleaned:
            return

        service = getattr(self.server, "service", None)
        if service is not None:
            service.enqueue_text(cleaned)

        try:
            self.request.sendall(b"OK\n")
        except (ConnectionResetError, BrokenPipeError, OSError):
            pass


class _DaemonSocketServer(socketserver.ThreadingUnixStreamServer):
    """Multi-threaded UNIX domain stream socket server for daemon IPC."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        server_address: str | Path,
        RequestHandlerClass: type[socketserver.BaseRequestHandler],
        service: NarratorService,
    ) -> None:
        self.service = service
        super().__init__(str(server_address), RequestHandlerClass)
```

### 4.4 Modifications to `NarratorService.__init__`
```python
    def __init__(
        self,
        *,
        sink: NativeAudioSink | None = None,
        engine: TTSEngine | None = None,
        synth: ResilientSynthesizer | None = None,
        profile: VoiceProfile | None = None,
        socket_path: Path | str | None = None,
    ) -> None:
        ...
        if socket_path is not None:
            self._socket_path = Path(socket_path)
        else:
            self._socket_path = Path(
                os.environ.get("AUTO_SPEECH_DAEMON_SOCK", str(DEFAULT_SOCKET_PATH))
            )
        self._socket_server: _DaemonSocketServer | None = None
        self._socket_thread: threading.Thread | None = None
        self._queue_lock = threading.Lock()
        self._atexit_registered = False
```

### 4.5 Socket Server Lifecycle Methods on `NarratorService`
```python
    def _start_socket_server(self) -> None:
        """Start the background UNIX socket listener thread."""
        if self._socket_server is not None:
            return

        self._socket_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            if self._socket_path.exists() or self._socket_path.is_symlink():
                _log(f"unlinking stale socket file: {self._socket_path}")
                self._socket_path.unlink(missing_ok=True)
        except OSError as exc:
            _log(f"warning unlinking stale socket file: {exc}")

        try:
            self._socket_server = _DaemonSocketServer(
                self._socket_path,
                _DaemonRequestHandler,
                service=self,
            )
            self._socket_thread = threading.Thread(
                target=self._socket_server.serve_forever,
                name="daemon-socket-server",
                daemon=True,
            )
            self._socket_thread.start()
            if not self._atexit_registered:
                atexit.register(self._cleanup_socket_file)
                self._atexit_registered = True
            _log(f"socket server listening at {self._socket_path}")
        except Exception as exc:
            _log(f"failed to start socket server at {self._socket_path}: {exc}")
            raise

    def _cleanup_socket_file(self) -> None:
        """Safe unlinking of socket file (can be called via atexit)."""
        try:
            if self._socket_path.exists() or self._socket_path.is_symlink():
                self._socket_path.unlink(missing_ok=True)
        except OSError:
            pass

    def _stop_socket_server(self) -> None:
        """Stop socket server, close listening socket, and remove file."""
        server = self._socket_server
        self._socket_server = None
        if server is not None:
            try:
                server.shutdown()
            except Exception as exc:
                _log(f"error shutting down socket server: {exc}")
            try:
                server.server_close()
            except Exception as exc:
                _log(f"error closing socket server: {exc}")

        if self._socket_thread is not None and self._socket_thread.is_alive():
            self._socket_thread.join(timeout=2.0)
            self._socket_thread = None

        self._cleanup_socket_file()
        if self._atexit_registered:
            try:
                atexit.unregister(self._cleanup_socket_file)
            except Exception:
                pass
            self._atexit_registered = False
```

### 4.6 Updates in `run()`
In `NarratorService.run()`:
```python
        tts_thread = threading.Thread(target=self._tts_worker, daemon=True)
        tts_thread.start()

        try:
            self._start_socket_server()
            self._tail_events()
        finally:
            self._stop_socket_server()
            ...
```

---

## 5. Risk Assessment & Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| **Stale socket on reboot/crash** | `bind()` fails with `Address already in use` | `_start_socket_server` calls `unlink(missing_ok=True)` on the path before instantiating server. |
| **Concurrent client bursts** | Race condition shedding queue items or deadlock | Protected by `self._queue_lock` in `_enqueue_item`. |
| **Abrupt client disconnection** | Unhandled `BrokenPipeError` crashes handler thread | `recv()` and `sendall()` wrapped in `(ConnectionResetError, BrokenPipeError, OSError)` try-except. |
| **Large payload truncation** | Messages > OS buffer size clipped | Chunked buffer read loop until EOF (`data == b""`). |
| **Premature idle shutdown** | Daemon terminates while receiving CLI speech | `enqueue_text` updates `self._last_event_ts = time.time()`. |
| **Unit test breakage (`_bare_service`)** | `AttributeError: '_queue_lock'` on uninitialized instances | Use `getattr(self, "_queue_lock", None)` defensively. |
| **Socket path length limit (macOS 104 bytes)** | `bind()` fails with `AF_UNIX path too long` | Default is `/tmp/auto-speech-daemon.sock` (29 chars); env vars from `tempfile` are within bounds. |

---

## 6. Verification Plan

1. **Unit Tests**:
   - Run `PYTHONPATH=. pytest tests/test_narrator_service.py` to ensure existing 21 unit tests pass.
   - Add unit tests for `_start_socket_server`, `enqueue_text`, stale socket removal, and socket unlinking on shutdown.
2. **E2E Tests**:
   - Run `PYTHONPATH=. pytest tests/e2e/test_tier1_features.py -k "test_tier1_r2"` to verify Tier 1 R2 tests.
   - Run `PYTHONPATH=. pytest tests/e2e/test_tier2_boundaries.py -k "test_tier2_r2"` to verify Tier 2 boundary cases.
   - Run `PYTHONPATH=. pytest tests/e2e/test_tier3_combinations.py -k "socket"` to verify concurrency and backpressure.
   - Run `PYTHONPATH=. pytest tests/e2e/test_tier4_scenarios.py` to verify full session lifecycle.
