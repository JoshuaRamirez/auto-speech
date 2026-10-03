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
                time.sleep(0.01)
                if i % 2 == 0:
                    sink.interrupt()
            
            # Wait for all threads to finish.
            # We must repeatedly call interrupt so that queued threads abort quickly
            while any(th.is_alive() for th in threads):
                sink.interrupt()
                time.sleep(0.05)
            
            for th in threads:
                th.join()
            
            active = sandbox.spy_mpv.get_active_pids()
"""

start_idx = content.find("            threads = []")
end_idx = content.find("            self.assertEqual(active, [], f\"Orphaned mpv processes found: {active}\")")
if start_idx != -1 and end_idx != -1:
    content = content[:start_idx] + replacement.strip("\n") + "\n            " + content[end_idx:]

with open("tests/test_narrator_stress.py", "w") as f:
    f.write(content)

