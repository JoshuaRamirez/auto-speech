import re
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
                time.sleep(0.02)
                if i % 2 == 0:
                    sink.interrupt()
            
            for th in threads:
                sink.interrupt() # unblock everything
                th.join(timeout=2.0)
            
            active = sandbox.spy_mpv.get_active_pids()
"""

# replace the block
start_idx = content.find("            for i in range(20):")
end_idx = content.find("            self.assertEqual(active, [], f\"Orphaned mpv processes found: {active}\")")
if start_idx != -1 and end_idx != -1:
    content = content[:start_idx] + replacement.strip("\n") + "\n            " + content[end_idx:]

with open("tests/test_narrator_stress.py", "w") as f:
    f.write(content)
