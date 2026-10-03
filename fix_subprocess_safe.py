import re

with open("tests/test_socket_server_stress.py", "r") as f:
    content = f.read()

content = content.replace("synth=MagicMock(),", "tts_executor=MagicMock(),")

# Insert import tts_executor at the top, cleanly
content = content.replace("import narrator_service", "import tts_executor\nimport narrator_service")

with open("tests/test_socket_server_stress.py", "w") as f:
    f.write(content)

