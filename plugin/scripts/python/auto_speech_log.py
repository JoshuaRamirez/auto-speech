"""Shared size-capped logging for auto-speech.

Before this, the daemon/worker logs under /tmp grew without bound; weeks
of unattended operation could fill /tmp and silently break playback. This
module centralizes one retention policy with two entry points:

  get_logger(name, path)
      A logging.Logger backed by a RotatingFileHandler (maxBytes +
      backupCount). For a LONG-LIVED in-process writer — the narrator
      daemon — where mid-run rotation is required. The handler must OWN
      the file exclusively; a shell that also redirects a child's stdio
      onto the same path would defeat rotation (the inherited fd keeps
      the renamed inode alive), so such processes send raw stdio to a
      separate .out file instead.

  rotate_if_oversize(path)
      A one-shot size-check + cascading rename, for callers that rotate a
      file BEFORE opening it for append. The shell hooks use a bash
      mirror of this (plugin/scripts/shell/log_rotate.sh) on their fast
      path; this Python form exists for in-process callers and tests and
      documents the canonical policy.

Caps are env-overridable so retention can be tuned without code changes:
  AUTO_SPEECH_LOG_MAX_BYTES   (default 5 MiB)
  AUTO_SPEECH_LOG_BACKUPS     (default 3)
"""
from __future__ import annotations

import logging
import os
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

DEFAULT_MAX_BYTES = 5 * 1024 * 1024  # 5 MiB — mirrored in log_rotate.sh
DEFAULT_BACKUPS = 3


def max_bytes() -> int:
    try:
        return int(os.environ.get("AUTO_SPEECH_LOG_MAX_BYTES", DEFAULT_MAX_BYTES))
    except ValueError:
        return DEFAULT_MAX_BYTES


def backup_count() -> int:
    try:
        return int(os.environ.get("AUTO_SPEECH_LOG_BACKUPS", DEFAULT_BACKUPS))
    except ValueError:
        return DEFAULT_BACKUPS


def get_logger(name: str, path: Path) -> logging.Logger:
    """A size-capped logger writing to `path`. Idempotent: repeated calls
    with the same (name, path) reuse the existing handler rather than
    stacking duplicates. Never raises — a handler that cannot open its
    file degrades to a NullHandler so logging can't crash the daemon."""
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    logger.propagate = False

    handler_id = f"auto-speech-rot:{path}"
    for h in logger.handlers:
        if getattr(h, "_auto_speech_id", None) == handler_id:
            return logger

    handler: logging.Handler
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            str(path),
            maxBytes=max_bytes(),
            backupCount=backup_count(),
            encoding="utf-8",
            delay=True,  # don't open the file until the first emit
        )
        fmt = logging.Formatter("[%(asctime)sZ] %(message)s", datefmt="%Y-%m-%dT%H:%M:%S")
        fmt.converter = time.gmtime  # UTC, matching the historical 'Z' suffix
        handler.setFormatter(fmt)
    except OSError:
        handler = logging.NullHandler()
    handler._auto_speech_id = handler_id  # type: ignore[attr-defined]
    logger.addHandler(handler)
    return logger


def rotate_if_oversize(path: Path, cap: int | None = None) -> bool:
    """Cascade-rename `path` → `path.1` → … → `path.N` (N = backup_count)
    when it meets/exceeds the cap, dropping the oldest. Returns True iff a
    rotation occurred. Safe to call before a redirect opens `path` for
    append; never raises."""
    limit = cap if cap is not None else max_bytes()
    try:
        size = path.stat().st_size
    except OSError:
        return False
    if size < limit:
        return False
    backups = backup_count()
    try:
        for i in range(backups, 0, -1):
            src = path if i == 1 else path.with_name(f"{path.name}.{i - 1}")
            dst = path.with_name(f"{path.name}.{i}")
            if src.exists():
                os.replace(src, dst)
        return True
    except OSError:
        return False


if __name__ == "__main__":
    import sys

    # CLI for shell callers: `python auto_speech_log.py rotate <path>`
    if len(sys.argv) == 3 and sys.argv[1] == "rotate":
        rotate_if_oversize(Path(sys.argv[2]))
    else:
        print("usage: auto_speech_log.py rotate <path>", file=sys.stderr)
        sys.exit(2)
