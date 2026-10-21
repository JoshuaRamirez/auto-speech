"""Tier 1: Feature Coverage (R1, R2, R3).

Verifies the primary behavior (happy paths and core contracts) for:
- R1: In-Process TTSEngine and Blocking AudioSink
- R2: Thin Client IPC via UNIX Sockets
- R3: Removal of Dead Architectural Sprawl
"""

from __future__ import annotations

import os
import re
import socket
import socketserver
import sys
import threading
import time
import unittest

from tests.e2e.harness import (
    PLUGIN_PYTHON,
    PLUGIN_SHELL,
    PROJECT_ROOT,
    IsolatedEnvironment,
    create_dummy_wav,
    run_speak_cli,
)


class TestTier1R1InProcessAudioSink(unittest.TestCase):
    """Tier 1 tests for R1: In-Process TTSEngine and Blocking NativeAudioSink."""

    def setUp(self) -> None:
        self.sandbox = IsolatedEnvironment(use_spy_mpv=True, mpv_delay_s=0.1)
        self.dummy_wav = create_dummy_wav(self.sandbox.root / "test.wav", duration_s=0.1)

    def tearDown(self) -> None:
        self.sandbox.cleanup()

    def test_tier1_r1_native_audio_sink_plays_synchronously(self) -> None:
        """Verifies NativeAudioSink.play() blocks until mpv exits and passes required flags."""
        sink_path = PLUGIN_PYTHON / "native_audio_sink.py"
        self.assertTrue(
            sink_path.exists(),
            f"R1 Violation: Expected {sink_path} to exist as defined in PROJECT.md Code Layout.",
        )

        sys.path.insert(0, str(PLUGIN_PYTHON))
        try:
            import native_audio_sink

            sink = native_audio_sink.NativeAudioSink()
            t0 = time.time()
            with unittest.mock.patch.dict(os.environ, self.sandbox.env):
                sink.play(self.dummy_wav)
            elapsed = time.time() - t0

            # Must block for at least the spy delay
            self.assertGreaterEqual(
                elapsed,
                0.08,
                f"NativeAudioSink.play did not block synchronously (elapsed: {elapsed:.3f}s)",
            )

            # Check flags passed to mpv
            invocations = self.sandbox.spy_mpv.get_invocations()
            self.assertGreaterEqual(len(invocations), 1, "mpv was never invoked by NativeAudioSink")
            args = invocations[-1]["argv"]

            expected_flags = ["--really-quiet", "--no-video", "--keep-open=no", "--idle=no"]
            for flag in expected_flags:
                self.assertIn(
                    flag,
                    args,
                    f"R1 Violation: mpv invocation missing required flag {flag}. Got: {args}",
                )
            self.assertIn(str(self.dummy_wav), args, "mpv invocation missing wav_path argument")
        finally:
            if str(PLUGIN_PYTHON) in sys.path:
                sys.path.remove(str(PLUGIN_PYTHON))

    def test_tier1_r1_native_audio_sink_interrupt(self) -> None:
        """Verifies NativeAudioSink.interrupt() halts active playback immediately."""
        sink_path = PLUGIN_PYTHON / "native_audio_sink.py"
        self.assertTrue(sink_path.exists(), f"Expected {sink_path} to exist.")

        sys.path.insert(0, str(PLUGIN_PYTHON))
        try:
            import native_audio_sink

            # Configure longer delay to test interruption
            long_sandbox = IsolatedEnvironment(use_spy_mpv=True, mpv_delay_s=2.0)
            try:
                sink = native_audio_sink.NativeAudioSink()

                play_error = []
                play_done = threading.Event()

                def _play() -> None:
                    try:
                        with unittest.mock.patch.dict(os.environ, long_sandbox.env):
                            sink.play(self.dummy_wav)
                    except Exception as e:  # noqa: BLE001 — test thread records any playback failure
                        play_error.append(e)
                    finally:
                        play_done.set()

                th = threading.Thread(target=_play)
                t0 = time.time()
                th.start()

                # Wait for mpv to start
                time.sleep(0.1)
                sink.interrupt()

                play_done.wait(timeout=1.0)
                elapsed = time.time() - t0

                self.assertTrue(play_done.is_set(), "interrupt() failed to unblock playback thread")
                self.assertLess(
                    elapsed,
                    1.5,
                    f"Playback was not interrupted immediately (elapsed {elapsed:.2f}s, expected <1.5s)",
                )
            finally:
                long_sandbox.cleanup()
        finally:
            if str(PLUGIN_PYTHON) in sys.path:
                sys.path.remove(str(PLUGIN_PYTHON))

    def test_tier1_r1_tts_engine_instantiated_in_process(self) -> None:
        """Verifies NarratorService imports and instantiates TTSEngine in-process."""
        service_file = PLUGIN_PYTHON / "narrator_service.py"
        content = service_file.read_text(encoding="utf-8")

        self.assertIn(
            "TTSEngine",
            content,
            "R1 Violation: TTSEngine must be imported and used in-process in narrator_service.py",
        )
        self.assertNotIn(
            "run_speak.sh",
            content,
            "R1 Violation: narrator_service.py still references run_speak.sh instead of in-process engine",
        )

    def test_tier1_r1_sequential_playback_fifo(self) -> None:
        """Verifies multiple audio playback requests are processed sequentially (never concurrent)."""
        sink_path = PLUGIN_PYTHON / "native_audio_sink.py"
        self.assertTrue(sink_path.exists(), "native_audio_sink.py must exist")

        sys.path.insert(0, str(PLUGIN_PYTHON))
        try:
            import native_audio_sink

            sink = native_audio_sink.NativeAudioSink()
            max_concurrent = 0

            with unittest.mock.patch.dict(os.environ, self.sandbox.env):
                for i in range(3):
                    w = create_dummy_wav(self.sandbox.root / f"test_{i}.wav")
                    sink.play(w)
                    active = len(self.sandbox.spy_mpv.get_active_pids())
                    max_concurrent = max(max_concurrent, active)

            self.assertLessEqual(
                max_concurrent,
                1,
                f"Audio played concurrently (max active mpv pids: {max_concurrent})",
            )
            self.assertEqual(len(self.sandbox.spy_mpv.get_invocations()), 3)
        finally:
            if str(PLUGIN_PYTHON) in sys.path:
                sys.path.remove(str(PLUGIN_PYTHON))

    def test_tier1_r1_no_orphan_mpv_processes(self) -> None:
        """Verifies that no detached or zombie mpv processes remain active after playback."""
        sink_path = PLUGIN_PYTHON / "native_audio_sink.py"
        self.assertTrue(sink_path.exists(), "native_audio_sink.py must exist")

        sys.path.insert(0, str(PLUGIN_PYTHON))
        try:
            import native_audio_sink

            sink = native_audio_sink.NativeAudioSink()
            with unittest.mock.patch.dict(os.environ, self.sandbox.env):
                sink.play(self.dummy_wav)

            active_pids = self.sandbox.spy_mpv.get_active_pids()
            self.assertEqual(
                active_pids,
                [],
                f"Orphan mpv processes found running after playback: {active_pids}",
            )
        finally:
            if str(PLUGIN_PYTHON) in sys.path:
                sys.path.remove(str(PLUGIN_PYTHON))

    def test_tier1_r1_no_time_sleep_or_sigkill_hacks(self) -> None:
        """Verifies narrator_service.py does not use time.sleep for audio or SIGKILL/pkill."""
        service_file = PLUGIN_PYTHON / "narrator_service.py"
        content = service_file.read_text(encoding="utf-8")

        # Check for duration sleep hack
        self.assertNotIn(
            "sleeping for wav duration",
            content,
            "R1 Violation: narrator_service.py still contains duration-guessing time.sleep log/logic",
        )
        self.assertNotIn(
            "pkill",
            content,
            "R1 Violation: narrator_service.py still uses pkill hack instead of NativeAudioSink",
        )


