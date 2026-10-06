from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import socket
import sys
import tempfile
import threading
import time
import traceback
import wave
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from audio_transcript import AudioTranscript
from cache_entry import CacheEntry
from cache_store import CacheStore
from chunk_planner import ChunkPlanner
from claude_cli_rewriter import ClaudeCliRewriteError, ClaudeCliRewriter, ClaudeCliUnavailable
from config_constants import BASE_DURATION_SECONDS, BOUNDARY_TOLERANCE, FALLBACK_CHARS_PER_SEC
from flask import Flask, Response, jsonify, render_template, request
from job_state import PHASE_GENERATING, PHASE_HANDED_OFF, PHASE_REWRITING
from job_tracker import JobTracker
from native_audio_sink import NativeAudioSink
from tts_engine import TTSGenerationError, TTSNoSpeakableContentError
from voice_profile import VoiceProfile
from wav_concatenator import WavConcatenator, WavConcatError

_EXTENSION_ORIGIN_RE = re.compile(r"chrome-extension://[a-p]{32}")
_SYNTH_MAX_CHARS = 20000
logger = logging.getLogger(__name__)

def _add_cors_headers(resp):
    origin = request.headers.get("Origin", "")
    if _EXTENSION_ORIGIN_RE.fullmatch(origin):
        resp.headers["Access-Control-Allow-Origin"] = origin
        resp.headers["Vary"] = "Origin"
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return resp

def _text_field(body: dict, name: str, default: str = "") -> str | None:
    raw = body.get(name)
    if raw is None:
        return default
    if not isinstance(raw, str):
        return None
    return raw.strip()

def _synthesize_hash(text: str, voice_id: str, speed: float) -> str:
    key_input = text.encode("utf-8") + b"\x00" + f"{voice_id}:{speed}".encode() + b"\x00synthesize"
    return hashlib.sha256(key_input).hexdigest()

def _wav_duration_seconds(path: Path) -> float:
    with wave.open(str(path), "rb") as wf:
        frames = wf.getnframes()
        rate = wf.getframerate()
    return frames / rate if rate else 0.0

def _job_to_dict(job) -> dict | None:
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

def _send_daemon_socket_request(payload: dict[str, Any], timeout: float = 2.0) -> dict[str, Any] | None:
    socket_path = Path(os.environ.get("AUTO_SPEECH_DAEMON_SOCK", "/tmp/auto-speech-daemon.sock"))
    if not socket_path.is_socket():
        return None
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect(str(socket_path))
        sock.sendall(json.dumps(payload).encode("utf-8") + b"\n")
        resp_data = b""
        while b"\n" not in resp_data:
            chunk = sock.recv(4096)
            if not chunk:
                break
            resp_data += chunk
        sock.close()
        if not resp_data:
            return None
        return json.loads(resp_data.partition(b"\n")[0].decode("utf-8"))
    except Exception:  # noqa: BLE001 — daemon probe failure yields no payload
        return None


