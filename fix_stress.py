with open("tests/test_socket_server_stress.py", "r") as f:
    content = f.read()
import re
content = re.sub(
    r'synth=[^,]+,\s*engine=None,?',
    r'tts_executor=tts_executor.TTSExecutor(),',
    content
)
content = re.sub(
    r'synth=mock_synth,\s*',
    r'tts_executor=tts_executor.TTSExecutor(mock_synth),',
    content
)
content = content.replace("synth=MagicMock(),", "tts_executor=MagicMock(),")
if "import tts_executor" not in content:
    content = content.replace("import narrator_service", "import tts_executor\nimport narrator_service")
with open("tests/test_socket_server_stress.py", "w") as f:
    f.write(content)
