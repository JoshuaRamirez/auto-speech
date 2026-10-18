"""Unified CLI dispatcher for auto-speech: speech synthesis, playback transport, session management, and diagnostics."""

from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import time
from pathlib import Path

from state_paths import state_path
from typing import Any

from autoplay_config import load_config as load_autoplay_config
from autoplay_enrollment import SessionEnrollment, format_status as format_enrollment_status
from autoplay_gate import AutoplayGate
from autoplay_scope import SoloScope, format_status as format_scope_status
from cache_store import CacheStore
from daemon_client import DaemonClient, Priority
from doctor import Doctor
from mpv_ipc import MpvIpc, MpvIpcError
from native_audio_sink import AudioSinkError, NativeAudioSink
from narrator_config import load_config as load_narrator_config
from self_update import needs_sync
from sound_yield import run_action as run_sound_yield_action
from speak import send_speech_request

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_NO_SESSION = 2
EXIT_INVALID_INPUT = 2
EXIT_NO_CACHE_ENTRY = 2
EXIT_IPC_FAIL = 3
EXIT_BAD_ARG = 4
EXIT_PLAYBACK_FAIL = 6
EXIT_INTERRUPTED = 130

MPV_SOCK_PATH = state_path("auto-speech-mpv.sock")


def _project_root() -> Path:
    # plugin/scripts/python/cli.py -> project root is parents[3]
    return Path(__file__).resolve().parents[3]


def _default_cache_root() -> Path:
    return _project_root() / "config" / "cache"


def _get_daemon_socket_path() -> Path:
    return Path(os.environ.get("AUTO_SPEECH_DAEMON_SOCK", str(state_path("auto-speech-daemon.sock"))))


def _is_mpv_socket_alive() -> bool:
    try:
        return MPV_SOCK_PATH.is_socket()
    except OSError:
        return False


def _has_active_session() -> bool:
    if _is_mpv_socket_alive():
        return True
    mpv_pid_file = state_path("auto-speech-mpv.pid")
    if mpv_pid_file.is_file():
        try:
            pid = int(mpv_pid_file.read_text().strip())
            os.kill(pid, 0)
            return True
        except (ValueError, OSError):
            pass
    return False


def _send_mpv_command(cmd: list | dict) -> tuple[int, str]:
    if not _has_active_session() or not _is_mpv_socket_alive():
        return (
            EXIT_NO_SESSION,
            "control: no active playback session (unified daemon uses synchronous audio).",
        )
    try:
        reply = MpvIpc.send(cmd, MPV_SOCK_PATH)
    except MpvIpcError as exc:
        return EXIT_IPC_FAIL, f"control: IPC failure: {exc}"
    if reply.get("error") != "success":
        return EXIT_IPC_FAIL, f"control: mpv command returned error: {reply.get('error')}"
    return EXIT_OK, ""


