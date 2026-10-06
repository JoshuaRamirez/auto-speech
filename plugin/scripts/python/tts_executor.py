import os
import shutil
import sys
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any, TypeVar

from apple_say_engine import AppleSayEngine
from resilient_synthesizer import ResilientSynthesizer
from tts_engine import TTSEngine

T = TypeVar("T")


def get_default_tts_engine() -> Any:
    """Return default TTS engine: AppleSayEngine on macOS, TTSEngine (Kokoro) otherwise."""
    preferred = os.environ.get("AUTO_SPEECH_TTS_ENGINE", "").strip().lower()
    if preferred == "kokoro":
        return TTSEngine()
    if preferred in ("say", "apple") or (sys.platform == "darwin" and shutil.which("say") is not None):
        return AppleSayEngine()
    return TTSEngine()


class TTSExecutor:
    """Single-responsibility module for TTS execution.
    
    Ensures TTS operations execute safely on a dedicated worker thread.
    """
    def __init__(self, engine: Any | None = None, synth: ResilientSynthesizer | None = None):
        self._engine = engine if engine is not None else get_default_tts_engine()
        self._synth = synth or ResilientSynthesizer(self._engine)
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="tts-worker")
        
    def ensure_loaded(self) -> None:
        """Pre-warm the model on the worker thread."""
        future = self._executor.submit(self._engine._ensure_loaded)
        future.result()
        
    def submit(self, fn: Callable[..., T], *args, **kwargs) -> T:
        """Run a function on the TTS worker thread and return the result."""
        return self._executor.submit(fn, *args, **kwargs).result()

    def shutdown(self, wait: bool = True) -> None:
        self._executor.shutdown(wait=wait)
        
    @property
    def synth(self) -> ResilientSynthesizer:
        return self._synth
        
    @property
    def engine(self) -> TTSEngine:
        return self._engine
