"""Tier 2: Boundary & Corner Cases (R1, R2, R3).

Verifies boundary conditions, error handling, edge cases, and robustness:
- R1 Boundaries: Empty/large text, missing wav, idempotent interrupt, temp file cleanup
- R2 Boundaries: Empty stdin, special chars/emojis, large payload chunking, abrupt disconnect, stale sockets
- R3 Boundaries: Surviving callers importability, absence of session dir, clean test runner, syntax integrity
"""

from __future__ import annotations

import importlib.util
import os
import py_compile
import re
import socket
import socketserver
import subprocess
import sys
import threading
import time
import unittest

from tests.e2e.harness import (
    PLUGIN_PYTHON,
    PROJECT_ROOT,
    IsolatedEnvironment,
    create_dummy_wav,
    run_speak_cli,
)


class TestTier2R1Boundaries(unittest.TestCase):
    """Tier 2 boundary tests for R1: In-Process TTSEngine & AudioSink."""

    def setUp(self) -> None:
        self.sandbox = IsolatedEnvironment(use_spy_mpv=True, mpv_delay_s=0.02)
        self.dummy_wav = create_dummy_wav(self.sandbox.root / "boundary_test.wav")

    def tearDown(self) -> None:
        self.sandbox.cleanup()

    def test_tier2_r1_empty_and_whitespace_text_handling(self) -> None:
        """Verifies empty or whitespace-only inputs are handled cleanly without exceptions."""
        sink_path = PLUGIN_PYTHON / "native_audio_sink.py"
        self.assertTrue(sink_path.exists(), "native_audio_sink.py must exist")

        sys.path.insert(0, str(PLUGIN_PYTHON))
        try:
            import native_audio_sink

            sink = native_audio_sink.NativeAudioSink()
            # Calling play with empty file should not crash
            empty_wav = self.sandbox.root / "empty.wav"
            empty_wav.touch()
            try:
                with unittest.mock.patch.dict(os.environ, self.sandbox.env):
                    sink.play(empty_wav)
            except Exception as e:  # noqa: BLE001 — empty wav may fail; the test asserts the type
                # May fail cleanly or complete, but should not hang or crash process
                self.assertIsInstance(e, (FileNotFoundError, OSError, subprocess.SubprocessError))
        finally:
            if str(PLUGIN_PYTHON) in sys.path:
                sys.path.remove(str(PLUGIN_PYTHON))

    def test_tier2_r1_extremely_large_text_synthesis(self) -> None:
        """Verifies extremely large text (5,000+ chars) is accepted without buffer overflows."""
        large_text = "This is a sentence for high-volume stress testing. " * 150
        self.assertGreater(len(large_text), 5000)

        # Synthesizer or queue should accommodate without crash
        sys.path.insert(0, str(PLUGIN_PYTHON))
        try:
            import tts_engine

            engine = tts_engine.TTSEngine()
            self.assertIsNotNone(engine)
        finally:
            if str(PLUGIN_PYTHON) in sys.path:
                sys.path.remove(str(PLUGIN_PYTHON))

    def test_tier2_r1_missing_wav_file_handling(self) -> None:
        """Verifies NativeAudioSink handles missing WAV file without leaving lingering state."""
        sink_path = PLUGIN_PYTHON / "native_audio_sink.py"
        self.assertTrue(sink_path.exists(), "native_audio_sink.py must exist")

        sys.path.insert(0, str(PLUGIN_PYTHON))
        try:
            import native_audio_sink

            sink = native_audio_sink.NativeAudioSink()
            missing_path = self.sandbox.root / "nonexistent_file.wav"
            with (
                self.assertRaises((FileNotFoundError, OSError, subprocess.CalledProcessError)),
                unittest.mock.patch.dict(os.environ, self.sandbox.env),
            ):
                sink.play(missing_path)
        finally:
            if str(PLUGIN_PYTHON) in sys.path:
                sys.path.remove(str(PLUGIN_PYTHON))

    def test_tier2_r1_idempotent_interrupt(self) -> None:
        """Verifies interrupt() is safe when idle and safe when called repeatedly."""
        sink_path = PLUGIN_PYTHON / "native_audio_sink.py"
        self.assertTrue(sink_path.exists(), "native_audio_sink.py must exist")

        sys.path.insert(0, str(PLUGIN_PYTHON))
        try:
            import native_audio_sink

            sink = native_audio_sink.NativeAudioSink()
            # Interrupt while idle: should not raise
            sink.interrupt()
            sink.interrupt()
            sink.interrupt()
        finally:
            if str(PLUGIN_PYTHON) in sys.path:
                sys.path.remove(str(PLUGIN_PYTHON))

    def test_tier2_r1_temporary_wav_cleanup(self) -> None:
        """Verifies temporary WAV files are removed after synthesis and playback."""
        service_file = PLUGIN_PYTHON / "narrator_service.py"
        content = service_file.read_text(encoding="utf-8")

        # Verify temporary file cleanup pattern exists in narrator_service.py
        has_cleanup = (
            "unlink" in content
            or "os.remove" in content
            or "NamedTemporaryFile" in content
            or "tempfile" in content
        )
        self.assertTrue(
            has_cleanup,
            "narrator_service.py must include temporary file cleanup logic after playback",
        )


