"""WebServer: localhost Flask app exposing the auto-speech pipeline + controls.

Single-class module. Holds one TTSEngine for the life of the process so
requests after the first skip the model load.

MLX detail: MLX state (compute streams) is per-thread. The TTSEngine must
be loaded AND used from the same thread. Flask serves requests on a pool
of worker threads, so we route all TTS-touching work through a dedicated
single-worker ThreadPoolExecutor. The executor's one thread loads the
model on its first task and runs every subsequent generate() on that
same thread. The lock is then redundant for serialization but kept as
a defensive guard against double-submitting before a result is awaited.

Run with:
  source .venv/bin/activate
  python plugin/scripts/python/web_server.py [--port 7860]
"""
from __future__ import annotations

import argparse
import hashlib
import sys
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, jsonify, render_template, request

from cache_store import CacheStore
from claude_cli_rewriter import (
    ClaudeCliRewriteError,
    ClaudeCliRewriter,
    ClaudeCliUnavailable,
    load_default_template,
)
from config_constants import DEFAULT_SPEED, DEFAULT_VOICE_ID, FALLBACK_CHARS_PER_SEC
from job_state import (
    PHASE_GENERATING,
    PHASE_HANDED_OFF,
    PHASE_REWRITING,
)
from job_tracker import JobTracker
from mpv_controller import MpvController, MpvNotInstalledError, MpvStartupError
from mpv_ipc import MpvIpc, MpvIpcError
from pipeline import (
    EXIT_OK,
    PipelineOrchestrator,
)
from session_dir import SessionDir
from tts_engine import TTSEngine
from voice_profile import VoiceProfile
from voice_profile_store import VoiceProfileStore


_DEFAULT_HOST = "127.0.0.1"
_DEFAULT_PORT = 7860


def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _config_voice_path() -> Path:
    return _project_root() / "config" / "voice_calibration.json"


def _cache_root() -> Path:
    return _project_root() / "config" / "cache"


def _templates_dir() -> Path:
    return _project_root() / "plugin" / "web" / "templates"


def _job_to_dict(job) -> dict | None:
    """Render a Job as the JSON shape /api/status emits, or None."""
    if job is None:
        return None
    elapsed = max(0.0, time.time() - job.started_at)
    return {
        "id": job.id,
        "phase": job.phase,
        "started_at": job.started_at,
        "elapsed_s": elapsed,
        "mode": job.mode,
        "source_chars": job.source_chars,
        "rewrite_chars": job.rewrite_chars,
        "hash": job.hash,
        "error": job.error,
    }


def _fallback_profile() -> VoiceProfile:
    return VoiceProfile(
        voice_id=DEFAULT_VOICE_ID,
        speed=DEFAULT_SPEED,
        chars_per_second=FALLBACK_CHARS_PER_SEC,
        calibrated_at="fallback",
        calibration_source_chars=0,
    )


