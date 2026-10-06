"""WebServer: localhost Flask app exposing the auto-speech pipeline + controls.

Run with:
  source .venv/bin/activate
  python plugin/scripts/python/web_server.py [--port 7860]
"""

from __future__ import annotations

import argparse
import sys
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cache_store import CacheStore
from claude_cli_rewriter import ClaudeCliRewriter, load_default_template
from config_constants import DEFAULT_SPEED, DEFAULT_VOICE_ID, FALLBACK_CHARS_PER_SEC
from flask import Flask
from http_routing import HttpRoutes, _supported_lang_prefixes
from job_tracker import JobTracker
from native_audio_sink import NativeAudioSink
from resilient_synthesizer import ResilientSynthesizer
from tts_engine import TTSEngine
from tts_executor import get_default_tts_engine
from voice_profile import VoiceProfile
from voice_profile_store import VoiceProfileStore


class TTSExecutor:
    """Helper for web server TTSEngine execution on a single worker thread."""

    def __init__(self, engine: Any | None = None, synth: ResilientSynthesizer | None = None):
        self._engine = engine if engine is not None else get_default_tts_engine()
        self._synth = synth or ResilientSynthesizer(self._engine)
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="tts-worker")

    def ensure_loaded(self) -> None:
        """Pre-warm the model on the worker thread."""
        future = self._executor.submit(self._engine._ensure_loaded)
        future.result()

    def submit(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        return self._executor.submit(fn, *args, **kwargs).result()

    def shutdown(self, wait: bool = True) -> None:
        self._executor.shutdown(wait=wait)

    @property
    def synth(self) -> ResilientSynthesizer:
        return self._synth

    @property
    def engine(self) -> TTSEngine:
        return self._engine


_DEFAULT_HOST = "127.0.0.1"
_DEFAULT_PORT = 7860


def _discover_voices(model_id: str) -> list[str]:
    if model_id.startswith("apple-say"):
        from apple_say_engine import get_available_say_voices

        voices = get_available_say_voices()
        return sorted(voices.keys())

    try:
        from huggingface_hub import snapshot_download

        snap = snapshot_download(model_id, allow_patterns=["voices/*"], local_files_only=True)
    except Exception as exc:  # noqa: BLE001
        print(f"[web] voice discovery failed: {exc}", file=sys.stderr)
        return []
    voices_dir = Path(snap) / "voices"
    if not voices_dir.is_dir():
        return []
    all_ids = sorted(p.stem for p in voices_dir.iterdir() if p.is_file())
    supported = _supported_lang_prefixes()
    usable = [v for v in all_ids if v and v[0] in supported]
    dropped = len(all_ids) - len(usable)
    if dropped:
        print(f"[web] {dropped} voices hidden (language G2P not installed)", file=sys.stderr)
    return usable


def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _config_voice_path() -> Path:
    return _project_root() / "config" / "voice_calibration.json"


def _cache_root() -> Path:
    return _project_root() / "config" / "cache"


def _templates_dir() -> Path:
    return _project_root() / "plugin" / "web" / "templates"


def _fallback_profile() -> VoiceProfile:
    return VoiceProfile(
        voice_id=DEFAULT_VOICE_ID,
        speed=DEFAULT_SPEED,
        chars_per_second=FALLBACK_CHARS_PER_SEC,
        calibrated_at="fallback",
        calibration_source_chars=0,
    )


class WebServer:
    """Composition Root: Flask app + held services for the auto-speech web UI."""

    def __init__(self) -> None:
        self._app = Flask(__name__, template_folder=str(_templates_dir()), static_folder=None)

        # Horizon 0: Dependency Injection
        self._audio_sink = NativeAudioSink()
        self._tts_executor = TTSExecutor()
        self._cache = CacheStore(_cache_root())
        self._lock = threading.Lock()
        self._profile_store = VoiceProfileStore(_config_voice_path())
        self._profile = self._load_profile()
        self._voices = _discover_voices(self._tts_executor.engine.model_id)
        print(f"[web] {len(self._voices)} voices discovered", file=sys.stderr)
        if self._voices and self._profile.voice_id not in self._voices:
            best_v = "Ava (Premium)" if "Ava (Premium)" in self._voices else self._voices[0]
            self._profile = VoiceProfile(
                voice_id=best_v,
                speed=self._profile.speed,
                chars_per_second=self._profile.chars_per_second,
                calibrated_at=self._profile.calibrated_at,
                calibration_source_chars=self._profile.calibration_source_chars,
            )

        self._job_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="speak-job")
        self._jobs = JobTracker()

        try:
            template = load_default_template()
        except FileNotFoundError as exc:
            print(f"[web] WARNING: rewrite prompt missing: {exc}", file=sys.stderr)
            template = "{SOURCE}"
        self._rewriter = ClaudeCliRewriter(template)

        self._routes = HttpRoutes(
            app=self._app,
            audio_sink=self._audio_sink,
            cache=self._cache,
            profile=self._profile,
            voices=self._voices,
            jobs=self._jobs,
            rewriter=self._rewriter,
            tts_executor=self._tts_executor,
            job_executor=self._job_executor,
            lock=self._lock,
        )
        self._routes.register()

        # Prewarm TTS worker
        try:
            self._tts_executor.ensure_loaded()
        except Exception as exc:  # noqa: BLE001
            print(f"[web] WARNING: TTS pre-warm failed: {exc}", file=sys.stderr)

    def run(self, host: str = _DEFAULT_HOST, port: int = _DEFAULT_PORT) -> None:
        if host not in {"127.0.0.1", "localhost", "::1"}:
            raise RuntimeError(f"refusing to bind to {host!r}")
        print(f"[web] listening on http://{host}:{port}/", file=sys.stderr)
        self._app.run(host=host, port=port, threaded=True, use_reloader=False)

    def _load_profile(self) -> VoiceProfile:
        loaded = self._profile_store.load()
        if loaded is not None:
            return loaded
        return _fallback_profile()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="auto-speech localhost web server")
    p.add_argument("--port", type=int, default=_DEFAULT_PORT)
    args = p.parse_args(argv)

    server = WebServer()
    print(f"[web] start={datetime.now(UTC).isoformat(timespec='seconds')}", file=sys.stderr)
    try:
        server.run(host=_DEFAULT_HOST, port=args.port)
    except KeyboardInterrupt:
        print("[web] shutting down", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