class HttpRoutes:
    """Configures Flask routing for the auto-speech web server."""
    
    def __init__(
        self,
        app: Flask,
        audio_sink: NativeAudioSink,
        cache: CacheStore,
        profile: VoiceProfile,
        voices: list[str],
        jobs: JobTracker,
        rewriter: ClaudeCliRewriter,
        tts_executor,
        job_executor,
        lock: threading.Lock
    ):
        self.app = app
        self._audio_sink = audio_sink
        self._cache = cache
        self._profile = profile
        self._voices = voices
        self._jobs = jobs
        self._rewriter = rewriter
        self._tts_executor = tts_executor
        self._job_executor = job_executor
        self._lock = lock

    def _dispatch_playback(self, wav_path: Path, source_hash: str | None = None) -> None:
        if isinstance(self._audio_sink, NativeAudioSink) and source_hash:
            resp = _send_daemon_socket_request({"action": "play_cache", "source_hash": source_hash})
            if resp and resp.get("status") in ("queued", "ok"):
                return
        self._audio_sink.play(wav_path)

    def register(self):
        self.app.add_url_rule("/", view_func=self._index, methods=["GET"])
        self.app.add_url_rule("/api/speak", view_func=self._handle_speak, methods=["POST"])
        self.app.add_url_rule("/api/synthesize", view_func=self._handle_synthesize, methods=["POST", "OPTIONS"])
        self.app.add_url_rule("/api/voices", view_func=self._handle_voices, methods=["GET"])
        self.app.add_url_rule("/api/replay", view_func=self._handle_replay, methods=["POST"])
        self.app.add_url_rule("/api/cache", view_func=self._handle_cache_list, methods=["GET"])
        self.app.add_url_rule("/api/pause", view_func=self._handle_pause, methods=["POST"])
        self.app.add_url_rule("/api/resume", view_func=self._handle_resume, methods=["POST"])
        self.app.add_url_rule("/api/seek", view_func=self._handle_seek, methods=["POST"])
        self.app.add_url_rule("/api/restart", view_func=self._handle_restart, methods=["POST"])
        self.app.add_url_rule("/api/end", view_func=self._handle_end, methods=["POST"])
        self.app.add_url_rule("/api/status", view_func=self._handle_status, methods=["GET"])
        self.app.after_request(_add_cors_headers)

    def _index(self):
        return render_template(
            "index.html",
            voice_id=self._profile.voice_id,
            speed=self._profile.speed,
            chars_per_second=self._profile.chars_per_second,
        )

    def _compute_hash(self, text: str, mode: str = "rewrite") -> str:
        key_input = text.encode("utf-8") + b"\x00" + f"{self._profile.voice_id}:{self._profile.speed}".encode()
        if mode != "rewrite":
            key_input += b"\x00" + mode.encode("utf-8")
        return hashlib.sha256(key_input).hexdigest()

    def _handle_speak(self):
        body = request.get_json(silent=True) or {}
        text = _text_field(body, "text")
        if text is None:
            return jsonify({"error": "text must be a string"}), 400
        rewrite_mode = bool(body.get("rewrite", True))
        if not text:
            return jsonify({"error": "text is required"}), 400

        source_hash = self._compute_hash(text, mode="rewrite" if rewrite_mode else "passthrough")
        try:
            rewrite_timeout = float(body.get("rewrite_timeout_s", 600.0))
        except (TypeError, ValueError):
            return jsonify({"error": "rewrite_timeout_s must be a number"}), 400

        with self._lock:
            hit = self._cache.lookup(source_hash)
            if hit is not None:
                wav_path, entry = hit
                try:
                    threading.Thread(
                        target=self._dispatch_playback,
                        args=(wav_path, source_hash),
                        daemon=True,
                    ).start()
                except Exception as exc:  # noqa: BLE001 — dispatch failure returns HTTP 500
                    return jsonify({"error": str(exc)}), 500
                return jsonify({
                    "status": "cache_hit",
                    "hash": source_hash,
                    "mode": "rewrite" if rewrite_mode else "passthrough",
                    "char_count": entry.char_count,
                    "duration_seconds": entry.duration_seconds,
                })

            if self._jobs.is_active():
                cur = self._jobs.current()
                return jsonify({"error": f"another speak job is in flight ({cur.phase})", "job": _job_to_dict(cur)}), 409

            mode_str = "rewrite" if rewrite_mode else "passthrough"
            self._jobs.begin(mode=mode_str, source_chars=len(text), source_hash=source_hash)

        self._job_executor.submit(self._run_speak_job, text, mode_str, source_hash, rewrite_timeout)
        return jsonify({"status": "queued", "job": _job_to_dict(self._jobs.current())}), 202

    def _run_speak_job(self, text: str, mode: str, source_hash: str, rewrite_timeout: float) -> None:
        try:
            if mode == "rewrite":
                self._jobs.transition(PHASE_REWRITING)
                try:
                    audio_text = self._rewriter.rewrite(text, timeout_seconds=rewrite_timeout)
                except ClaudeCliUnavailable as exc:
                    self._jobs.fail(str(exc))
                    return
                except ClaudeCliRewriteError as exc:
                    self._jobs.fail(f"rewrite failed: {exc}")
                    return
                self._jobs.transition(PHASE_GENERATING, rewrite_chars=len(audio_text))
            else:
                audio_text = text
                self._jobs.transition(PHASE_GENERATING, rewrite_chars=len(text))

            try:
                wav_path = self._tts_executor.submit(self._synthesize_to_cache, audio_text, self._profile, source_hash)
            except Exception as exc:  # noqa: BLE001 — synthesis failure is recorded on the job
                self._jobs.fail(f"pipeline crashed: {exc!r}")
                return

            self._jobs.transition(PHASE_HANDED_OFF)
            try:
                self._dispatch_playback(wav_path, source_hash)
            except Exception as exc:  # noqa: BLE001 — playback failure is logged, not raised
                print(f"[web] playback failed: {exc}", file=sys.stderr)
        except Exception as exc:  # noqa: BLE001 — pipeline crash is recorded on the job
            try:
                self._jobs.fail(f"crash: {exc!r}")
            except Exception as exc2:  # noqa: BLE001 — failure recorder must not raise
                print(f"[web] could not record job failure: {exc2!r}", file=sys.stderr)

    def _handle_voices(self):
        return jsonify({"voices": self._voices, "default": self._profile.voice_id})

    def _handle_synthesize(self):
        if request.method == "OPTIONS":
            return ("", 204)

        body = request.get_json(silent=True) or {}
        text = _text_field(body, "text")
        if text is None:
            return jsonify({"error": "text must be a string"}), 400
        if not text:
            return jsonify({"error": "text is required"}), 400
        if len(text) > _SYNTH_MAX_CHARS:
            return jsonify({"error": f"text exceeds {_SYNTH_MAX_CHARS} chars"}), 413

        voice_id = _text_field(body, "voice", self._profile.voice_id)
        if voice_id is None:
            return jsonify({"error": "voice must be a string"}), 400
        voice_id = voice_id or self._profile.voice_id
        if self._voices and voice_id not in self._voices:
            return jsonify({"error": f"unknown voice {voice_id!r}", "voices": self._voices}), 400
        try:
            speed = float(body.get("speed", self._profile.speed))
        except (TypeError, ValueError):
            return jsonify({"error": "speed must be a number"}), 400
        speed = max(0.5, min(2.0, speed))

        profile = VoiceProfile(
            voice_id=voice_id,
            speed=speed,
            chars_per_second=self._profile.chars_per_second,
            calibrated_at=self._profile.calibrated_at,
            calibration_source_chars=self._profile.calibration_source_chars,
        )
        source_hash = _synthesize_hash(text, voice_id, speed)

        try:
            wav_path = self._tts_executor.submit(self._synthesize_to_cache, text, profile, source_hash)
        except TTSNoSpeakableContentError as exc:
            return jsonify({"error": "no speakable text", "reason": str(exc)}), 422
        except TTSGenerationError as exc:
            return jsonify({"error": f"synthesis failed: {exc}"}), 500
        except Exception as exc:  # noqa: BLE001 — unexpected synthesis errors return HTTP 500
            traceback.print_exc(file=sys.stderr)
            return jsonify({"error": f"synthesis crashed: {exc!r}"}), 500

        try:
            data = wav_path.read_bytes()
        except OSError as exc:
            return jsonify({"error": f"could not read wav: {exc}"}), 500
        return Response(data, mimetype="audio/wav", headers={"X-Auto-Speech-Hash": source_hash})

    def _synthesize_to_cache(self, text: str, profile: VoiceProfile, source_hash: str) -> Path:
        hit = self._cache.lookup(source_hash)
        if hit is not None:
            return hit[0]

        with tempfile.TemporaryDirectory(prefix="autospeech-synth-") as tmpdir:
            tmp_wav = self._render_full_wav(text, profile, Path(tmpdir))
            duration = _wav_duration_seconds(tmp_wav)
            cps = (len(text) / duration) if duration > 0 else FALLBACK_CHARS_PER_SEC
            entry = CacheEntry(
                source_hash=source_hash,
                voice_id=profile.voice_id,
                speed=profile.speed,
                char_count=len(text),
                duration_seconds=duration,
                created_at=datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
                chars_per_second_at_creation=cps,
            )
            return self._cache.promote(source_hash, tmp_wav, entry)

    def _render_full_wav(self, text: str, profile: VoiceProfile, tmpdir: Path) -> Path:
        tmpdir.mkdir(parents=True, exist_ok=True)
        full_wav = tmpdir / "full.wav"
        transcript = AudioTranscript(text=text)

        if False:
            spans = [(tmpdir / "span-000.wav", text)]
        else:
            plan = ChunkPlanner().plan(
                transcript,
                profile,
                base_duration_seconds=BASE_DURATION_SECONDS,
                tolerance=BOUNDARY_TOLERANCE,
            )
            spans = [(tmpdir / f"chunk-{d.index:03d}.wav", d.text) for d in plan]

        part_wavs: list[Path] = []
        for out_path, span_text in spans:
            part_wavs.extend(self._tts_executor.synth.synthesize_parts(span_text, profile, out_path))

        if not part_wavs:
            raise TTSNoSpeakableContentError("no speakable content in selection")
        if len(part_wavs) == 1 and part_wavs[0] != full_wav:
            part_wavs[0].replace(full_wav)
            return full_wav
        try:
            WavConcatenator.concat(part_wavs, full_wav)
        except WavConcatError as exc:
            raise TTSGenerationError(f"chunk concat failed: {exc}") from exc
        return full_wav

    def _handle_replay(self):
        body = request.get_json(silent=True) or {}
        h = _text_field(body, "hash")
        if h is None:
            return jsonify({"error": "hash must be a string"}), 400
        h = h.lower()
        if not h:
            return jsonify({"error": "hash is required"}), 400

        with self._lock:
            wav_path, source_hash = self._resolve_cached_wav_and_hash(h)
            if wav_path is None:
                return jsonify({"error": f"no cache entry matching {h!r}"}), 404
            try:
                threading.Thread(
                    target=self._dispatch_playback,
                    args=(wav_path, source_hash),
                    daemon=True,
                ).start()
            except Exception as exc:  # noqa: BLE001 — cached playback dispatch returns HTTP 500
                return jsonify({"error": str(exc)}), 500
            return jsonify({"status": "started", "wav": str(wav_path)})

    def _resolve_cached_wav_and_hash(self, h: str) -> tuple[Path | None, str | None]:
        if len(h) == 64 and all(c in "0123456789abcdef" for c in h):
            hit = self._cache.lookup(h)
            if hit is not None:
                return hit[0], h
            return None, None
        for wav_path, entry in self._cache.list_by_recency():
            if entry.source_hash.startswith(h):
                return wav_path, entry.source_hash
        return None, None

    def _resolve_cached_wav(self, h: str) -> Path | None:
        wav, _ = self._resolve_cached_wav_and_hash(h)
        return wav

    def _handle_cache_list(self):
        items = []
        for wav_path, entry in self._cache.list_by_recency():
            items.append({
                "hash": entry.source_hash,
                "voice_id": entry.voice_id,
                "speed": entry.speed,
                "char_count": entry.char_count,
                "duration_seconds": entry.duration_seconds,
                "created_at": entry.created_at,
                "wav": str(wav_path),
            })
        return jsonify({"entries": items})

    def _handle_pause(self):
        return jsonify({"error": "not supported by native audio sink"}), 501

    def _handle_resume(self):
        return jsonify({"error": "not supported by native audio sink"}), 501

    def _handle_restart(self):
        return jsonify({"error": "not supported by native audio sink"}), 501

    def _handle_end(self):
        if isinstance(self._audio_sink, NativeAudioSink):
            _send_daemon_socket_request({"action": "interrupt"})
        self._audio_sink.interrupt()
        return jsonify({"status": "ended"})

    def _handle_seek(self):
        body = request.get_json(silent=True) or {}
        target = _text_field(body, "target")
        if target is None:
            return jsonify({"error": "target must be a string"}), 400
        if not target:
            return jsonify({"error": "target is required"}), 400
        return jsonify({"error": "not supported by native audio sink"}), 501

    def _handle_status(self):
        active = self._audio_sink.is_playing
        if isinstance(self._audio_sink, NativeAudioSink):
            daemon_status = _send_daemon_socket_request({"action": "status"})
            if daemon_status and daemon_status.get("status") == "ok":
                active = active or (daemon_status.get("daemon_state") in ("PLAYING", "SYNTHESIZING"))
        payload = {
            "active": active,
            "paused": False,
            "position": 0.0,
            "duration": 0.0,
            "wav": "",
        }
        payload["job"] = _job_to_dict(self._jobs.current())
        return jsonify(payload)

def _supported_lang_prefixes() -> set[str]:
    supported = set("abefhip")
    for prefix, module in (("j", "misaki.ja"), ("z", "misaki.zh")):
        try:
            __import__(module)
            supported.add(prefix)
        except Exception:  # optional G2P extra may fail to import
            logger.debug("optional language module %s unavailable", module, exc_info=True)
    return supported