class WebServer:
    """Flask app + held services for the auto-speech web UI."""

    def __init__(self) -> None:
        self._app = Flask(
            __name__,
            template_folder=str(_templates_dir()),
            static_folder=None,
        )
        self._tts = TTSEngine()
        self._cache = CacheStore(_cache_root())
        self._mpv = MpvController()
        self._lock = threading.Lock()
        self._profile_store = VoiceProfileStore(_config_voice_path())
        self._profile = self._load_profile()

        # Single worker thread that owns the TTSEngine's MLX state for the
        # life of the server. All TTS-touching work goes through it.
        self._tts_executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="tts-worker"
        )

        # Phase 17: at-most-one fire-and-forget speak job. The HTTP layer
        # consults the tracker to decide 202 (queued) vs 409 (busy).
        self._jobs = JobTracker()

        # Phase 14: shared 12-rule rewrite prompt + CLI-backed rewriter.
        try:
            template = load_default_template()
        except FileNotFoundError as exc:
            print(f"[web] WARNING: rewrite prompt missing: {exc}", file=sys.stderr)
            template = "{SOURCE}"
        self._rewriter = ClaudeCliRewriter(template)
        if self._rewriter.is_available():
            print("[web] rewriter: claude CLI on PATH", file=sys.stderr)
        else:
            print(
                "[web] WARNING: `claude` not on PATH. Rewrite-on requests will fail; "
                "rewrite-off (pass-through) still works.",
                file=sys.stderr,
            )

        self._register_routes()
        self._prewarm_tts()

    # ----- lifecycle -----

    def run(self, host: str = _DEFAULT_HOST, port: int = _DEFAULT_PORT) -> None:
        # Invariant I-13.1: localhost binding only.
        if host not in {"127.0.0.1", "localhost", "::1"}:
            raise RuntimeError(
                f"refusing to bind to {host!r}; this server is localhost-only "
                f"(see ADR-010). Edit web_server.py if you really need to."
            )
        print(
            f"[web] listening on http://{host}:{port}/  "
            f"(voice={self._profile.voice_id} speed={self._profile.speed} "
            f"cps={self._profile.chars_per_second:.2f})",
            file=sys.stderr,
        )
        self._app.run(host=host, port=port, threaded=True, use_reloader=False)

    def _load_profile(self) -> VoiceProfile:
        loaded = self._profile_store.load()
        if loaded is not None:
            return loaded
        print("[web] no calibration; using fallback profile", file=sys.stderr)
        return _fallback_profile()

    def _prewarm_tts(self) -> None:
        # Run the load on the worker thread so MLX state binds to it.
        future = self._tts_executor.submit(self._tts._ensure_loaded)  # noqa: SLF001
        try:
            future.result()
        except Exception as exc:  # noqa: BLE001 — broad on purpose
            print(
                f"[web] WARNING: TTS pre-warm failed: {exc}. /api/speak will fail later.",
                file=sys.stderr,
            )

    # ----- route registration -----

    def _register_routes(self) -> None:
        self._app.add_url_rule("/", view_func=self._index, methods=["GET"])
        self._app.add_url_rule(
            "/api/speak", view_func=self._handle_speak, methods=["POST"]
        )
        self._app.add_url_rule(
            "/api/replay", view_func=self._handle_replay, methods=["POST"]
        )
        self._app.add_url_rule(
            "/api/cache", view_func=self._handle_cache_list, methods=["GET"]
        )
        self._app.add_url_rule(
            "/api/pause", view_func=self._handle_pause, methods=["POST"]
        )
        self._app.add_url_rule(
            "/api/resume", view_func=self._handle_resume, methods=["POST"]
        )
        self._app.add_url_rule(
            "/api/seek", view_func=self._handle_seek, methods=["POST"]
        )
        self._app.add_url_rule(
            "/api/restart", view_func=self._handle_restart, methods=["POST"]
        )
        self._app.add_url_rule(
            "/api/end", view_func=self._handle_end, methods=["POST"]
        )
        self._app.add_url_rule(
            "/api/status", view_func=self._handle_status, methods=["GET"]
        )

    # ----- pages -----

    def _index(self):
        return render_template(
            "index.html",
            voice_id=self._profile.voice_id,
            speed=self._profile.speed,
            chars_per_second=self._profile.chars_per_second,
        )

    # ----- API: speak / replay -----

    def _handle_speak(self):
        body = request.get_json(silent=True) or {}
        text = (body.get("text") or "").strip()
        rewrite_mode = bool(body.get("rewrite", True))  # Phase 14: default ON
        if not text:
            return jsonify({"error": "text is required"}), 400

        # Phase 14: distinct cache key per pipeline mode so rewrite-on and
        # passthrough don't alias for the same source text.
        source_hash = self._compute_hash(
            text, mode="rewrite" if rewrite_mode else "passthrough"
        )
        # Default 10 min for the rewriter; large pastes legitimately need it.
        rewrite_timeout = float(body.get("rewrite_timeout_s", 600.0))

        with self._lock:
            # Cache hits stay synchronous — they finish in ~50 ms and don't
            # need the fire-and-forget round-trip.
            hit = self._cache.lookup(source_hash)
            if hit is not None:
                wav_path, entry = hit
                try:
                    self._mpv.start(wav_path)
                except (MpvNotInstalledError, MpvStartupError) as exc:
                    return jsonify({"error": str(exc)}), 500
                return jsonify(
                    {
                        "status": "cache_hit",
                        "hash": source_hash,
                        "mode": "rewrite" if rewrite_mode else "passthrough",
                        "char_count": entry.char_count,
                        "duration_seconds": entry.duration_seconds,
                    }
                )

            # Phase 17: at-most-one in-flight job. If one is still active,
            # tell the caller to wait — don't kill in-progress work.
            if self._jobs.is_active():
                cur = self._jobs.current()
                return (
                    jsonify(
                        {
                            "error": f"another speak job is in flight ({cur.phase})",
                            "job": _job_to_dict(cur),
                        }
                    ),
                    409,
                )

            # Miss + nothing in flight → register a new job and submit it.
            mode_str = "rewrite" if rewrite_mode else "passthrough"
            self._jobs.begin(
                mode=mode_str,
                source_chars=len(text),
                source_hash=source_hash,
            )

        # Submit OUTSIDE the lock — the executor's worker is the real
        # serializer. The lock just protected the begin/check critical region.
        self._tts_executor.submit(
            self._run_speak_job, text, mode_str, source_hash, rewrite_timeout
        )

        return (
            jsonify(
                {
                    "status": "queued",
                    "job": _job_to_dict(self._jobs.current()),
                }
            ),
            202,
        )

    def _run_speak_job(
        self,
        text: str,
        mode: str,
        source_hash: str,
        rewrite_timeout: float,
    ) -> None:
        """Background runner: rewrite (if needed) → TTS → mpv handoff.

        Drives the JobTracker through phase transitions. Any exception
        becomes a `failed` phase with the error string captured.
        """
        try:
            if mode == "rewrite":
                self._jobs.transition(PHASE_REWRITING)
                print(
                    f"[web] job rewriting  src_chars={len(text)} "
                    f"timeout={rewrite_timeout:.0f}s",
                    file=sys.stderr,
                )
                try:
                    audio_text = self._rewriter.rewrite(
                        text, timeout_seconds=rewrite_timeout
                    )
                except ClaudeCliUnavailable as exc:
                    print(f"[web] job FAIL (rewriter unavailable): {exc}", file=sys.stderr)
                    self._jobs.fail(str(exc))
                    return
                except ClaudeCliRewriteError as exc:
                    print(f"[web] job FAIL (rewrite): {exc}", file=sys.stderr)
                    self._jobs.fail(f"rewrite failed: {exc}")
                    return
                print(
                    f"[web] job rewrite ok src={len(text)} → out={len(audio_text)} chars",
                    file=sys.stderr,
                )
                self._jobs.transition(
                    PHASE_GENERATING, rewrite_chars=len(audio_text)
                )
            else:
                audio_text = text
                self._jobs.transition(
                    PHASE_GENERATING, rewrite_chars=len(text)
                )

            orchestrator = PipelineOrchestrator(
                source_hash=source_hash,
                cache_root=_cache_root(),
                tts_engine=self._tts,
            )
            try:
                rc = orchestrator.run(audio_text)
            except Exception as exc:  # noqa: BLE001
                print(f"[web] job CRASH (pipeline): {exc!r}", file=sys.stderr)
                traceback.print_exc(file=sys.stderr)
                self._jobs.fail(f"pipeline crashed: {exc!r}")
                return

            if rc != EXIT_OK:
                print(f"[web] job FAIL pipeline exit={rc}", file=sys.stderr)
                self._jobs.fail(f"pipeline exited with code {rc}")
                return

            self._jobs.transition(PHASE_HANDED_OFF)
            print("[web] job handed_off (mpv playing)", file=sys.stderr)
        except Exception as exc:  # noqa: BLE001 — guard the worker thread
            print(f"[web] job CRASH (outer): {exc!r}", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)
            try:
                self._jobs.fail(f"crash: {exc!r}")
            except Exception as exc2:
                # The crash is already logged above; this only guards the
                # state update from masking it. Still surface the secondary
                # failure rather than swallowing it entirely.
                print(f"[web] could not record job failure: {exc2!r}", file=sys.stderr)

    def _handle_replay(self):
        body = request.get_json(silent=True) or {}
        h = (body.get("hash") or "").strip().lower()
        if not h:
            return jsonify({"error": "hash is required"}), 400

        with self._lock:
            wav_path = self._resolve_cached_wav(h)
            if wav_path is None:
                return jsonify({"error": f"no cache entry matching {h!r}"}), 404
            try:
                self._mpv.start(wav_path)
            except (MpvNotInstalledError, MpvStartupError) as exc:
                return jsonify({"error": str(exc)}), 500
            return jsonify({"status": "started", "wav": str(wav_path)})

    def _resolve_cached_wav(self, h: str) -> Path | None:
        # Full 64-hex-char hash → use lookup directly.
        if len(h) == 64 and all(c in "0123456789abcdef" for c in h):
            hit = self._cache.lookup(h)
            return hit[0] if hit is not None else None
        # Otherwise treat it as a prefix match against existing dir names.
        for wav_path, entry in self._cache.list_by_recency():
            if entry.source_hash.startswith(h):
                return wav_path
        return None

    def _handle_cache_list(self):
        items = []
        for wav_path, entry in self._cache.list_by_recency():
            items.append(
                {
                    "hash": entry.source_hash,
                    "voice_id": entry.voice_id,
                    "speed": entry.speed,
                    "char_count": entry.char_count,
                    "duration_seconds": entry.duration_seconds,
                    "created_at": entry.created_at,
                    "wav": str(wav_path),
                }
            )
        return jsonify({"entries": items})

    # ----- API: control -----

    def _handle_pause(self):
        return self._send_mpv(["set_property", "pause", True])

    def _handle_resume(self):
        return self._send_mpv(["set_property", "pause", False])

    def _handle_restart(self):
        return self._send_mpv(["seek", 0, "absolute"])

    def _handle_end(self):
        if not SessionDir.is_mpv_running():
            return jsonify({"error": "no active session"}), 404
        try:
            MpvIpc.send(["quit"], SessionDir.socket_path())
        except MpvIpcError as exc:
            return jsonify({"error": str(exc)}), 502
        SessionDir.clear()
        return jsonify({"status": "ended"})

    def _handle_seek(self):
        body = request.get_json(silent=True) or {}
        target = (body.get("target") or "").strip()
        if not target:
            return jsonify({"error": "target is required"}), 400
        if not SessionDir.is_mpv_running():
            return jsonify({"error": "no active session"}), 404

        if target.lower() == "end":
            try:
                reply = MpvIpc.send(
                    ["get_property", "duration"], SessionDir.socket_path()
                )
            except MpvIpcError as exc:
                return jsonify({"error": str(exc)}), 502
            duration = reply.get("data")
            if not isinstance(duration, (int, float)):
                return jsonify({"error": "mpv did not report duration"}), 502
            return self._send_mpv(
                ["seek", max(0.0, float(duration) - 0.5), "absolute"]
            )

        if target.startswith("+") or target.startswith("-"):
            try:
                offset = float(target)
            except ValueError:
                return jsonify({"error": f"bad relative target {target!r}"}), 400
            return self._send_mpv(["seek", offset, "relative"])

        try:
            absolute = float(target)
        except ValueError:
            return jsonify({"error": f"bad absolute target {target!r}"}), 400
        return self._send_mpv(["seek", absolute, "absolute"])

    def _handle_status(self):
        # Build the playback half first.
        if not SessionDir.is_mpv_running():
            payload = {"active": False}
        else:
            sock = SessionDir.socket_path()
            try:
                time_pos = MpvIpc.send(["get_property", "time-pos"], sock).get("data")
                duration = MpvIpc.send(["get_property", "duration"], sock).get("data")
                paused = MpvIpc.send(["get_property", "pause"], sock).get("data")
            except MpvIpcError:
                payload = {"active": False}
            else:
                wav_path = SessionDir.wav_path_path()
                wav = (
                    wav_path.read_text(encoding="utf-8").strip()
                    if wav_path.is_file()
                    else ""
                )
                payload = {
                    "active": True,
                    "paused": bool(paused) if paused is not None else False,
                    "position": float(time_pos)
                    if isinstance(time_pos, (int, float))
                    else 0.0,
                    "duration": float(duration)
                    if isinstance(duration, (int, float))
                    else 0.0,
                    "wav": wav,
                }
        # Phase 17: also report the current/last fire-and-forget job.
        payload["job"] = _job_to_dict(self._jobs.current())
        return jsonify(payload)

    # ----- helpers -----

    def _send_mpv(self, command: list):
        if not SessionDir.is_mpv_running():
            return jsonify({"error": "no active session"}), 404
        try:
            reply = MpvIpc.send(command, SessionDir.socket_path())
        except MpvIpcError as exc:
            return jsonify({"error": str(exc)}), 502
        err = reply.get("error")
        if err and err != "success":
            return jsonify({"error": err}), 502
        return jsonify({"status": "ok"})

    def _compute_hash(self, text: str, mode: str = "rewrite") -> str:
        """Hash key for the cache.

        Mode "rewrite" preserves the existing key shape (sha256 over
        source||0x00||voice:speed) so cache entries created by the slash
        command and by web rewrite-on share keys.

        Mode "passthrough" appends 0x00||"passthrough" so its entries
        live in a distinct key space and never alias rewrite entries
        for the same source.
        """
        key_input = (
            text.encode("utf-8")
            + b"\x00"
            + f"{self._profile.voice_id}:{self._profile.speed}".encode("utf-8")
        )
        if mode != "rewrite":
            key_input += b"\x00" + mode.encode("utf-8")
        return hashlib.sha256(key_input).hexdigest()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="auto-speech localhost web server")
    p.add_argument("--port", type=int, default=_DEFAULT_PORT)
    args = p.parse_args(argv)

    server = WebServer()
    print(f"[web] start={datetime.now(timezone.utc).isoformat(timespec='seconds')}",
          file=sys.stderr)
    try:
        server.run(host=_DEFAULT_HOST, port=args.port)
    except KeyboardInterrupt:
        print("[web] shutting down (mpv keeps playing if active)", file=sys.stderr)
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
