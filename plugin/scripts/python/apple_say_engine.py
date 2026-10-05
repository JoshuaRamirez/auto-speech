"""AppleSayEngine: Native macOS Neural Voice adapter via `say`.

Uses Apple's onboard high-quality neural voice (e.g. Ava Premium / Enhanced)
with zero cold-boot time, 0 MB Unified Memory footprint, and native
24kHz 16-bit mono RIFF WAV output matching NativeAudioSink and CacheStore.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from tts_engine import TTSGenerationError

if TYPE_CHECKING:
    from voice_profile import VoiceProfile

DEFAULT_SAY_RATE = 212
APPLE_SAMPLE_RATE = 24000
PREFERRED_VOICES = ("Ava (Premium)", "Ava (Enhanced)", "Ava", "Samantha (Premium)", "Samantha (Enhanced)", "Samantha")

_CACHED_AVAILABLE_VOICES: Optional[dict[str, str]] = None
_CACHED_DEFAULT_VOICE: Optional[str] = None
_CACHED_SYSTEM_SPOKEN_VOICE: Optional[str] = None


def get_system_spoken_voice(force_refresh: bool = False) -> Optional[str]:
    """Detect the configured macOS Spoken Content voice (e.g. Aaron / Siri Natural)."""
    global _CACHED_SYSTEM_SPOKEN_VOICE
    if _CACHED_SYSTEM_SPOKEN_VOICE is not None and not force_refresh:
        return _CACHED_SYSTEM_SPOKEN_VOICE

    if sys.platform != "darwin":
        return None
    try:
        res = subprocess.run(
            ["defaults", "read", "com.apple.Accessibility", "SpokenContentDefaultVoiceSelectionsByLanguage"],
            capture_output=True,
            text=True,
            check=False,
            timeout=1,
        )
        if res.returncode == 0:
            m = re.search(r"voiceId\s*=\s*\"?([^\";]+)\"?", res.stdout)
            if m:
                raw_id = m.group(1).strip()
                parts = raw_id.split(".")
                name = parts[-1]
                _CACHED_SYSTEM_SPOKEN_VOICE = name
                return name
    except Exception:
        pass
    return None


def get_available_say_voices(force_refresh: bool = False) -> dict[str, str]:
    """Return dictionary of voice_name -> language code available on this Mac."""
    global _CACHED_AVAILABLE_VOICES
    if _CACHED_AVAILABLE_VOICES is not None and not force_refresh:
        return _CACHED_AVAILABLE_VOICES

    if sys.platform != "darwin" or shutil.which("say") is None:
        _CACHED_AVAILABLE_VOICES = {}
        return _CACHED_AVAILABLE_VOICES

    try:
        res = subprocess.run(["say", "-v", "?"], capture_output=True, text=True, check=False)
        if res.returncode != 0:
            _CACHED_AVAILABLE_VOICES = {}
            return _CACHED_AVAILABLE_VOICES

        voices = {}
        for line in res.stdout.splitlines():
            line_str = line.strip()
            if not line_str:
                continue
            m = re.match(r"^([^\t#]+?)\s+([a-z]{2}_[A-Z]{2})\s*#", line_str)
            if m:
                vname = m.group(1).strip()
                lang = m.group(2).strip()
                voices[vname] = lang
        _CACHED_AVAILABLE_VOICES = voices
        return voices
    except Exception:
        _CACHED_AVAILABLE_VOICES = {}
        return _CACHED_AVAILABLE_VOICES


def get_default_say_voice(force_refresh: bool = False) -> str:
    """Discover best available voice on this Mac in preference order."""
    global _CACHED_DEFAULT_VOICE
    if _CACHED_DEFAULT_VOICE is not None and not force_refresh:
        return _CACHED_DEFAULT_VOICE

    voices = get_available_say_voices(force_refresh=force_refresh)
    for preferred in PREFERRED_VOICES:
        if preferred in voices:
            _CACHED_DEFAULT_VOICE = preferred
            return preferred

    # Fallback to any en_US voice or first available voice
    for vname, lang in voices.items():
        if lang.startswith("en_"):
            _CACHED_DEFAULT_VOICE = vname
            return vname

    if voices:
        _CACHED_DEFAULT_VOICE = next(iter(voices.keys()))
        return _CACHED_DEFAULT_VOICE

    _CACHED_DEFAULT_VOICE = "Ava (Premium)"
    return _CACHED_DEFAULT_VOICE


class AppleSayEngine:
    """Synthesize 24kHz LEI16 mono WAV using native macOS `say`."""

    def __init__(
        self,
        voice: Optional[str] = None,
        default_rate: Optional[int] = None,
    ) -> None:
        self._voice = voice
        self._default_rate = default_rate
        self._explicit_rate = default_rate is not None

    @property
    def model_id(self) -> str:
        voice = self.resolve_voice()
        if voice:
            return f"apple-say/{voice}"
        sys_voice = get_system_spoken_voice()
        if sys_voice:
            return f"apple-say/{sys_voice}"
        return "apple-say/system"

    def _ensure_loaded(self) -> None:
        """No-op for Apple Say: system daemon is always ready without memory load."""
        return

    def resolve_voice(self, profile_voice_id: Optional[str] = None) -> Optional[str]:
        """Resolve voice name.

        Returns explicit voice name string if an override is provided, or None
        if the macOS system default voice should be used (no -v flag passed).
        """
        candidate = self._voice
        if not candidate and profile_voice_id:
            candidate = profile_voice_id
        if not candidate:
            candidate = os.environ.get("AUTO_SPEECH_VOICE")

        if not candidate:
            return None

        candidate_str = candidate.strip()
        lower = candidate_str.lower()
        if lower in ("default", "system", "auto", "none", "", "af_nova"):
            return None

        return candidate_str

    def resolve_rate(self, speed: float = 1.0) -> int:
        """Calculate words per minute based on profile speed multiplier."""
        base_rate = self._default_rate if self._default_rate is not None else DEFAULT_SAY_RATE
        return max(50, min(500, int(round(base_rate * speed))))

    def synthesize(
        self,
        text: str,
        voice_profile: Optional[VoiceProfile] = None,
        out_path: Optional[Path] = None,
    ) -> None:
        """Synthesize text to 24kHz LEI16 WAV atomically."""
        if not text or not text.strip():
            raise TTSGenerationError("empty text passed to synthesize")

        if out_path is None:
            raise TTSGenerationError("out_path is required for synthesize")

        if sys.platform != "darwin" or shutil.which("say") is None:
            raise TTSGenerationError("AppleSayEngine is only supported on macOS with `say` utility available")

        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_wav = out_path.with_suffix(out_path.suffix + ".partial")

        voice_id = getattr(voice_profile, "voice_id", None) if voice_profile else None
        speed = getattr(voice_profile, "speed", 1.0) if voice_profile else 1.0

        voice = self.resolve_voice(voice_id)

        cmd = ["say"]
        if voice:
            cmd.extend(["-v", voice])

        if speed is not None and abs(float(speed) - 1.0) > 0.01:
            cmd.extend(["-r", str(self.resolve_rate(speed))])
        elif self._explicit_rate and self._default_rate is not None:
            cmd.extend(["-r", str(self._default_rate)])

        cmd.extend([
            "--file-format=WAVE",
            "--data-format=LEI16@24000",
            "-o",
            str(tmp_wav),
        ])

        text_file: Optional[Path] = None
        try:
            if len(text) > 8192:
                fd, tf_path = tempfile.mkstemp(prefix="say_input_", suffix=".txt")
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(text)
                text_file = Path(tf_path)
                cmd.extend(["-f", str(text_file)])
            else:
                cmd.append(text)

            res = subprocess.run(cmd, capture_output=True, text=True, check=False)
            if res.returncode != 0:
                raise TTSGenerationError(f"say command failed with exit {res.returncode}: {res.stderr.strip()}")

            if not tmp_wav.is_file() or tmp_wav.stat().st_size == 0:
                raise TTSGenerationError("say command finished but generated no audio file")

            tmp_wav.replace(out_path)
        finally:
            if text_file and text_file.exists():
                try:
                    text_file.unlink()
                except OSError:
                    pass
            if tmp_wav.exists():
                try:
                    tmp_wav.unlink()
                except OSError:
                    pass
