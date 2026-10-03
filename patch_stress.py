import re

with open("tests/test_socket_server_stress.py", "r") as f:
    content = f.read()

# Replace any call to NarratorService(
# to pass tts_executor=mock_executor instead of synth=...
content = re.sub(
    r'synth=mock_synth,\s*engine=None,',
    r'tts_executor=tts_executor.TTSExecutor(mock_synth),',
    content
)
content = re.sub(
    r'synth=synth,\s*engine=None,',
    r'tts_executor=tts_executor.TTSExecutor(synth),',
    content
)

with open("tests/test_socket_server_stress.py", "w") as f:
    f.write(content)
