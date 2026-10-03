with open("tests/test_socket_server_stress.py", "r") as f:
    content = f.read()

content = content.replace('class MockExecutor:\n    def __init__(self, synth):\n        self.synth = synth\n    def submit(self, fn, *args, **kwargs): return fn(*args, **kwargs)\n    def ensure_loaded(self): pass\n\nimport tts_executor\ntts_executor.TTSExecutor = MockExecutor\n\n', "")

with open("tests/test_socket_server_stress.py", "w") as f:
    f.write(content)
