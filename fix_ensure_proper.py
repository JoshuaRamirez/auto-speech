import re
with open("plugin/scripts/python/narrator_service.py", "r") as f:
    content = f.read()

replacement = """    def _ensure_tts_initialized(self) -> None:
        if getattr(self, "_tts_initialized", False):
            return
        if getattr(self, "_tts_executor", None) is None:
            # Fallback for stress tests that bypass __init__
            from tts_executor import TTSExecutor
            self._tts_executor = TTSExecutor(
                engine=getattr(self, "_engine", None),
                synth=getattr(self, "_synth", None)
            )
                
        if self._profile is None:
            self._profile = _load_profile_or_fallback(self._config)
        self._tts_executor.ensure_loaded()
        self._tts_initialized = True"""

# We just replace the old _ensure_tts_initialized body.
# Let's find it with regex or split.
start_idx = content.find("    def _ensure_tts_initialized(self) -> None:")
end_idx = content.find("    def _tts_worker(self) -> None:")
content = content[:start_idx] + replacement + "\n\n" + content[end_idx:]

with open("plugin/scripts/python/narrator_service.py", "w") as f:
    f.write(content)

