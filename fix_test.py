import re
with open("tests/test_synthesize_endpoint.py", "r") as f:
    content = f.read()

old_block = """        with mock.patch.object(web_server, "JobTracker") as po_cls:
            po_cls.return_value.run.return_value = web_server.EXIT_OK
            try:
                r = client.post("/api/speak", json={"text": "long paste", "rewrite": True})
                assert r.status_code == 202
                assert started.wait(timeout=10), "rewrite never started"

                # While the rewrite is parked, synthesize must still complete.
                resp = client.post("/api/synthesize", json={"text": "quick selection"})
                assert resp.status_code == 200
                assert resp.data[:4] == b"RIFF"
            finally:
                release.set()
                # Drain the speak job INSIDE the patch so it finishes against
                # the stub pipeline, never the real cache/mpv.
                server._job_executor.shutdown(wait=True)  # noqa: SLF001
            # The stubbed pipeline must have been what ran.
            po_cls.return_value.run.assert_called_once()"""

new_block = """        with mock.patch.object(server._audio_sink, "play") as mock_play:
            try:
                r = client.post("/api/speak", json={"text": "long paste", "rewrite": True})
                assert r.status_code == 202
                assert started.wait(timeout=10), "rewrite never started"

                # While the rewrite is parked, synthesize must still complete.
                resp = client.post("/api/synthesize", json={"text": "quick selection"})
                assert resp.status_code == 200
                assert resp.data[:4] == b"RIFF"
            finally:
                release.set()
                # Drain the speak job INSIDE the patch so it finishes against
                # the stub pipeline, never the real cache/mpv.
                server._job_executor.shutdown(wait=True)  # noqa: SLF001
            # The stubbed pipeline must have been what ran.
            mock_play.assert_called_once()"""

content = content.replace(old_block, new_block)
with open("tests/test_synthesize_endpoint.py", "w") as f:
    f.write(content)
