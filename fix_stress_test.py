import re

with open("tests/test_socket_server_stress.py", "r") as f:
    content = f.read()

# Replace:
# synth=mock_synth,
# engine=None
# with:
# tts_executor=mock_executor

# First let's find NarratorService(
# ...
# We can just run a python script to redefine NarratorService in the test if that's easier, or modify it via sed.

content = content.replace("synth=mock_synth,", "")
content = content.replace("synth=synth,", "")
content = content.replace("engine=None", "")

# In the test files, they define a mock_synth or similar.
# We'll just define MockExecutor inline.

patch_code = """
class MockExecutor:
    def __init__(self, synth):
        self.synth = synth
    def submit(self, fn, *args, **kwargs): return fn(*args, **kwargs)
    def ensure_loaded(self): pass

import tts_executor
tts_executor.TTSExecutor = MockExecutor
"""

content = patch_code + content
with open("tests/test_socket_server_stress.py", "w") as f:
    f.write(content)
