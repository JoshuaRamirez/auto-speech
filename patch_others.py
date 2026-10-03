import re, glob
for path in glob.glob("tests/*stress*.py"):
    with open(path, "r") as f:
        content = f.read()
    
    # Same sub
    content = re.sub(
        r'synth=[^,]+,\s*engine=None,?',
        r'tts_executor=tts_executor.TTSExecutor(),',
        content
    )
    # Just in case some had synth=mock_synth without engine=None
    content = re.sub(
        r'synth=mock_synth,\s*',
        r'tts_executor=tts_executor.TTSExecutor(mock_synth),',
        content
    )
    with open(path, "w") as f:
        f.write(content)
