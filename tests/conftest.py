"""pytest entry: the suite is silent unless AUTO_SPEECH_TEST_REAL_AUDIO=1 (see quiet_audio.py)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from quiet_audio import ensure_quiet  # noqa: E402

ensure_quiet()
