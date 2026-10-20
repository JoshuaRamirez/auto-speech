"""The test suite never plays sound (his 2026-10-20 10:40 "turn off the speech tests").

ensure_quiet() points mpv at its null audio output (AUTO_SPEECH_MPV_AO=null) unless the
explicit opt-in AUTO_SPEECH_TEST_REAL_AUDIO=1 is set. tests/run_all.sh exports the same,
and every test that can reach mpv calls ensure_quiet() at import, so a bare `pytest` or
`python tests/test_x.py` is silent too.
"""

from __future__ import annotations

import os

REAL_AUDIO_ENV = "AUTO_SPEECH_TEST_REAL_AUDIO"
AUDIO_OUTPUT_ENV = "AUTO_SPEECH_MPV_AO"
SILENT_OUTPUT = "null"


def real_audio_allowed() -> bool:
    """True only when AUTO_SPEECH_TEST_REAL_AUDIO=1: the one way a test may make sound."""
    return os.environ.get(REAL_AUDIO_ENV, "").strip() == "1"


def ensure_quiet() -> str | None:
    """Forces mpv's null output for this process unless real audio is opted in.

    Returns the audio output in force (None when real audio is allowed and nothing set).
    """
    if real_audio_allowed():
        return (os.environ.get(AUDIO_OUTPUT_ENV) or "").strip() or None
    os.environ[AUDIO_OUTPUT_ENV] = SILENT_OUTPUT
    return SILENT_OUTPUT
