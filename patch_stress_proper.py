import re
import glob

for path in glob.glob("tests/*stress*.py"):
    with open(path, "r") as f:
        content = f.read()

    # Change tts_executor=tts_executor.TTSExecutor(...) to tts_executor=TTSExecutor(synth=...)
    # Ensure from tts_executor import TTSExecutor is added
    if "from tts_executor import TTSExecutor" not in content:
        content = content.replace("from narrator_service import", "from tts_executor import TTSExecutor\nfrom narrator_service import")

    content = re.sub(
        r'tts_executor=tts_executor\.TTSExecutor\(([^)]+)\),',
        r'tts_executor=TTSExecutor(synth=\1),',
        content
    )
    content = content.replace("tts_executor=tts_executor.TTSExecutor(),", "tts_executor=TTSExecutor(),")

    with open(path, "w") as f:
        f.write(content)

