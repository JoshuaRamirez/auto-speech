"""WebServer: localhost Flask app exposing the auto-speech pipeline + controls.

Run with:
  source .venv/bin/activate
  python plugin/scripts/python/web_server.py [--port 7860]
"""

from __future__ import annotations
import argparse
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask

from cache_store import CacheStore
from claude_cli_rewriter import ClaudeCliRewriter, load_default_template
from config_constants import DEFAULT_SPEED, DEFAULT_VOICE_ID, FALLBACK_CHARS_PER_SEC
from job_tracker import JobTracker
from native_audio_sink import NativeAudioSink
from voice_profile import VoiceProfile
from voice_profile_store import VoiceProfileStore
from tts_executor import TTSExecutor
from http_routing import HttpRoutes, _supported_lang_prefixes

_DEFAULT_HOST = "127.0.0.1"
_DEFAULT_PORT = 7860

def _discover_voices(model_id: str) -> list[str]:
    try:
        from huggingface_hub import snapshot_download
        snap = snapshot_download(model_id, allow_patterns=["voices/*"], local_files_only=True)
    except Exception as exc:
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
            lock=self._lock
        )
        self._routes.register()
        
        # Prewarm TTS worker
        try:
            self._tts_executor.ensure_loaded()
        except Exception as exc:
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
    print(f"[web] start={datetime.now(timezone.utc).isoformat(timespec='seconds')}", file=sys.stderr)
    try:
        server.run(host=_DEFAULT_HOST, port=args.port)
    except KeyboardInterrupt:
        print("[web] shutting down", file=sys.stderr)
    return 0

if __name__ == "__main__":
    sys.exit(main())