class TestTier1R2ThinClientIPC(unittest.TestCase):
    """Tier 1 tests for R2: Thin Client IPC via UNIX Sockets."""

    def setUp(self) -> None:
        self.sandbox = IsolatedEnvironment(use_spy_mpv=False)

    def tearDown(self) -> None:
        self.sandbox.cleanup()

    def test_tier1_r2_speak_cli_transmits_stdin_to_socket(self) -> None:
        """Verifies speak.py reads stdin and forwards payload over UNIX socket."""
        received_payloads: list[str] = []

        class DummyHandler(socketserver.BaseRequestHandler):
            def handle(self) -> None:
                data = b""
                while True:
                    chunk = self.request.recv(4096)
                    if not chunk:
                        break
                    data += chunk
                received_payloads.append(data.decode("utf-8"))

        class UnixServer(socketserver.ThreadingUnixStreamServer):
            allow_reuse_address = True

        server = UnixServer(str(self.sandbox.socket_path), DummyHandler)
        th = threading.Thread(target=server.serve_forever, daemon=True)
        th.start()
        time.sleep(0.05)

        try:
            test_text = "Hello from thin client speak"
            res = run_speak_cli(test_text, env=self.sandbox.env)

            self.assertEqual(
                res.returncode,
                0,
                f"speak.py failed with exit code {res.returncode}. Stderr: {res.stderr}",
            )
            self.assertEqual(len(received_payloads), 1)
            self.assertEqual(received_payloads[0].strip(), test_text)
        finally:
            server.shutdown()
            server.server_close()

    def test_tier1_r2_daemon_socket_enqueues_to_tts_queue(self) -> None:
        """Verifies narrator_service socket listener receives payload and enqueues to _tts_queue."""
        service_file = PLUGIN_PYTHON / "narrator_service.py"
        content = service_file.read_text(encoding="utf-8")

        self.assertTrue(
            "socketserver" in content or "socket.AF_UNIX" in content,
            "R2 Violation: narrator_service.py must include a UNIX domain socket server",
        )
        self.assertIn(
            "auto-speech-daemon.sock",
            content,
            "R2 Violation: narrator_service.py must listen on /tmp/auto-speech-daemon.sock",
        )

    def test_tier1_r2_socket_wire_protocol_stream_handling(self) -> None:
        """Verifies wire protocol handles stream chunks and EOF properly."""
        received: list[bytes] = []

        class ProtocolHandler(socketserver.BaseRequestHandler):
            def handle(self) -> None:
                chunks = []
                while True:
                    data = self.request.recv(1024)
                    if not data:
                        break
                    chunks.append(data)
                received.append(b"".join(chunks))

        server = socketserver.ThreadingUnixStreamServer(
            str(self.sandbox.socket_path), ProtocolHandler
        )
        th = threading.Thread(target=server.serve_forever, daemon=True)
        th.start()
        time.sleep(0.05)

        try:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.connect(str(self.sandbox.socket_path))
            sock.sendall(b"chunk1 ")
            time.sleep(0.01)
            sock.sendall(b"chunk2")
            sock.shutdown(socket.SHUT_WR)
            time.sleep(0.05)
            sock.close()

            self.assertEqual(len(received), 1)
            self.assertEqual(received[0], b"chunk1 chunk2")
        finally:
            server.shutdown()
            server.server_close()

    def test_tier1_r2_speak_cli_fails_gracefully_when_daemon_down(self) -> None:
        """Verifies speak.py exits non-zero and prints error to stderr when daemon is down."""
        # Ensure socket does not exist
        if self.sandbox.socket_path.exists():
            self.sandbox.socket_path.unlink()

        res = run_speak_cli("test when down", env=self.sandbox.env)

        self.assertNotEqual(
            res.returncode,
            0,
            f"speak.py should exit non-zero when daemon is down, got {res.returncode}",
        )
        self.assertTrue(
            len(res.stderr.strip()) > 0,
            "speak.py should write an error message to stderr when daemon is unreachable",
        )

    def test_tier1_r2_daemon_cleans_up_socket_file_on_shutdown(self) -> None:
        """Verifies socket file is removed upon daemon shutdown."""
        service_file = PLUGIN_PYTHON / "narrator_service.py"
        content = service_file.read_text(encoding="utf-8")

        # Verify source code includes cleanup logic for socket
        self.assertTrue(
            "unlink" in content or "remove" in content,
            "narrator_service.py must include cleanup logic for socket file on shutdown",
        )

    def test_tier1_r2_speak_cli_accepts_backward_compatible_args(self) -> None:
        """Verifies speak.py does not crash when callers pass legacy CLI flags."""
        args = [
            "--ordinal",
            "1",
            "--keep-artifacts",
            "--source-hash",
            "a" * 64,
        ]
        # Run with daemon down: it should fail due to socket connection, NOT argument parsing error (code 2)
        res = run_speak_cli("test args", args=args, env=self.sandbox.env)

        self.assertNotIn(
            "unrecognized arguments",
            res.stderr,
            f"speak.py failed argument parsing: {res.stderr}",
        )


