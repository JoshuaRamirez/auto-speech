import re

with open("tests/test_socket_server_stress.py", "r") as f:
    content = f.read()

content = content.replace("synth=MagicMock(),", "tts_executor=MagicMock(),")
content = content.replace("from unittest.mock import MagicMock", "from unittest.mock import MagicMock\nimport tts_executor")

with open("tests/test_socket_server_stress.py", "w") as f:
    f.write(content)

