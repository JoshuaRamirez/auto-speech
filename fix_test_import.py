with open("tests/test_synthesize_endpoint.py", "r") as f:
    content = f.read()
if "import tts_engine" not in content:
    content = content.replace("import web_server", "import web_server\nimport tts_engine")
with open("tests/test_synthesize_endpoint.py", "w") as f:
    f.write(content)