def handle_speak(
    args: argparse.Namespace,
    client: Any | None = None,
    sink: Any | None = None,
) -> int:
    gate = AutoplayGate()
    if gate.worker_gated_off():
        print(
            "auto-speech: globally muted (~/.claude/auto-speech.disabled present); speech suppressed.",
            file=sys.stderr,
        )
        return EXIT_OK

    if args.source_hash is not None:
        sh = args.source_hash.strip().lower()
        if len(sh) != 64 or any(c not in "0123456789abcdef" for c in sh):
            print(
                f"speak: --source-hash must be 64 hex chars, got {args.source_hash!r}",
                file=sys.stderr,
            )
            return EXIT_INVALID_INPUT
        args.source_hash = sh

    is_tty = False
    if sys.stdin is not None:
        try:
            is_tty = bool(sys.stdin.isatty())
        except (AttributeError, ValueError, OSError):
            is_tty = False

    if args.words:
        text = " ".join(args.words)
    elif is_tty:
        if args.source_hash:
            text = ""
        else:
            print("speak: no text provided on command line or stdin", file=sys.stderr)
            return EXIT_INVALID_INPUT
    else:
        text = sys.stdin.read() if sys.stdin else ""

    if not text.strip():
        if args.source_hash:
            cache_root = _default_cache_root()
            cache_wav = cache_root / args.source_hash[:16] / "full.wav"
            if sink is not None and cache_wav.is_file():
                try:
                    sink.play(cache_wav)
                    return EXIT_OK
                except Exception as exc:
                    print(f"speak: cached playback failed: {exc}", file=sys.stderr)
                    return EXIT_FAILURE

            cache_kwargs: dict[str, Any] = {}
            if args.session_id is not None:
                cache_kwargs["session_id"] = args.session_id
            if args.priority != int(Priority.EXPLICIT_MCP):
                cache_kwargs["priority"] = args.priority

            c = client or DaemonClient(socket_path=args.socket_path)
            try:
                c.ensure_daemon_running()
                resp = c.play_cache(args.source_hash, **cache_kwargs)
                if resp.status in ("ok", "queued"):
                    return EXIT_OK
                return EXIT_FAILURE
            except Exception as exc:
                if cache_wav.is_file():
                    try:
                        NativeAudioSink().play(cache_wav)
                        return EXIT_OK
                    except Exception:
                        pass
                print(f"speak: cached playback failed: {exc}", file=sys.stderr)
                return EXIT_FAILURE
        else:
            print("speak: no text provided on command line or stdin", file=sys.stderr)
            return EXIT_INVALID_INPUT

    c = client or DaemonClient(socket_path=args.socket_path)
    try:
        c.ensure_daemon_running()
        resp = c.speak(
            text,
            priority=Priority(args.priority),
            source_hash=args.source_hash,
            session_id=args.session_id,
            voice_id=args.voice if args.voice != "system" else "system",
            speed=args.speed,
        )
        if resp.status in ("ok", "queued"):
            return EXIT_OK
        return EXIT_FAILURE
    except Exception as exc:
        kwargs: dict[str, Any] = {}
        if args.socket_path is not None:
            kwargs["socket_path"] = args.socket_path
        if args.source_hash is not None:
            kwargs["source_hash"] = args.source_hash
        if args.session_id is not None:
            kwargs["session_id"] = args.session_id
        if args.priority != int(Priority.EXPLICIT_MCP):
            kwargs["priority"] = args.priority
        rc = send_speech_request(text, **kwargs)
        if rc != EXIT_OK:
            print(f"speak: speech request failed: {exc}", file=sys.stderr)
        return rc


def handle_replay(
    args: argparse.Namespace,
    client: Any | None = None,
    sink: Any | None = None,
) -> int:
    if args.ordinal < 1:
        print(f"replay: --ordinal must be >= 1, got {args.ordinal}", file=sys.stderr)
        return EXIT_INVALID_INPUT

    store = CacheStore(_default_cache_root())
    entries = store.list_by_recency()
    if not entries:
        print(
            "replay: no cache entries found. Run /speak at least once first.",
            file=sys.stderr,
        )
        return EXIT_NO_CACHE_ENTRY
    if args.ordinal > len(entries):
        print(
            f"replay: only {len(entries)} cache entries; you asked for #{args.ordinal}",
            file=sys.stderr,
        )
        return EXIT_NO_CACHE_ENTRY

    wav_path, entry = entries[args.ordinal - 1]
    print(
        f"[replay] ordinal={args.ordinal}  voice={entry.voice_id}  "
        f"dur={entry.duration_seconds:.1f}s  created={entry.created_at}  "
        f"path={wav_path}",
        file=sys.stderr,
    )

    gate = AutoplayGate()
    if gate.worker_gated_off():
        print(
            "auto-speech: globally muted (~/.claude/auto-speech.disabled present); replay suppressed.",
            file=sys.stderr,
        )
        return EXIT_OK

    if sink is not None:
        try:
            sink.play(wav_path)
            return EXIT_OK
        except AudioSinkError as exc:
            print(f"replay: playback failed: {exc}", file=sys.stderr)
            return EXIT_PLAYBACK_FAIL
        except KeyboardInterrupt:
            sink.interrupt()
            return EXIT_INTERRUPTED

    if client is not None:
        try:
            resp = client.play_cache(entry.source_hash)
            if resp.status in ("ok", "queued"):
                return EXIT_OK
            return EXIT_FAILURE
        except KeyboardInterrupt:
            return EXIT_INTERRUPTED
        except Exception as exc:
            print(f"replay: playback failed: {exc}", file=sys.stderr)
            return EXIT_PLAYBACK_FAIL

    daemon_sock = _get_daemon_socket_path()
    if daemon_sock.is_socket():
        try:
            c = DaemonClient(socket_path=daemon_sock)
            resp = c.play_cache(entry.source_hash)
            if resp.status in ("ok", "queued"):
                return EXIT_OK
        except Exception:
            pass

    try:
        s = NativeAudioSink()
        s.play(wav_path)
        return EXIT_OK
    except AudioSinkError as exc:
        print(f"replay: playback failed: {exc}", file=sys.stderr)
        return EXIT_PLAYBACK_FAIL
    except KeyboardInterrupt:
        return EXIT_INTERRUPTED


