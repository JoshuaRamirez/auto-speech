with open("plugin/scripts/python/narrator_mlx_summarizer.py", "r") as f:
    content = f.read()

# We need to extract the prefix stripping block and move it up.
import re

block = """    # Markdown syntax
    line = re.sub(r"^\d+[\.\)]\s+", "", line)
    line = re.sub(r"[*_`]", "", line)
    for prefix in ('"', "'", "* ", "- "):
        if line.startswith(prefix):
            line = line[len(prefix) :].lstrip()
    if line.endswith('"') or line.endswith("'"):
        line = line[:-1].rstrip()
    line = line.rstrip(".")"""

content = content.replace(block + "\n", "")

# Insert it right after:     line = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")

insertion = """    line = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")

""" + block + "\n"

content = content.replace('    line = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")\n', insertion)

with open("plugin/scripts/python/narrator_mlx_summarizer.py", "w") as f:
    f.write(content)
