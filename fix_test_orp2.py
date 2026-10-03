import re
with open("tests/test_narrator_stress.py", "r") as f:
    content = f.read()

content = content.replace("            self.assertEqual(active, [], f\"Orphaned mpv processes found: {active}\")", "            self.assertEqual(active, [], f\"Orphaned mpv processes found: {active}\")")
# wait I will just rewrite it using sed.