def handle_pause(_args: argparse.Namespace) -> int:
    rc, msg = _send_mpv_command(["set_property", "pause", True])
    if rc != EXIT_OK:
        print(msg, file=sys.stderr)
        return rc
    print("paused")
    return EXIT_OK


def handle_resume(_args: argparse.Namespace) -> int:
    rc, msg = _send_mpv_command(["set_property", "pause", False])
    if rc != EXIT_OK:
        print(msg, file=sys.stderr)
        return rc
    print("resumed")
    return EXIT_OK


def handle_restart(_args: argparse.Namespace) -> int:
    rc, msg = _send_mpv_command(["seek", 0, "absolute"])
    if rc != EXIT_OK:
        print(msg, file=sys.stderr)
        return rc
    print("restarted")
    return EXIT_OK


def handle_seek(args: argparse.Namespace) -> int:
    target = getattr(args, "target", "")
    target = target.strip() if isinstance(target, str) else ""
    if not target:
        print("control: seek requires a target (+N, -N, N, or 'end')", file=sys.stderr)
        return EXIT_BAD_ARG

    cmd: list[Any]
    if target == "end":
        cmd = ["seek", 999999, "absolute"]
    elif target.startswith("+"):
        try:
            val = float(target[1:])
            if not math.isfinite(val):
                raise ValueError("non-finite seek target")
            cmd = ["seek", val, "relative"]
        except ValueError:
            print("control: seek requires a target (+N, -N, N, or 'end')", file=sys.stderr)
            return EXIT_BAD_ARG
    elif target.startswith("-"):
        try:
            val = float(target)
            if not math.isfinite(val):
                raise ValueError("non-finite seek target")
            cmd = ["seek", val, "relative"]
        except ValueError:
            print("control: seek requires a target (+N, -N, N, or 'end')", file=sys.stderr)
            return EXIT_BAD_ARG
    else:
        try:
            val = float(target)
            if not math.isfinite(val) or val < 0:
                raise ValueError("non-finite or negative absolute seek target")
            cmd = ["seek", val, "absolute"]
        except ValueError:
            print("control: seek requires a target (+N, -N, N, or 'end')", file=sys.stderr)
            return EXIT_BAD_ARG

    if not _has_active_session():
        print(
            "control: no active playback session (unified daemon uses synchronous audio).",
            file=sys.stderr,
        )
        return EXIT_NO_SESSION

    rc, msg = _send_mpv_command(cmd)
    if rc != EXIT_OK:
        print(msg, file=sys.stderr)
        return rc
    print("seek ok")
    return EXIT_OK


def handle_stop(_args: argparse.Namespace) -> int:
    if not _has_active_session():
        print(
            "control: no active playback session (unified daemon uses synchronous audio).",
            file=sys.stderr,
        )
        return EXIT_NO_SESSION

    if _is_mpv_socket_alive():
        try:
            MpvIpc.send(["stop"], MPV_SOCK_PATH)
        except Exception:
            pass

    daemon_sock = _get_daemon_socket_path()
    if daemon_sock.is_socket():
        try:
            client = DaemonClient(socket_path=daemon_sock)
            client.interrupt()
        except Exception:
            pass

    mpv_pid_file = state_path("auto-speech-mpv.pid")
    if mpv_pid_file.is_file():
        try:
            pid = int(mpv_pid_file.read_text().strip())
            os.kill(pid, 15)
            time.sleep(0.05)
            try:
                os.kill(pid, 0)
                os.kill(pid, 9)
            except OSError:
                pass
        except (ValueError, OSError):
            pass
        finally:
            try:
                mpv_pid_file.unlink()
            except OSError:
                pass

    print("stopped")
    return EXIT_OK


