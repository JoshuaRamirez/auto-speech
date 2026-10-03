with open("tests/test_socket_server_stress.py", "r") as f:
    content = f.read()

import re

# Insert mock tts_executor initialization after svc._queue_lock = threading.Lock() or similar
replacement = """        svc._queue_lock = threading.Lock()
        class MockExecutor:
            def submit(self, fn, *args, **kwargs): return fn(*args, **kwargs)
            def ensure_loaded(self): pass
            @property
            def synth(self): return getattr(svc, "_synth", None)
        svc._tts_executor = MockExecutor()"""

content = content.replace("        svc._queue_lock = threading.Lock()", replacement)

# Do the same for svc2
replacement2 = """        svc2._queue_lock = threading.Lock()
        class MockExecutor2:
            def submit(self, fn, *args, **kwargs): return fn(*args, **kwargs)
            def ensure_loaded(self): pass
            @property
            def synth(self): return getattr(svc2, "_synth", None)
        svc2._tts_executor = MockExecutor2()"""

content = content.replace("        svc2._queue_lock = threading.Lock()", replacement2)

with open("tests/test_socket_server_stress.py", "w") as f:
    f.write(content)
