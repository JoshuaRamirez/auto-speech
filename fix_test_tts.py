with open("tests/test_synthesize_endpoint.py", "r") as f:
    content = f.read()

content = content.replace("server._tts.", "server._tts_executor.engine.")
content = content.replace("server._cache.", "server._routes._cache.")
content = content.replace("server._jobs.", "server._routes._jobs.")
content = content.replace("server._rewriter.", "server._routes._rewriter.")
content = content.replace("server._job_executor.", "server._routes._job_executor.")
content = content.replace("server._audio_sink", "server._routes._audio_sink")

with open("tests/test_synthesize_endpoint.py", "w") as f:
    f.write(content)
