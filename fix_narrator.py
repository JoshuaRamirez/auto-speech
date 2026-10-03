import re

with open("plugin/scripts/python/narrator_service.py", "r") as f:
    content = f.read()

# Replace imports
content = re.sub(
    r'import socketserver\n',
    '',
    content
)

content = content.replace(
    "from resilient_synthesizer import ResilientSynthesizer",
    "from unix_ipc_server import _DaemonSocketServer\nfrom tts_executor import TTSExecutor\nfrom resilient_synthesizer import ResilientSynthesizer"
)

# Remove the two classes
content = re.sub(
    r'class _DaemonRequestHandler\(socketserver\.BaseRequestHandler\):.*?(?=class NarratorService:)',
    '',
    content,
    flags=re.DOTALL
)

# Replace _socket_server initialization
content = content.replace(
    "_DaemonSocketServer(\n                self._socket_path,\n                _DaemonRequestHandler,\n                service=self,\n            )",
    "_DaemonSocketServer(\n                self._socket_path,\n                on_text=self.enqueue_text,\n            )"
)

# Replace TTSEngine and ResilientSynthesizer fields with TTSExecutor
# __init__:
content = content.replace(
    "engine: TTSEngine | None = None,\n        synth: ResilientSynthesizer | None = None,",
    "tts_executor: TTSExecutor | None = None,"
)
content = content.replace(
    "self._engine: TTSEngine | None = engine\n        self._synth: ResilientSynthesizer | None = synth",
    "self._tts_executor = tts_executor or TTSExecutor()"
)

# Replace _ensure_tts_initialized
content = re.sub(
    r'def _ensure_tts_initialized\(self\) -> None:.*?def _tts_worker\(self\) -> None:',
    r'''def _ensure_tts_initialized(self) -> None:
        if getattr(self, "_tts_initialized", False):
            return
        if self._profile is None:
            self._profile = _load_profile_or_fallback(self._config)
        self._tts_executor.ensure_loaded()
        self._tts_initialized = True

    def _tts_worker(self) -> None:''',
    content,
    flags=re.DOTALL
)

# Replace _speak logic to use TTSExecutor
old_speak = """    def _speak(self, line: str) -> None:
        \"\"\"Synthesize and play speech in-process on the _tts_worker thread.

        Replaces legacy external process sprawl and mpv duration sleep hacks.
        Uses ResilientSynthesizer to handle any Kokoro generation faults,
        plays synchronously via NativeAudioSink, and cleans up temporary WAV files.
        \"\"\"
        line = line.strip()
        if not line:
            return

        _log(f"speak: {line}")
        self._ensure_tts_initialized()
        if self._synth is None or self._sink is None or self._profile is None:
            _log(f"tts engine not available; dropped narration: {line[:50]!r}")
            return

        with tempfile.NamedTemporaryFile(prefix="narrator_", suffix=".wav", delete=False) as f:
            temp_wav = Path(f.name)

        try:
            has_audio = self._synth.synthesize_one(line, self._profile, temp_wav)
            if has_audio and temp_wav.exists() and temp_wav.stat().st_size > 0:
                _log(f"playing audio ({temp_wav.stat().st_size} bytes)...")
                self._sink.play(temp_wav)
            else:
                _log(f"no speakable audio generated for: {line[:50]!r}")
        except Exception as exc:
            _log(f"speak error: {exc!r}")
        finally:
            temp_wav.unlink(missing_ok=True)
            temp_wav.with_suffix(temp_wav.suffix + ".partial").unlink(missing_ok=True)
            for frag in temp_wav.parent.glob(f"{temp_wav.stem}-*.wav"):
                frag.unlink(missing_ok=True)"""

new_speak = """    def _speak(self, line: str) -> None:
        line = line.strip()
        if not line:
            return

        _log(f"speak: {line}")
        self._ensure_tts_initialized()
        if self._sink is None or self._profile is None:
            _log(f"tts engine not available; dropped narration: {line[:50]!r}")
            return

        with tempfile.NamedTemporaryFile(prefix="narrator_", suffix=".wav", delete=False) as f:
            temp_wav = Path(f.name)

        try:
            has_audio = self._tts_executor.submit(self._tts_executor.synth.synthesize_one, line, self._profile, temp_wav)
            if has_audio and temp_wav.exists() and temp_wav.stat().st_size > 0:
                _log(f"playing audio ({temp_wav.stat().st_size} bytes)...")
                self._sink.play(temp_wav)
            else:
                _log(f"no speakable audio generated for: {line[:50]!r}")
        except Exception as exc:
            _log(f"speak error: {exc!r}")
        finally:
            temp_wav.unlink(missing_ok=True)
            temp_wav.with_suffix(temp_wav.suffix + ".partial").unlink(missing_ok=True)
            for frag in temp_wav.parent.glob(f"{temp_wav.stem}-*.wav"):
                frag.unlink(missing_ok=True)"""

content = content.replace(old_speak, new_speak)

with open("plugin/scripts/python/narrator_service.py", "w") as f:
    f.write(content)

