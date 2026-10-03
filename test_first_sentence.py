import sys
sys.path.insert(0, str('plugin/scripts/python'))
from narrator_mlx_summarizer import _first_sentence

print(repr(_first_sentence("ok")))
print(repr(_first_sentence('"quoted line"\\nrest')))
