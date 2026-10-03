with open("tests/test_narrator_stress.py", "r") as f:
    content = f.read()

replacement = """
            threads = []
            for i in range(20):
                def run_play():
                    with patch.dict(os.environ, sandbox.env):
                        sink.play(wav)
                th = threading.Thread(target=run_play)
                threads.append(th)
                th.start()
            
            for th in threads:
                th.join()
            
            active = sandbox.spy_mpv.get_active_pids()
            self.assertEqual(active, [], f"Orphaned mpv processes found: {active}")
"""

start_idx = content.find("            threads = []")
end_idx = content.find("            self.assertEqual(active, [], f\"Orphaned mpv processes found: {active}\")")
if start_idx != -1 and end_idx != -1:
    content = content[:start_idx] + replacement.strip("\n") + "\n" + content[end_idx+len("            self.assertEqual(active, [], f\"Orphaned mpv processes found: {active}\")"):]

with open("tests/test_narrator_stress.py", "w") as f:
    f.write(content)