def handle_autoplay(args: argparse.Namespace) -> int:
    action = getattr(args, "autoplay_action", None)
    if not action:
        print("autoplay: action required (on | off | status | mode | scope)", file=sys.stderr)
        return EXIT_INVALID_INPUT

    if action == "on":
        session_id = args.session_id or os.environ.get("CLAUDE_CODE_SESSION_ID") or None
        if not session_id:
            print("autoplay on: no session ID provided to enroll", file=sys.stderr)
            return EXIT_FAILURE
        disabled_marker = Path.home() / ".claude" / "auto-speech.disabled"
        try:
            disabled_marker.unlink(missing_ok=True)
        except OSError:
            pass
        enrollment = SessionEnrollment()
        enrollment.enroll(session_id)
        print(f"autoplay enabled for session {session_id}")
        return EXIT_OK

    elif action == "off":
        session_id = args.session_id or os.environ.get("CLAUDE_CODE_SESSION_ID") or None
        if session_id:
            enrollment = SessionEnrollment()
            enrollment.withdraw(session_id)
            print(f"autoplay disabled for session {session_id}")
        else:
            mute_path = Path.home() / ".claude" / "auto-speech.disabled"
            mute_path.parent.mkdir(parents=True, exist_ok=True)
            mute_path.touch()
            print("no session id — set global mute marker instead (~/.claude/auto-speech.disabled)")
        return EXIT_OK

    elif action == "status":
        session_id = args.session_id or os.environ.get("CLAUDE_CODE_SESSION_ID") or None
        enrollment = SessionEnrollment()
        scope = SoloScope()
        print(format_enrollment_status(enrollment, session_id))
        print(format_scope_status(scope, session_id))
        return EXIT_OK

    elif action == "mode":
        if not args.mode:
            cfg = load_autoplay_config()
            print(
                f"mode={cfg.get('mode')} summary_size={cfg.get('summary_size')} "
                f"config_path={cfg.get('config_path')}"
            )
            return EXIT_OK
        m = args.mode.strip().lower()
        if m in ("raw", "bypass"):
            new_mode = "raw"
            new_size = "small"
        elif m == "verbatim":
            new_mode = "verbatim"
            new_size = "small"
        elif m in ("small", "medium", "large"):
            new_mode = "summary"
            new_size = m
        else:
            print(
                f"autoplay-mode: unknown value {args.mode} (expected raw | verbatim | small | medium | large)",
                file=sys.stderr,
            )
            return EXIT_INVALID_INPUT

        config_dir = Path.home() / ".config" / "auto-speech"
        config_dir.mkdir(parents=True, exist_ok=True)
        config_file = config_dir / "autoplay.toml"
        config_file.write_text(
            f'[autoplay]\nmode = "{new_mode}"\nsummary_size = "{new_size}"\n',
            encoding="utf-8",
        )
        print(f"wrote {config_file}: mode={new_mode} summary_size={new_size}")
        return EXIT_OK

    elif action == "scope":
        scope = SoloScope()
        if not args.scope:
            session_id = args.session_id or os.environ.get("CLAUDE_CODE_SESSION_ID") or None
            print(format_scope_status(scope, session_id))
            return EXIT_OK
        s = args.scope.strip().lower()
        if s == "all":
            scope.clear()
            print("autoplay scope: ALL — every enrolled session reads (default)")
            return EXIT_OK
        elif s in ("solo", "this", "one", "only"):
            session_id = args.session_id or os.environ.get("CLAUDE_CODE_SESSION_ID") or None
            if not session_id:
                print("scope solo requires a session id", file=sys.stderr)
                return EXIT_FAILURE
            scope.set_solo(session_id)
            SessionEnrollment().enroll(session_id)
            print(f"autoplay scope: SOLO — only THIS session reads (spotlight={session_id})")
            return EXIT_OK
        else:
            print(f"scope: unknown value {args.scope} (expected all | solo)", file=sys.stderr)
            return EXIT_INVALID_INPUT

    print(f"autoplay: unknown action {action}", file=sys.stderr)
    return EXIT_INVALID_INPUT


