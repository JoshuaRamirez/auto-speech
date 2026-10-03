with open("tests/test_narrator_stress.py", "r") as f:
    lines = f.readlines()

out = []
for line in lines:
    if "self.assertEqual(active, [], f\"Orphaned mpv processes found" in line:
        out.append("            self.assertEqual(active, [], f\"Orphaned mpv processes found: {active}\")\n")
    else:
        out.append(line)

with open("tests/test_narrator_stress.py", "w") as f:
    f.write("".join(out))
