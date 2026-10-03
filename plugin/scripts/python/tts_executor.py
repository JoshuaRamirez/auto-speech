import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable, TypeVar

from resilient_synthesizer import ResilientSynthesizer
from tts_engine import TTSEngine
from voice_profile import VoiceProfile

T = TypeVar("T")

class TTSExecutor:
    """Single-responsibility module for TTSEngine ML execution.
    
    Ensures MLX operations stay on a single thread (a hard MLX requirement).
    """
    def __init__(self, engine: TTSEngine | None = None, synth: ResilientSynthesizer | None = None):
        self._engine = engine or TTSEngine()
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
