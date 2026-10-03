with open("plugin/scripts/python/narrator_service.py", "r") as f:
    content = f.read()

replacement = """    def _ensure_tts_initialized(self) -> None:
        if getattr(self, "_tts_initialized", False):
            return
        if getattr(self, "_tts_executor", None) is None:
            # Fallback for stress tests that bypass __init__
            from tts_executor import TTSExecutor
            self._tts_executor = TTSExecutor()
            if hasattr(self, "_synth"):
                self._tts_executor._synth = self._synth
                
        if self._profile is None:"""

import re
content = re.sub(
    r'    def _ensure_tts_initialized\(self\) -> None:\n        if getattr\(self, "_tts_initialized", False\):\n            return\n        if self\._profile is None:',
    replacement,
    content
)

with open("plugin/scripts/python/narrator_service.py", "w") as f:
    f.write(content)