def handle_narrate(args: argparse.Namespace) -> int:
    action = getattr(args, "narrate_action", None)
    if not action:
        print("narrate: action required (on | off | status | stop | config)", file=sys.stderr)
        return EXIT_INVALID_INPUT

    if action == "on":
        session_id = args.session_id or os.environ.get("CLAUDE_CODE_SESSION_ID") or None
        if not session_id:
            print(
                "narrate on: session ID not set; cannot register session",
                file=sys.stderr,
            )
            return EXIT_FAILURE
        marker_dir = Path.home() / ".claude" / "auto-speech-narrate-sessions"
        marker_dir.mkdir(parents=True, exist_ok=True)
        (marker_dir / session_id).touch()
        legacy_marker = Path.cwd() / ".claude" / "narrate.enabled"
        if legacy_marker.is_file():
            try:
                legacy_marker.unlink()
            except OSError:
                pass
        c = DaemonClient()
        c.ensure_daemon_running()
        print(f"narrate on — session {session_id}, daemon running")
        return EXIT_OK

    elif action == "off":
        session_id = args.session_id or os.environ.get("CLAUDE_CODE_SESSION_ID") or None
        if session_id:
            marker = Path.home() / ".claude" / "auto-speech-narrate-sessions" / session_id
            try:
                marker.unlink(missing_ok=True)
            except OSError:
                pass
        legacy_marker = Path.cwd() / ".claude" / "narrate.enabled"
        if legacy_marker.is_file():
            try:
                legacy_marker.unlink()
            except OSError:
                pass
        print(f"narrate off — session {session_id or '<none>'}")
        return EXIT_OK

    elif action == "status":
        session_id = args.session_id or os.environ.get("CLAUDE_CODE_SESSION_ID") or None
        marker_dir = Path.home() / ".claude" / "auto-speech-narrate-sessions"
        is_enrolled = (marker_dir / session_id).is_file() if session_id and marker_dir.is_dir() else False
        enrolled_count = len(list(marker_dir.glob("*"))) if marker_dir.is_dir() else 0
        c = DaemonClient()
        daemon_alive = c.is_alive()
        print(f"session: {session_id or '<none>'}")
        print(f"enrolled: {is_enrolled} (total enrolled: {enrolled_count})")
        print(f"daemon alive: {daemon_alive}")
        return EXIT_OK

    elif action == "stop":
        stop_script = _project_root() / "plugin" / "scripts" / "shell" / "narrator_service_stop.sh"
        if stop_script.is_file():
            res = subprocess.run(
                ["bash", str(stop_script)],
                check=False,
                stdout=subprocess.PIPE,
                text=True,
            )
            output = res.stdout.strip()
            if output:
                print(output)
            return res.returncode
        print("narrator daemon: not running")
        return EXIT_OK

    elif action == "config":
        cfg_act = (getattr(args, "action", None) or "show").strip().lower()
        cfg = load_narrator_config()
        cfg_path = cfg.get("config_path")
        if cfg_act == "path":
            if cfg_path:
                print(cfg_path)
            else:
                print("no narrator config file loaded (using shipped defaults)")
            return EXIT_OK
        elif cfg_act == "show":
            if cfg_path and Path(cfg_path).is_file():
                print(Path(cfg_path).read_text(encoding="utf-8"))
            else:
                print(json.dumps(cfg, indent=2))
            return EXIT_OK
        elif cfg_act == "edit":
            if not cfg_path:
                cfg_path = str(Path.home() / ".config" / "auto-speech" / "narrator.toml")
                Path(cfg_path).parent.mkdir(parents=True, exist_ok=True)
                example = _project_root() / "config" / "narrator.toml.example"
                if example.is_file():
                    Path(cfg_path).write_text(
                        example.read_text(encoding="utf-8"),
                        encoding="utf-8",
                    )
            editor = os.environ.get("EDITOR") or os.environ.get("VISUAL") or "open -t"
            try:
                subprocess.run(f'{editor} "{cfg_path}"', shell=True, check=False)
                print(f"opened {cfg_path} in {editor}")
                return EXIT_OK
            except Exception as exc:
                print(f"narrate config: failed to open editor: {exc}", file=sys.stderr)
                return EXIT_FAILURE
        else:
            print(
                f"config: unknown action {args.action} (expected show|edit|path)",
                file=sys.stderr,
            )
            return EXIT_INVALID_INPUT

    print(f"narrate: unknown action {action}", file=sys.stderr)
    return EXIT_INVALID_INPUT


def handle_yield(args: argparse.Namespace, store: Any | None = None, gate: Any | None = None) -> int:
    """`auto-speech yield <status|on|off|add|remove|clear|check> [apps…]`:
    the sound-yield switch and exception list (same verbs as the MCP tool)."""
    action = getattr(args, "yield_action", None) or "status"
    apps = getattr(args, "apps", None) or []
    result = run_sound_yield_action(action, apps, store=store, gate=gate)
    if not result.ok:
        print(result.text, file=sys.stderr)
        return EXIT_INVALID_INPUT
    print(result.text)
    return EXIT_OK


