with open("tests/test_narrator_service.py", "r") as f:
    content = f.read()

import re

# We need to set svc._tts_executor = type('MockExecutor', (), {'submit': lambda fn, *args: fn(*args), 'ensure_loaded': lambda: None, 'synth': synth})
# But the easier way is just string replace "svc._engine = None" with "svc._engine = None\n    class MockExecutor:\n        def submit(self, fn, *args, **kwargs): return fn(*args, **kwargs)\n        def ensure_loaded(self): pass\n        @property\n        def synth(self): return svc._synth\n    svc._tts_executor = MockExecutor()"

replacement = """    svc._engine = None
    class MockExecutor:
        def submit(self, fn, *args, **kwargs): return fn(*args, **kwargs)
        def ensure_loaded(self): pass
        @property
        def synth(self): return svc._synth
    svc._tts_executor = MockExecutor()"""

content = content.replace("    svc._engine = None", replacement)

with open("tests/test_narrator_service.py", "w") as f:
    f.write(content)
