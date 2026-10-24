import os
import shutil
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from typing import Any, Callable, TypeVar

from apple_say_engine import AppleSayEngine
from resilient_synthesizer import ResilientSynthesizer
from tts_engine import SynthesisTimeout, TTSEngine

__all__ = ["SynthesisTimeout", "TTSExecutor", "get_default_tts_engine"]

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
        self._executor = self._new_executor()
        self._lock = threading.Lock()
        self.abandoned = 0   # workers given up on after a timeout (diagnostics)

    @staticmethod
    def _new_executor() -> ThreadPoolExecutor:
        return ThreadPoolExecutor(max_workers=1, thread_name_prefix="tts-worker")
        
    def ensure_loaded(self) -> None:
        """Pre-warm the model on the worker thread."""
        load_fn = getattr(self._engine, "_ensure_loaded", None) or getattr(self._engine, "ensure_loaded", None)
        if callable(load_fn):
            future = self._executor.submit(load_fn)
            future.result()
        
    def submit(self, fn: Callable[..., T], *args, timeout: float | None = None, **kwargs) -> T:
        """Run a function on the TTS worker thread and return the result.

        With `timeout`, a call that has not returned by then raises
        SynthesisTimeout and the worker thread is abandoned: the one worker
        is single-lane, so a call stuck in the model would otherwise hold
        every later call behind it (the 2026-10-19 hang: five hours silent
        behind one synthesis). The stuck thread is left to finish or not;
        a new worker serves the next call at once."""
        with self._lock:
            executor = self._executor
        future = executor.submit(fn, *args, **kwargs)
        try:
            return future.result(timeout=timeout)
        except FutureTimeout:
            with self._lock:
                if self._executor is executor:
                    self._executor = self._new_executor()
                    self.abandoned += 1
            executor.shutdown(wait=False, cancel_futures=True)
            raise SynthesisTimeout(float(timeout or 0.0)) from None

    def shutdown(self, wait: bool = True) -> None:
        with self._lock:
            executor = self._executor
        executor.shutdown(wait=wait)
        
    @property
    def synth(self) -> ResilientSynthesizer:
        return self._synth
        
    @property
    def engine(self) -> TTSEngine:
        return self._engine
