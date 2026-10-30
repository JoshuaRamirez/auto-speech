"""pytest entry: the suite is silent unless AUTO_SPEECH_TEST_REAL_AUDIO=1 (see quiet_audio.py)."""

from __future__ import annotations

import isolation  # noqa: F401,E402,I001  -- before any auto-speech module: private stores only (#m1030-1241-6e0a)
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from quiet_audio import ensure_quiet  # noqa: E402

ensure_quiet()