def handle_hook(args: argparse.Namespace) -> int:
    action = getattr(args, "hook_action", None)
    if not action:
        print("hook: action required (stop | narrator | bootstrap)", file=sys.stderr)
        return EXIT_INVALID_INPUT

    if action == "stop":
        if os.environ.get("AUTO_SPEECH_SUPPRESS_HOOKS") == "1":
            return EXIT_OK
        if AutoplayGate().worker_gated_off():
            return EXIT_OK

        payload_raw = sys.stdin.read() if sys.stdin else ""
        try:
            payload = json.loads(payload_raw) if payload_raw.strip() else {}
        except Exception:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}

        raw_session_id = payload.get("session_id") or payload.get("conversationId") or ""
        session_id = raw_session_id if isinstance(raw_session_id, str) else str(raw_session_id)
        raw_transcript = (
            payload.get("transcript_path") or payload.get("transcriptPath") or ""
        )
        transcript_path = raw_transcript if isinstance(raw_transcript, str) else str(raw_transcript)

        scope = SoloScope()
        if not scope.session_allowed(session_id):
            return EXIT_OK

        enrollment = SessionEnrollment()
        if not enrollment.is_enrolled(session_id):
            return EXIT_OK

        beacon_dir = Path("/tmp")
        beacon_file = (
            beacon_dir / f"auto-speech-last-stop.{session_id}"
            if session_id
            else beacon_dir / "auto-speech-last-stop"
        )
        try:
            beacon_file.touch()
        except OSError:
            pass

        c = DaemonClient()
        c.ensure_daemon_running()

        worker_script = _project_root() / "plugin" / "scripts" / "shell" / "autoplay_worker.sh"
        if worker_script.is_file():
            beacon_mtime = "0"
            if beacon_file.is_file():
                try:
                    beacon_mtime = str(beacon_file.stat().st_mtime)
                except OSError:
                    beacon_mtime = "0"
            try:
                subprocess.Popen(
                    [
                        "bash",
                        str(worker_script),
                        beacon_mtime,
                        transcript_path,
                        session_id,
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    stdin=subprocess.DEVNULL,
                    start_new_session=True,
                )
            except Exception:
                pass
        return EXIT_OK

    elif action == "narrator":
        if os.environ.get("AUTO_SPEECH_SUPPRESS_HOOKS") == "1":
            return EXIT_OK

        payload_raw = sys.stdin.read() if sys.stdin else ""
        try:
            payload = json.loads(payload_raw) if payload_raw.strip() else {}
        except Exception:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}

        raw_session_id = (
            payload.get("session_id")
            or payload.get("conversationId")
            or os.environ.get("CLAUDE_CODE_SESSION_ID", "")
        )
        session_id = raw_session_id if isinstance(raw_session_id, str) else str(raw_session_id)
        if not session_id:
            return EXIT_OK

        marker = Path.home() / ".claude" / "auto-speech-narrate-sessions" / session_id
        if not marker.is_file():
            return EXIT_OK

        c = DaemonClient()
        c.ensure_daemon_running()

        raw_event = payload.get("hook_event_name", "unknown")
        event = raw_event if isinstance(raw_event, str) else str(raw_event)

        record = {
            "ts": int(time.time()),
            "cwd": os.getcwd(),
            "pid": os.getpid(),
            "event": event,
            "payload": payload,
        }
        events_log = state_path("auto-speech-narrator-events.jsonl")
        try:
            with open(events_log, "a", encoding="utf-8") as f:
                f.write(json.dumps(record) + "\n")
        except OSError:
            pass
        return EXIT_OK

    elif action == "bootstrap":
        lock_path = _project_root() / "uv.lock"
        stamp_path = _project_root() / "setup" / ".synced"
        force = getattr(args, "force", False)

        if force or needs_sync(lock_path, stamp_path):
            bootstrap_script = _project_root() / "setup" / "bootstrap.sh"
            if bootstrap_script.is_file():
                cmd = ["bash", str(bootstrap_script)]
                if force:
                    cmd.append("--force")
                res = subprocess.run(cmd, check=False)
                return res.returncode
        return EXIT_OK

    print(f"hook: unknown action {action}", file=sys.stderr)
    return EXIT_INVALID_INPUT


def handle_doctor(args: argparse.Namespace) -> int:
    max_q = load_narrator_config().get("max_queue_depth", 32)
    doc = Doctor(max_queue_depth=max_q)
    report = doc.run()
    if getattr(args, "json", False):
        print(report.to_json())
    else:
        print(report.to_text())
    return report.exit_code


