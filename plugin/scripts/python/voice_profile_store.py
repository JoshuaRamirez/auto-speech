"""VoiceProfileStore: load/save the active VoiceProfile as JSON."""
from __future__ import annotations

import json
import os
from pathlib import Path

from voice_profile import VoiceProfile


class VoiceProfileStore:
    """Read/write the active VoiceProfile.

    File format is a single JSON object matching VoiceProfile's shape.
    Writes are atomic (temp file + rename).
    """

    def __init__(self, path: Path) -> None:
        self._path = path

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> VoiceProfile | None:
        if not self._path.exists():
            return None
        with open(self._path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return VoiceProfile.from_dict(data)

    def save(self, profile: VoiceProfile) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(self._path.suffix + ".partial")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(profile.to_dict(), fh, indent=2)
            fh.write("\n")
        os.replace(tmp, self._path)
