with open("tests/test_narrator_stress.py", "r") as f:
    content = f.read()

# Replace:
#                 th = threading.Thread(
#                     target=lambda: (
#                         patch.dict(os.environ, sandbox.env),
#                         sink.play(wav),
#                     )
#                 )
# with a proper def
import re

replacement = """
                def run_play():
                    with patch.dict(os.environ, sandbox.env):
                        sink.play(wav)
                th = threading.Thread(target=run_play)
"""

content = re.sub(
    r'                th = threading\.Thread\(\n                    target=lambda: \(\n                        patch\.dict\(os\.environ, sandbox\.env\),\n                        sink\.play\(wav\),\n                    \)\n                \)',
    replacement.strip("\n"),
    content
)

with open("tests/test_narrator_stress.py", "w") as f:
    f.write(content)