def handle_update(args: argparse.Namespace) -> int:
    bootstrap_script = _project_root() / "setup" / "bootstrap.sh"
    if not bootstrap_script.is_file():
        print("auto-speech: bootstrap script not found", file=sys.stderr)
        return EXIT_FAILURE
    cmd = ["bash", str(bootstrap_script)]
    if getattr(args, "force", False):
        cmd.append("--force")
    res = subprocess.run(cmd, check=False)
    if res.returncode == 0:
        print("auto-speech: dependencies updated and in sync.")
        return EXIT_OK
    else:
        print(
            f"auto-speech: update failed (exit code {res.returncode}). See /tmp/auto-speech-sync.log.",
            file=sys.stderr,
        )
        return EXIT_FAILURE


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="auto-speech",
        description="Unified CLI dispatcher for auto-speech: speech synthesis, playback transport, session management, and diagnostics.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--version",
        action="version",
        version="auto-speech 0.3.0",
    )
    subparsers = parser.add_subparsers(
        dest="subcommand",
        metavar="<subcommand>",
        help="Subcommand to execute. Run 'auto-speech <subcommand> --help' for details.",
    )

    # 1. speak
    p_speak = subparsers.add_parser("speak", help="Synthesize and play speech.")
    p_speak.add_argument("words", nargs="*", default=[], help="Words to speak.")
    p_speak.add_argument(
        "--source-hash",
        type=str,
        default=None,
        help="64-hex SHA-256 hash for cache lookup/promotion.",
    )
    p_speak.add_argument(
        "--session-id",
        type=str,
        default=None,
        help="Session ID for tracking speech events.",
    )
    p_speak.add_argument(
        "--priority",
        type=int,
        default=int(Priority.EXPLICIT_MCP),
        help="Queue priority (1=USER_INTERRUPT, 2=EXPLICIT_MCP, 3=AUTOPLAY, 4=TOOL_NARRATION).",
    )
    p_speak.add_argument(
        "--voice",
        type=str,
        default="system",
        help="Voice identifier (default: system).",
    )
    p_speak.add_argument(
        "--speed",
        type=float,
        default=1.0,
        help="Speech speed multiplier (default: 1.0).",
    )
    p_speak.add_argument(
        "--socket-path",
        type=str,
        default=None,
        help="Override path to auto-speech daemon UNIX socket.",
    )
    p_speak.add_argument(
        "--ordinal",
        type=int,
        default=1,
        help="1-indexed ordinal position (compatibility flag).",
    )
    p_speak.add_argument(
        "--keep-artifacts",
        action="store_true",
        help="Preserve temporary audio files (compatibility flag).",
    )

    # 2. replay
    p_replay = subparsers.add_parser("replay", help="Replay recent cached speech.")
    p_replay.add_argument(
        "--ordinal",
        type=int,
        default=1,
        help="1-indexed recency ordinal (1 = newest, default: 1).",
    )
    p_replay.add_argument(
        "--keep-artifacts",
        action="store_true",
        help="Preserve artifacts (compatibility flag).",
    )

    # 3-6. transport
    subparsers.add_parser("pause", help="Pause active mpv audio playback.")
    subparsers.add_parser("resume", help="Resume paused mpv audio playback.")
    subparsers.add_parser("restart", help="Restart active mpv audio playback from start.")
    subparsers.add_parser("stop", help="Stop playback immediately and purge pending queue.")

    p_seek = subparsers.add_parser("seek", help="Seek active playback (+N, -N, N, or 'end').")
    p_seek.add_argument(
        "target",
        type=str,
        help="Target offset: '+N', '-N', absolute seconds 'N', or 'end'.",
    )

    # 7. autoplay
    p_ap = subparsers.add_parser("autoplay", help="Autoplay subsystem controls.")
    sub_ap = p_ap.add_subparsers(dest="autoplay_action", metavar="<action>")

    p_ap_on = sub_ap.add_parser("on", help="Enable autoplay for this session.")
    p_ap_on.add_argument(
        "--session-id",
        type=str,
        default=None,
        help="Session ID to enroll.",
    )

    p_ap_off = sub_ap.add_parser(
        "off",
        help="Disable autoplay for this session (or globally).",
    )
    p_ap_off.add_argument(
        "--session-id",
        type=str,
        default=None,
        help="Session ID to withdraw.",
    )

    p_ap_status = sub_ap.add_parser(
        "status",
        help="Show autoplay enrollment, scope, and log status.",
    )
    p_ap_status.add_argument(
        "--session-id",
        type=str,
        default=None,
        help="Session ID to inspect.",
    )

    p_ap_mode = sub_ap.add_parser("mode", help="Show or set autoplay rewrite mode.")
    p_ap_mode.add_argument(
        "mode",
        nargs="?",
        default=None,
        help="Mode: raw | verbatim | small | medium | large.",
    )

    p_ap_scope = sub_ap.add_parser("scope", help="Show or set autoplay solo scope.")
    p_ap_scope.add_argument(
        "scope",
        nargs="?",
        default=None,
        help="Scope: all | solo.",
    )
    p_ap_scope.add_argument(
        "--session-id",
        type=str,
        default=None,
        help="Session ID for solo scope.",
    )

    # 8. narrate
    p_nr = subparsers.add_parser("narrate", help="Narration subsystem controls.")
    sub_nr = p_nr.add_subparsers(dest="narrate_action", metavar="<action>")

    p_nr_on = sub_nr.add_parser("on", help="Enable narration for this session.")
    p_nr_on.add_argument(
        "--session-id",
        type=str,
        default=None,
        help="Session ID to enroll.",
    )

    p_nr_off = sub_nr.add_parser("off", help="Disable narration for this session.")
    p_nr_off.add_argument(
        "--session-id",
        type=str,
        default=None,
        help="Session ID to withdraw.",
    )

    p_nr_status = sub_nr.add_parser(
        "status",
        help="Show narration daemon and marker status.",
    )
    p_nr_status.add_argument(
        "--session-id",
        type=str,
        default=None,
        help="Session ID to inspect.",
    )

    sub_nr.add_parser("stop", help="Stop the narrator daemon process.")

    p_nr_cfg = sub_nr.add_parser("config", help="Show, edit, or print narrator config path.")
    p_nr_cfg.add_argument(
        "action",
        nargs="?",
        default="show",
        help="Config action: show | path | edit.",
    )

    # 8b. yield (sound yield: hold speech while another app plays sound)
    p_sy = subparsers.add_parser(
        "yield",
        aliases=["sound-yield"],
        help="Hold speech while another app plays sound: switch, exception list, live check.",
    )
    sub_sy = p_sy.add_subparsers(dest="yield_action", metavar="<action>")
    sub_sy.add_parser("status", help="Show the switch, the exception list and the longest hold.")
    sub_sy.add_parser("on", help="Hold speech while another app plays sound (default).")
    sub_sy.add_parser("off", help="Never hold: speech plays over other apps' sound.")
    p_sy_add = sub_sy.add_parser("add", help="Add apps whose sound does not hold speech.")
    p_sy_add.add_argument("apps", nargs="+", help="Bundle ids (com.apple.Music) or names (Music).")
    p_sy_rm = sub_sy.add_parser("remove", help="Remove apps from the exception list.")
    p_sy_rm.add_argument("apps", nargs="+", help="Bundle ids or names to remove.")
    sub_sy.add_parser("clear", help="Empty the exception list.")
    sub_sy.add_parser("check", help="Say who is playing sound now and whether speech would hold.")

    # 9. hook
    p_hk = subparsers.add_parser("hook", help="Lifecycle hook execution.")
    sub_hk = p_hk.add_subparsers(dest="hook_action", metavar="<action>")

    sub_hk.add_parser("stop", help="Handle Stop hook (end-of-turn autoplay).")
    sub_hk.add_parser("narrator", help="Handle narrator tool activity events.")
    p_hk_boot = sub_hk.add_parser("bootstrap", help="Handle SessionStart bootstrap hook.")
    p_hk_boot.add_argument(
        "--force",
        action="store_true",
        help="Force dependency synchronization.",
    )

    # 10. doctor
    p_doc = subparsers.add_parser("doctor", help="System diagnostic health check.")
    p_doc.add_argument(
        "--json",
        action="store_true",
        help="Output health report in structured JSON format.",
    )

    # 11. update
    p_upd = subparsers.add_parser(
        "update",
        help="Update and synchronize virtual environment dependencies.",
    )
    p_upd.add_argument(
        "--force",
        action="store_true",
        help="Force sync regardless of lockfile stamp.",
    )

    return parser