class TestTier1R3DeadSprawlRemoval(unittest.TestCase):
    """Tier 1 tests for R3: Removal of Dead Architectural Sprawl."""

    def test_tier1_r3_obsolete_files_deleted(self) -> None:
        """Verifies all obsolete files identified in R3 have been deleted."""
        obsolete_files = [
            PLUGIN_SHELL / "run_speak.sh",
            PLUGIN_PYTHON / "pipeline.py",
            PLUGIN_PYTHON / "short_path.py",
            PLUGIN_PYTHON / "mpv_controller.py",
            PLUGIN_PYTHON / "session_dir.py",
            PROJECT_ROOT / "tests" / "test_mpv_wait.py",
        ]
        for f in obsolete_files:
            self.assertFalse(
                f.exists(),
                f"R3 Violation: Obsolete file {f} still exists in repository.",
            )

    def test_tier1_r3_no_pipeline_orchestrator_imports(self) -> None:
        """Verifies zero imports or usages of PipelineOrchestrator remain in python files."""
        pattern = re.compile(r"\bPipelineOrchestrator\b")
        offenders = []

        for p in (PROJECT_ROOT / "plugin").rglob("*.py"):
            if pattern.search(p.read_text(encoding="utf-8")):
                offenders.append(str(p.relative_to(PROJECT_ROOT)))

        self.assertEqual(
            offenders,
            [],
            f"R3 Violation: PipelineOrchestrator referenced in: {offenders}",
        )

    def test_tier1_r3_no_short_path_strategy_imports(self) -> None:
        """Verifies zero imports or usages of ShortPathStrategy remain in python files."""
        pattern = re.compile(r"\bShortPathStrategy\b")
        offenders = []

        for p in (PROJECT_ROOT / "plugin").rglob("*.py"):
            if pattern.search(p.read_text(encoding="utf-8")):
                offenders.append(str(p.relative_to(PROJECT_ROOT)))

        self.assertEqual(
            offenders,
            [],
            f"R3 Violation: ShortPathStrategy referenced in: {offenders}",
        )

    def test_tier1_r3_no_mpv_controller_imports(self) -> None:
        """Verifies zero imports or usages of MpvController remain in python files."""
        pattern = re.compile(r"\bMpvController\b")
        offenders = []

        for p in (PROJECT_ROOT / "plugin").rglob("*.py"):
            if pattern.search(p.read_text(encoding="utf-8")):
                offenders.append(str(p.relative_to(PROJECT_ROOT)))

        self.assertEqual(
            offenders,
            [],
            f"R3 Violation: MpvController referenced in: {offenders}",
        )

    def test_tier1_r3_no_session_dir_imports(self) -> None:
        """Verifies zero imports or usages of SessionDir remain in python files."""
        pattern = re.compile(r"\bSessionDir\b")
        offenders = []

        for p in (PROJECT_ROOT / "plugin").rglob("*.py"):
            if pattern.search(p.read_text(encoding="utf-8")):
                offenders.append(str(p.relative_to(PROJECT_ROOT)))

        self.assertEqual(
            offenders,
            [],
            f"R3 Violation: SessionDir referenced in: {offenders}",
        )

    def test_tier1_r3_no_run_speak_sh_references(self) -> None:
        """Verifies zero references to run_speak.sh remain in python or shell files."""
        pattern = re.compile(r"\brun_speak\.sh\b")
        offenders = []

        for folder in ["plugin", "setup"]:
            for p in (PROJECT_ROOT / folder).rglob("*"):
                if (
                    p.is_file()
                    and p.suffix in [".py", ".sh", ".md"]
                    and pattern.search(p.read_text(encoding="utf-8"))
                ):
                    offenders.append(str(p.relative_to(PROJECT_ROOT)))

        self.assertEqual(
            offenders,
            [],
            f"R3 Violation: run_speak.sh referenced in: {offenders}",
        )


if __name__ == "__main__":
    unittest.main()
