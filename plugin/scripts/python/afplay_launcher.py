"""AfplayLauncher: spawn `afplay` for one WAV with cooperative cancellation."""
from __future__ import annotations

import subprocess
import threading
import time
from pathlib import Path


POLL_INTERVAL_SECONDS = 0.1


class AfplayLauncher:
    """Play one WAV via afplay; honor a stop_event by terminating early."""

    @staticmethod
    def play(wav_path: Path, stop_event: threading.Event) -> int:
        proc = subprocess.Popen(
            ["afplay", str(wav_path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        while True:
            ret = proc.poll()
            if ret is not None:
                break
            if stop_event.is_set():
                print("[afplay] stop_event set; terminating afplay")
                proc.terminate()
                try:
                    proc.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    proc.kill()
                ret = proc.returncode
                break
            time.sleep(POLL_INTERVAL_SECONDS)
        if ret != 0 and not stop_event.is_set():
            stderr = proc.stderr.read().decode("utf-8", "replace") if proc.stderr else ""
            print(f"[afplay] nonzero exit {ret} for {wav_path}: {stderr}")
        return ret
