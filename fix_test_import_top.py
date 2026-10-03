import re
with open("tests/test_synthesize_endpoint.py", "r") as f:
    content = f.read()

# Remove all local "import web_server, tts_engine" and just put them at the top after sys.path
content = content.replace("import web_server, tts_engine", "import web_server")

# Find sys.path.insert
match = re.search(r'sys\.path\.insert\(0, str\([^\)]+\)\)', content)
if match:
    insert_pos = match.end()
    content = content[:insert_pos] + "\nimport web_server\nimport tts_engine\n" + content[insert_pos:]

with open("tests/test_synthesize_endpoint.py", "w") as f:
    f.write(content)
