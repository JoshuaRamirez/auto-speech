import re

with open("plugin/scripts/python/web_server.py", "r") as f:
    content = f.read()

# Add import NativeAudioSink if not there
if "from native_audio_sink import NativeAudioSink" not in content:
    content = content.replace("from narrator_summarizer import", "from native_audio_sink import NativeAudioSink\nfrom narrator_summarizer import")
    # Actually wait, I'll just put it near ResilientSynthesizer
    content = content.replace("from resilient_synthesizer import ResilientSynthesizer", "from native_audio_sink import NativeAudioSink\nfrom resilient_synthesizer import ResilientSynthesizer")

# Init self._audio_sink
if "self._audio_sink = NativeAudioSink()" not in content:
    content = content.replace("self._tts = TTSEngine()", "self._tts = TTSEngine()\n        self._audio_sink = NativeAudioSink()")

# Fix cache hit in _handle_speak
content = content.replace("self._mpv.start(wav_path)", "threading.Thread(target=self._audio_sink.play, args=(wav_path,), daemon=True).start()")

# Fix _run_speak_job
old_pipeline = """            try:
                rc = self._tts_executor.submit(lambda x: 0, audio_text).result()
            except Exception as exc:  # noqa: BLE001
                print(f"[web] job CRASH (pipeline): {exc!r}", file=sys.stderr)
                traceback.print_exc(file=sys.stderr)
                self._jobs.fail(f"pipeline crashed: {exc!r}")
                return

            if rc != 0:
                print(f"[web] job FAIL pipeline exit={rc}", file=sys.stderr)
                self._jobs.fail(f"pipeline exited with code {rc}")
                return

            self._jobs.transition(PHASE_HANDED_OFF)
            print("[web] job handed_off (mpv playing)", file=sys.stderr)"""

new_pipeline = """            try:
                wav_path = self._tts_executor.submit(self._synthesize_to_cache, audio_text, self._profile, source_hash).result()
            except Exception as exc:  # noqa: BLE001
                print(f"[web] job CRASH (pipeline): {exc!r}", file=sys.stderr)
                traceback.print_exc(file=sys.stderr)
                self._jobs.fail(f"pipeline crashed: {exc!r}")
                return

            self._jobs.transition(PHASE_HANDED_OFF)
            print(f"[web] job handed_off (playing {wav_path})", file=sys.stderr)
            
            try:
                self._audio_sink.play(wav_path)
            except Exception as exc:
                print(f"[web] playback failed: {exc}", file=sys.stderr)"""
content = content.replace(old_pipeline, new_pipeline)

# Fix API controls
old_controls = """    def _handle_pause(self):
        return self._send_mpv(["set_property", "pause", True])

    def _handle_resume(self):
        return self._send_mpv(["set_property", "pause", False])

    def _handle_restart(self):
        return self._send_mpv(["seek", 0, "absolute"])

    def _handle_end(self):
        if not False:
            return jsonify({"error": "no active session"}), 404
        try:
            {}
        except Exception as exc:
            return jsonify({"error": str(exc)}), 502
        return jsonify({"status": "ended"})

    def _handle_seek(self):
        body = request.get_json(silent=True) or {}
        target = _text_field(body, "target")
        if target is None:
            return jsonify({"error": "target must be a string"}), 400
        if not target:
            return jsonify({"error": "target is required"}), 400
        if not False:
            return jsonify({"error": "no active session"}), 404

        if target.lower() == "end":
            try:
                reply = getattr(["get_property", "duration"], "")
            except Exception as exc:
                return jsonify({"error": str(exc)}), 502
            duration = reply.get("data")
            if not isinstance(duration, (int, float)):
                return jsonify({"error": "mpv did not report duration"}), 502
            return self._send_mpv(["seek", max(0.0, float(duration) - 0.5), "absolute"])

        if target.startswith("+") or target.startswith("-"):
            try:
                offset = float(target)
            except ValueError:
                return jsonify({"error": f"bad relative target {target!r}"}), 400
            return self._send_mpv(["seek", offset, "relative"])

        try:
            absolute = float(target)
        except ValueError:
            return jsonify({"error": f"bad absolute target {target!r}"}), 400
        return self._send_mpv(["seek", absolute, "absolute"])

    def _handle_status(self):
        # Build the playback half first.
        if not False:
            payload = {"active": False}
        else:
            sock = ""
            try:
                time_pos = getattr(["get_property", "time-pos"], sock).get("data")
                duration = getattr(["get_property", "duration"], sock).get("data")
                paused = getattr(["get_property", "pause"], sock).get("data")
            except Exception:
                payload = {"active": False}
            else:
                wav_path = ""
                wav = wav_path.read_text(encoding="utf-8").strip() if wav_path.is_file() else ""
                payload = {
                    "active": True,
                    "paused": bool(paused) if paused is not None else False,
                    "position": float(time_pos) if isinstance(time_pos, (int, float)) else 0.0,
                    "duration": float(duration) if isinstance(duration, (int, float)) else 0.0,
                    "wav": wav,
                }
        # Phase 17: also report the current/last fire-and-forget job.
        payload["job"] = _job_to_dict(self._jobs.current())
        return jsonify(payload)

    # ----- helpers -----

    def _send_mpv(self, command: list):
        if not False:
            return jsonify({"error": "no active session"}), 404
        try:
            reply = {}
        except Exception as exc:
            return jsonify({"error": str(exc)}), 502
        err = reply.get("error")
        if err and err != "success":
            return jsonify({"error": err}), 502
        return jsonify({"status": "ok"})"""

new_controls = """    def _handle_pause(self):
        return jsonify({"error": "not supported by native audio sink"}), 501

    def _handle_resume(self):
        return jsonify({"error": "not supported by native audio sink"}), 501

    def _handle_restart(self):
        return jsonify({"error": "not supported by native audio sink"}), 501

    def _handle_end(self):
        self._audio_sink.interrupt()
        return jsonify({"status": "ended"})

    def _handle_seek(self):
        return jsonify({"error": "not supported by native audio sink"}), 501

    def _handle_status(self):
        payload = {
            "active": self._audio_sink.is_playing,
            "paused": False,
            "position": 0.0,
            "duration": 0.0,
            "wav": "",
        }
        # Phase 17: also report the current/last fire-and-forget job.
        payload["job"] = _job_to_dict(self._jobs.current())
        return jsonify(payload)"""

content = content.replace(old_controls, new_controls)
content = content.replace("shutting down (mpv keeps playing if active)", "shutting down")

with open("plugin/scripts/python/web_server.py", "w") as f:
    f.write(content)
