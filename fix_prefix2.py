with open("plugin/scripts/python/narrator_mlx_summarizer.py", "r") as f:
    content = f.read()

# I need to swap:
#     # Markdown syntax
#     line = re.sub(r"^\d+[\.\)]\s+", "", line)
# ...
#     import re

content = content.replace('        # Markdown syntax\n    line = re.sub(r"^\\d+[\\.\\)]\\s+", "", line)', '    import re\n    # Markdown syntax\n    line = re.sub(r"^\\\\d+[\\\\.\\\\)]\\\\s+", "", line)')

# Oh wait, let's just do it with python!
import re

content = re.sub(r'    # Markdown syntax', '    import re\n    # Markdown syntax', content, count=1)

with open("plugin/scripts/python/narrator_mlx_summarizer.py", "w") as f:
    f.write(content)