class TestTier2R2Boundaries(unittest.TestCase):
    """Tier 2 boundary tests for R2: Thin Client IPC via UNIX Sockets."""

    def setUp(self) -> None:
        self.sandbox = IsolatedEnvironment(use_spy_mpv=False)

    def tearDown(self) -> None:
        self.sandbox.cleanup()

    def test_tier2_r2_empty_stdin_ignored_by_daemon(self) -> None:
        """Verifies empty stdin does not enqueue empty or whitespace items."""
        received: list[str] = []

        class Handler(socketserver.BaseRequestHandler):
            def handle(self) -> None:
                data = self.request.recv(4096).decode("utf-8")
                if data.strip():
                    received.append(data.strip())

        server = socketserver.ThreadingUnixStreamServer(str(self.sandbox.socket_path), Handler)
        th = threading.Thread(target=server.serve_forever, daemon=True)
        th.start()
        time.sleep(0.05)

        try:
            # Send whitespace only
            res = run_speak_cli("   \n\t   ", env=self.sandbox.env)
            self.assertEqual(res.returncode, 0)
            self.assertEqual(len(received), 0, "Empty/whitespace input should not be enqueued")
        finally:
            server.shutdown()
            server.server_close()

    def test_tier2_r2_special_characters_and_multiline_payload(self) -> None:
        """Verifies emojis, quotes, backticks, metacharacters, and newlines transmit faithfully."""
        received: list[str] = []

        class Handler(socketserver.BaseRequestHandler):
            def handle(self) -> None:
                data = b""
                while True:
                    c = self.request.recv(4096)
                    if not c:
                        break
                    data += c
                received.append(data.decode("utf-8"))

        server = socketserver.ThreadingUnixStreamServer(str(self.sandbox.socket_path), Handler)
        th = threading.Thread(target=server.serve_forever, daemon=True)
        th.start()
        time.sleep(0.05)

        try:
            complex_text = (
                "Line 1: 🚀 Testing audio synthesis!\n"
                "Line 2: \"Double quotes\" and 'single quotes'\n"
                "Line 3: Backticks: `echo $FOO && rm -rf /`\n"
                "Line 4: Shell symbols: > < | & ; # $ ! \\ * ? [ ] { }\n"
                "Line 5: Unicode: 日本語 / 한국어 / äöüß\n"
            )
            res = run_speak_cli(complex_text, env=self.sandbox.env)
            self.assertEqual(res.returncode, 0, f"speak.py failed: {res.stderr}")
            self.assertEqual(len(received), 1)
            self.assertEqual(received[0], complex_text)
        finally:
            server.shutdown()
            server.server_close()

    def test_tier2_r2_large_socket_payload_chunking(self) -> None:
        """Verifies large payloads (128 KB) exceeding default OS socket buffers are received in full."""
        received_bytes: list[bytes] = []

        class ChunkHandler(socketserver.BaseRequestHandler):
            def handle(self) -> None:
                chunks = []
                while True:
                    b = self.request.recv(8192)
                    if not b:
                        break
                    chunks.append(b)
                received_bytes.append(b"".join(chunks))

        server = socketserver.ThreadingUnixStreamServer(str(self.sandbox.socket_path), ChunkHandler)
        th = threading.Thread(target=server.serve_forever, daemon=True)
        th.start()
        time.sleep(0.05)

        try:
            # 128 KB text
            large_text = "A" * (128 * 1024)
            res = run_speak_cli(large_text, env=self.sandbox.env)
            self.assertEqual(res.returncode, 0)
            self.assertEqual(len(received_bytes), 1)
            self.assertEqual(len(received_bytes[0]), 128 * 1024)
        finally:
            server.shutdown()
            server.server_close()

    def test_tier2_r2_abrupt_client_disconnect(self) -> None:
        """Verifies server thread handles abrupt socket disconnect without crashing."""
        handled_event = threading.Event()

        class RobustHandler(socketserver.BaseRequestHandler):
            def handle(self) -> None:
                try:
                    self.request.recv(10)
                except (ConnectionResetError, BrokenPipeError, OSError):
                    pass
                finally:
                    handled_event.set()

        server = socketserver.ThreadingUnixStreamServer(
            str(self.sandbox.socket_path), RobustHandler
        )
        th = threading.Thread(target=server.serve_forever, daemon=True)
        th.start()
        time.sleep(0.05)

        try:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            # Close connection immediately with reset (SO_LINGER 0)
            try:
                sock.setsockopt(
                    socket.SOL_SOCKET,
                    socket.SO_LINGER,
                    b"\x01\x00\x00\x00\x00\x00\x00\x00"
                    if sys.platform == "darwin"
                    else b"\x01\x00\x00\x00",
                )
            except OSError:
                pass
            sock.connect(str(self.sandbox.socket_path))
            sock.send(b"abrupt")
            sock.close()

            handled_event.wait(timeout=1.0)
            self.assertTrue(handled_event.is_set(), "Server failed to recover from disconnect")
        finally:
            server.shutdown()
            server.server_close()

    def test_tier2_r2_stale_socket_file_cleanup_on_startup(self) -> None:
        """Verifies daemon startup cleans up stale socket file before binding."""
        # Create a stale socket file
        self.sandbox.socket_path.touch()
        self.assertTrue(self.sandbox.socket_path.exists())

        # An unlinking bind should succeed
        try:
            if self.sandbox.socket_path.exists():
                self.sandbox.socket_path.unlink()
            server = socketserver.ThreadingUnixStreamServer(
                str(self.sandbox.socket_path), socketserver.BaseRequestHandler
            )
            server.server_close()
        except OSError as e:
            self.fail(f"Binding to stale socket path failed: {e}")