def main(
    argv: list[str] | None = None,
    *,
    client: Any | None = None,
    sink: Any | None = None,
) -> int:
    parser = build_parser()
    if argv is None:
        argv = sys.argv[1:]

    if not argv:
        parser.print_help(sys.stderr)
        return EXIT_INVALID_INPUT

    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return exc.code

    if args.subcommand is None:
        parser.print_help(sys.stderr)
        return EXIT_INVALID_INPUT

    try:
        if args.subcommand == "speak":
            return handle_speak(args, client=client, sink=sink)
        elif args.subcommand == "replay":
            return handle_replay(args, client=client, sink=sink)
        elif args.subcommand == "pause":
            return handle_pause(args)
        elif args.subcommand == "resume":
            return handle_resume(args)
        elif args.subcommand == "restart":
            return handle_restart(args)
        elif args.subcommand == "stop":
            return handle_stop(args)
        elif args.subcommand == "seek":
            return handle_seek(args)
        elif args.subcommand == "autoplay":
            return handle_autoplay(args)
        elif args.subcommand == "narrate":
            return handle_narrate(args)
        elif args.subcommand in ("yield", "sound-yield"):
            return handle_yield(args)
        elif args.subcommand == "hook":
            return handle_hook(args)
        elif args.subcommand == "doctor":
            return handle_doctor(args)
        elif args.subcommand == "update":
            return handle_update(args)
        else:
            parser.print_help(sys.stderr)
            return EXIT_INVALID_INPUT
    except KeyboardInterrupt:
        return EXIT_INTERRUPTED


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(EXIT_INTERRUPTED)
