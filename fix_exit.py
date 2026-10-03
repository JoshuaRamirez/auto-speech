with open("plugin/scripts/python/web_server.py", "r") as f:
    content = f.read()

content = content.replace("EXIT_OK = 0\n", "")
content = content.replace("from __future__ import annotations", "from __future__ import annotations\n\nEXIT_OK = 0")
with open("plugin/scripts/python/web_server.py", "w") as f:
    f.write(content)