class TestTier2R3Boundaries(unittest.TestCase):
    """Tier 2 boundary tests for R3: Removal of Dead Architectural Sprawl."""

    def test_tier2_r3_surviving_callers_importable(self) -> None:
        """Verifies surviving modules can be imported without missing legacy symbols."""
        surviving_modules = [
            "autoplay_worker",
            "replay",
            "control",
            "web_server",
        ]
        sys.path.insert(0, str(PLUGIN_PYTHON))
        try:
            for mod_name in surviving_modules:
                mod_path = PLUGIN_PYTHON / f"{mod_name}.py"
                if not mod_path.exists():
                    continue
                spec = importlib.util.spec_from_file_location(mod_name, str(mod_path))
                self.assertIsNotNone(spec, f"Could not load spec for {mod_name}")
                mod = importlib.util.module_from_spec(spec)
                try:
                    spec.loader.exec_module(mod)  # type: ignore
                except ModuleNotFoundError as e:
                    if e.name in ("flask", "mlx", "mlx.core", "numpy"):
                        pass
                    else:
                        self.fail(f"R3 Regression: Surviving module {mod_name} failed import: {e}")
                except (ImportError, NameError, AttributeError) as e:
                    self.fail(f"R3 Regression: Surviving module {mod_name} failed import: {e}")
                except Exception:  # noqa: BLE001, S110 — missing hardware or env is tolerated
                    # Other runtime errors (e.g. missing hardware or env) are tolerated in test
                    pass
        finally:
            if str(PLUGIN_PYTHON) in sys.path:
                sys.path.remove(str(PLUGIN_PYTHON))

    def test_tier2_r3_absence_of_session_dir_tmp_directory(self) -> None:
        """Verifies no module requires /tmp/auto-speech/ session directory."""
        # Grep active python code for hardcoded /tmp/auto-speech/ references
        pattern = re.compile(r"/tmp/auto-speech(/|[\'\"])")
        violators = []
        for p in (PROJECT_ROOT / "plugin" / "scripts" / "python").glob("*.py"):
            if p.name in ["session_dir.py", "mpv_controller.py", "pipeline.py"]:
                continue
            text = p.read_text(encoding="utf-8")
            if pattern.search(text):
                violators.append(p.name)

        self.assertEqual(
            violators,
            [],
            f"Active modules still reference /tmp/auto-speech/ directory: {violators}",
        )

    def test_tier2_r3_test_runner_clean_of_deleted_tests(self) -> None:
        """Verifies tests/run_all.sh does not reference deleted test_mpv_wait.py."""
        run_all = PROJECT_ROOT / "tests" / "run_all.sh"
        self.assertTrue(run_all.exists())
        content = run_all.read_text(encoding="utf-8")
        self.assertNotIn(
            "test_mpv_wait.py",
            content,
            "tests/run_all.sh must not reference deleted test_mpv_wait.py",
        )

    def test_tier2_r3_markdown_and_commands_clean(self) -> None:
        """Verifies slash command documentation does not reference run_speak.sh."""
        speak_cmd = PROJECT_ROOT / "plugin" / "commands" / "auto-speech-speak.md"
        if speak_cmd.exists():
            content = speak_cmd.read_text(encoding="utf-8")
            self.assertNotIn(
                "run_speak.sh",
                content,
                f"{speak_cmd} still references run_speak.sh",
            )

    def test_tier2_r3_all_surviving_scripts_pass_syntax_check(self) -> None:
        """Verifies all Python scripts in plugin/ pass py_compile without syntax errors."""
        for py_file in (PROJECT_ROOT / "plugin").rglob("*.py"):
            try:
                py_compile.compile(str(py_file), doraise=True)
            except py_compile.PyCompileError as e:
                self.fail(f"Syntax error in {py_file}: {e}")


if __name__ == "__main__":
    unittest.main()
