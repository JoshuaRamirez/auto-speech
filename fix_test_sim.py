import re
with open("tests/test_socket_server_stress.py", "r") as f:
    content = f.read()

content = content.replace("svc._synth = FastSynth()", "svc._synth = FastSynth()\n        class MockExecutor3:\n            def submit(self, fn, *args, **kwargs): return fn(*args, **kwargs)\n            def ensure_loaded(self): pass\n            @property\n            def synth(self): return svc._synth\n        svc._tts_executor = MockExecutor3()")

with open("tests/test_socket_server_stress.py", "w") as f:
    f.write(content)
