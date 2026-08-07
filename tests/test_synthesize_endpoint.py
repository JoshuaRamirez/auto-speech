"""Unit tests for the /api/synthesize endpoint (Chrome-extension backend).

Verifies the browser-facing synthesize path WITHOUT loading MLX: the
TTSEngine is stubbed to emit a tiny valid WAV. Pins:
  - 400 on empty text, 413 on oversize text
  - 200 audio/wav on success, with the hash header
  - second identical request is served from cache (no re-synthesis)
  - CORS reflected for extension origins ONLY; web origins get no ACAO
  - OPTIONS preflight returns 204

Runs under tests/run_all.sh (no pytest): full mode and the --web lane.
Needs Flask + numpy, so it is listed in NEEDS_DEPS (skipped hermetic).
"""
from __future__ import annotations

import sys
import tempfile
import wave
from pathlib import Path
from unittest import mock

SRC = Path(__file__).resolve().parents[1] / "plugin" / "scripts" / "python"
sys.path.insert(0, str(SRC))

# A syntactically-valid Chrome extension origin (32 chars of a-p).
EXT_ORIGIN = "chrome-extension://" + "a" * 32


def _write_tiny_wav(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(24000)
        wf.writeframes(b"\x00\x00" * 2400)  # 0.1 s of silence


def _make_server(tmp_path: Path):
    """Build a WebServer with MLX fully stubbed and cache under tmp_path."""
    import web_server

    synth_calls = {"n": 0}

    def fake_synthesize(text, profile, out_path):  # noqa: ANN001 — instance attr, no self
        synth_calls["n"] += 1
        _write_tiny_wav(Path(out_path))

    # Patch only construction (cache location, model prewarm, mpv). The
    # instance's synthesize is then replaced so it persists for the whole
    # test — the request path runs after this function returns.
    with mock.patch.object(web_server, "_cache_root", lambda: tmp_path / "cache"), \
        mock.patch("tts_engine.TTSEngine._ensure_loaded", lambda self: None), \
        mock.patch("web_server.MpvController"):
        server = web_server.WebServer()
    server._tts.synthesize = fake_synthesize  # noqa: SLF001
    return server, synth_calls


def test_empty_text_rejected() -> None:
    with tempfile.TemporaryDirectory() as td:
        server, _ = _make_server(Path(td))
        client = server._app.test_client()  # noqa: SLF001
        resp = client.post("/api/synthesize", json={"text": "   "})
        assert resp.status_code == 400


def test_oversize_text_rejected() -> None:
    with tempfile.TemporaryDirectory() as td:
        server, _ = _make_server(Path(td))
        client = server._app.test_client()  # noqa: SLF001
        resp = client.post("/api/synthesize", json={"text": "x" * 20001})
        assert resp.status_code == 413


def test_synthesize_returns_wav_and_caches() -> None:
    with tempfile.TemporaryDirectory() as td:
        server, synth_calls = _make_server(Path(td))
        client = server._app.test_client()  # noqa: SLF001

        r1 = client.post(
            "/api/synthesize",
            json={"text": "hello world"},
            headers={"Origin": EXT_ORIGIN},
        )
        assert r1.status_code == 200
        assert r1.mimetype == "audio/wav"
        assert r1.data[:4] == b"RIFF"
        assert r1.headers.get("X-Auto-Speech-Hash")
        assert r1.headers.get("Access-Control-Allow-Origin") == EXT_ORIGIN
        assert r1.headers.get("Vary") == "Origin"
        assert synth_calls["n"] == 1

        # Identical request → served from cache, no second synthesis.
        r2 = client.post("/api/synthesize", json={"text": "hello world"})
        assert r2.status_code == 200
        assert r2.data == r1.data
        assert synth_calls["n"] == 1


def test_no_speakable_content_returns_422() -> None:
    import tts_engine

    with tempfile.TemporaryDirectory() as td:
        server, _ = _make_server(Path(td))

        def raise_unspeakable(text, profile, out_path):  # noqa: ANN001
            raise tts_engine.TTSNoSpeakableContentError("no phonemes")

        server._tts.synthesize = raise_unspeakable  # noqa: SLF001
        client = server._app.test_client()  # noqa: SLF001
        resp = client.post("/api/synthesize", json={"text": "★ • #"})
        assert resp.status_code == 422
        assert resp.get_json()["error"] == "no speakable text"


def test_lang_code_for_voice() -> None:
    from tts_engine import _lang_code_for_voice

    assert _lang_code_for_voice("af_heart") == "a"
    assert _lang_code_for_voice("bm_george") == "b"
    assert _lang_code_for_voice("ef_dora") == "e"
    assert _lang_code_for_voice("zf_xiaoxiao") == "z"
    assert _lang_code_for_voice("") == "a"  # fallback
    assert _lang_code_for_voice("qq_unknown") == "a"  # unknown prefix → fallback


def test_split_span_granularity() -> None:
    # The split/retry logic now lives in the shared SpanSplitter +
    # ResilientSynthesizer collaborators so the CLI and autoplay paths get
    # the same recovery the web server had. See test_resilient_synthesizer.py.
    from span_splitter import SpanSplitter

    split = SpanSplitter().split
    assert split("One. Two. Three.") == ["One.", "Two.", "Three."]
    assert split("alpha, beta; gamma") == ["alpha", "beta", "gamma"]
    assert split("just four plain words") == ["just four", "plain words"]
    assert split("single") == ["single"]


def test_resilient_split_recovers_from_generate_fault() -> None:
    """A span that trips the generate bug is split finer until it succeeds."""
    import web_server

    with tempfile.TemporaryDirectory() as td:
        tmp_path = Path(td)
        server, _ = _make_server(tmp_path)

        # Simulate the mlx-audio bug: fail on any span longer than 3 words,
        # succeed (emit a tiny WAV) on shorter ones.
        def flaky(text, profile, out_path):  # noqa: ANN001
            if len(text.split()) > 3:
                raise web_server.TTSGenerationError("simulated broadcast bug")
            _write_tiny_wav(Path(out_path))

        server._tts.synthesize = flaky  # noqa: SLF001
        out = tmp_path / "chunk.wav"
        wavs = server._synth.synthesize_parts(  # noqa: SLF001
            "alpha beta gamma delta epsilon zeta eta", server._profile, out
        )
        assert len(wavs) >= 2
        for w in wavs:
            assert Path(w).exists()


def test_resilient_split_skips_unspeakable_leaf() -> None:
    """An unspeakable fragment is dropped, not fatal, when others speak."""
    import web_server

    with tempfile.TemporaryDirectory() as td:
        tmp_path = Path(td)
        server, _ = _make_server(tmp_path)

        def synth(text, profile, out_path):  # noqa: ANN001
            t = text.strip()
            if t == "★":
                raise web_server.TTSNoSpeakableContentError("no phonemes")
            if len(t.split()) > 1:
                raise web_server.TTSGenerationError("simulated broadcast bug")
            _write_tiny_wav(Path(out_path))

        server._tts.synthesize = synth  # noqa: SLF001
        out = tmp_path / "chunk.wav"
        # Splits to words; "hello" and "world" speak, lone "★" is skipped.
        wavs = server._synth.synthesize_parts(  # noqa: SLF001
            "hello ★ world", server._profile, out
        )
        assert len(wavs) == 2


def test_non_string_fields_are_client_errors_not_crashes() -> None:
    """JSON carries types; a wrong one is a 400, not a 500.

    {"text": 123} used to reach .strip() on an int, raising AttributeError
    deep in the handler — Flask reported a server fault for what is plainly
    a malformed request, and the traceback went to the server log.
    """
    with tempfile.TemporaryDirectory() as td:
        server, _ = _make_server(Path(td))
        client = server._app.test_client()  # noqa: SLF001

        cases = [
            ("/api/synthesize", {"text": 123}),
            ("/api/synthesize", {"text": "hi", "voice": 7}),
            ("/api/speak", {"text": ["a", "b"]}),
            ("/api/speak", {"text": "hi", "rewrite_timeout_s": "soon"}),
            ("/api/replay", {"hash": 42}),
            ("/api/seek", {"target": 5}),
        ]
        for route, body in cases:
            resp = client.post(route, json=body)
            assert resp.status_code == 400, (
                f"{route} {body} returned {resp.status_code}, expected 400"
            )


def test_options_preflight() -> None:
    with tempfile.TemporaryDirectory() as td:
        server, _ = _make_server(Path(td))
        client = server._app.test_client()  # noqa: SLF001
        resp = client.open(
            "/api/synthesize", method="OPTIONS", headers={"Origin": EXT_ORIGIN}
        )
        assert resp.status_code == 204
        assert resp.headers.get("Access-Control-Allow-Origin") == EXT_ORIGIN


def test_synthesize_not_blocked_by_inflight_rewrite() -> None:
    """A long /api/speak rewrite must not stall /api/synthesize.

    The rewrite runs on the speak-job thread; only the pipeline occupies
    the TTS worker. Pin: with a rewrite blocked mid-flight, a synthesize
    request completes.

    The whole flow — including the drain in `finally` — runs with
    PipelineOrchestrator patched out. Without that, the drained speak job
    would run the REAL pipeline: write into the live replay cache
    (config/cache/), spawn a real mpv, and potentially wait minutes on an
    active playback session."""
    import threading

    import web_server

    with tempfile.TemporaryDirectory() as td:
        server, _ = _make_server(Path(td))
        client = server._app.test_client()  # noqa: SLF001

        release = threading.Event()
        started = threading.Event()

        def slow_rewrite(text, timeout_seconds=600.0):  # noqa: ANN001, ARG001
            started.set()
            release.wait(timeout=30)
            return text

        server._rewriter.rewrite = slow_rewrite  # noqa: SLF001
        server._rewriter.is_available = lambda: True  # noqa: SLF001

        with mock.patch.object(web_server, "PipelineOrchestrator") as po_cls:
            po_cls.return_value.run.return_value = web_server.EXIT_OK
            try:
                r = client.post(
                    "/api/speak", json={"text": "long paste", "rewrite": True}
                )
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
            po_cls.return_value.run.assert_called_once()


def test_cors_denied_for_web_and_absent_origins() -> None:
    """Non-extension origins get NO ACAO header — pages can't read responses."""
    with tempfile.TemporaryDirectory() as td:
        server, _ = _make_server(Path(td))
        client = server._app.test_client()  # noqa: SLF001

        # A regular web origin: response carries no CORS grant.
        r_web = client.post(
            "/api/synthesize",
            json={"text": "hi"},
            headers={"Origin": "https://evil.example"},
        )
        assert "Access-Control-Allow-Origin" not in r_web.headers

        # No Origin at all (same-origin web-UI fetches): also no CORS grant.
        r_none = client.post("/api/synthesize", json={"text": "hi"})
        assert "Access-Control-Allow-Origin" not in r_none.headers

        # Near-miss origins: wrong alphabet (q) and wrong length (31).
        for bad in ("chrome-extension://" + "q" * 32, "chrome-extension://" + "a" * 31):
            r_bad = client.post(
                "/api/synthesize", json={"text": "hi"}, headers={"Origin": bad}
            )
            assert "Access-Control-Allow-Origin" not in r_bad.headers


def main() -> int:
    tests = [
        test_empty_text_rejected,
        test_oversize_text_rejected,
        test_synthesize_returns_wav_and_caches,
        test_no_speakable_content_returns_422,
        test_lang_code_for_voice,
        test_split_span_granularity,
        test_resilient_split_recovers_from_generate_fault,
        test_resilient_split_skips_unspeakable_leaf,
        test_non_string_fields_are_client_errors_not_crashes,
        test_options_preflight,
        test_synthesize_not_blocked_by_inflight_rewrite,
        test_cors_denied_for_web_and_absent_origins,
    ]
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
    print(f"/api/synthesize endpoint: {len(tests)} tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
