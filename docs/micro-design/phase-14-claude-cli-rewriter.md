# Phase 14 Micro-Design — Claude CLI rewriter for the web app

Implements [ADR-011](../decisions/ADR-011-claude-cli-rewriter.md).

## Scope
1. Extract the 12-rule rewrite prompt into a shared file.
2. New `ClaudeCliRewriter` (L1 I/O adapter) that runs `claude -p` in
   subprocess and returns the rewritten text.
3. Wire the rewriter into `/api/speak` behind a request flag (default on).
4. Extend the cache key with a pipeline-mode suffix when the request is
   pass-through.
5. UI: add a checkbox "Audio-friendly rewrite (Claude CLI)" defaulting
   to checked.

## M1 — Classes at this level

- **`ClaudeCliRewriter`** (new, L1 adapter)

Edits:
- `WebServer` — `_handle_speak` accepts `{text, rewrite}`; pass-through
  branch suffixes the cache key; rewrite branch invokes the rewriter
  before pipeline.

## M2 — Semantics

### `ClaudeCliRewriter`
A subprocess wrapper. Stateless. One method:

- `rewrite(source_text: str, *, timeout_seconds: float = 60.0) -> str`
- Pre: `source_text` is non-empty after strip; `claude` binary is
  on PATH.
- Post: returns a non-empty plain-text rewrite stripped of leading
  and trailing whitespace.
- Raises `ClaudeCliUnavailable` if the binary isn't on PATH.
- Raises `ClaudeCliRewriteError` on non-zero exit, timeout, or empty
  stdout — wraps the underlying cause.

### Prompt template
File: `plugin/prompts/audio_rewrite_prompt.txt`. Format:

```
You are converting a prior assistant response into a spoken form ...
[12 rules here, verbatim from speak.md]
SOURCE:
>>>
{SOURCE}
<<<
```

A trivial Python helper loads it at server startup and replaces
`{SOURCE}` with the actual text. The same file is documented as the
canonical prompt; future rewriters (API, local LM) read from it too.

### Subprocess invocation
```python
subprocess.run(
    ["claude", "-p", "--output-format", "text", "--allowed-tools", ""],
    input=prompt,
    capture_output=True,
    text=True,
    timeout=timeout_seconds,
    check=True,
)
```
Notes:
- `-p` / `--print` mode: agent runs, prints the response, exits.
- `--output-format text`: no JSON wrapper, no agent metadata.
- `--allowed-tools ""`: no Bash, no Read, no MCP — pure text-in/text-out.
- Prompt comes via **stdin**, not a flag arg, so the source can contain
  any characters without quoting concerns.
- `check=True` so a non-zero exit raises `CalledProcessError`, caught
  in the rewriter and rewrapped.

## M3 — Relationships

```
WebServer._handle_speak
   │
   ├── if rewrite=true:
   │      ClaudeCliRewriter.rewrite(source) → AUDIO_TEXT
   │      cache_key = sha256(source || 0x00 || voice:speed)        ← shared with /speak
   │
   └── if rewrite=false:
          AUDIO_TEXT = source                                       (literal)
          cache_key = sha256(source || 0x00 || voice:speed || 0x00 || "passthrough")

   ↓
   CacheStore.lookup(cache_key) → hit? play; miss? pipeline(AUDIO_TEXT)
```

Note that the cache stores the **audio**, not the rewrite. The rewrite
text is ephemeral. So a cache hit on a rewrite-mode request plays the
audio without invoking the rewriter at all — consistent with the
existing Phase 11 behavior.

## M4 — Implementation details

### `claude_cli_rewriter.py`
```python
class ClaudeCliUnavailable(RuntimeError): ...
class ClaudeCliRewriteError(RuntimeError): ...

class ClaudeCliRewriter:
    def __init__(self, prompt_template: str, claude_bin: str = "claude") -> None:
        self._template = prompt_template
        self._bin = claude_bin

    def rewrite(self, source_text: str, *, timeout_seconds: float = 60.0) -> str:
        text = source_text.strip()
        if not text:
            raise ClaudeCliRewriteError("source_text is empty")
        if shutil.which(self._bin) is None:
            raise ClaudeCliUnavailable(
                f"{self._bin!r} not found on PATH. Install Claude Code to enable rewrite."
            )
        prompt = self._template.replace("{SOURCE}", text)
        try:
            result = subprocess.run(
                [self._bin, "-p", "--output-format", "text", "--allowed-tools", ""],
                input=prompt,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=True,
            )
        except subprocess.TimeoutExpired as exc:
            raise ClaudeCliRewriteError(f"rewrite timed out after {timeout_seconds}s") from exc
        except subprocess.CalledProcessError as exc:
            raise ClaudeCliRewriteError(
                f"claude exited {exc.returncode}: {exc.stderr.strip()[:500]}"
            ) from exc
        out = result.stdout.strip()
        if not out:
            raise ClaudeCliRewriteError("claude returned empty rewrite")
        return out
```

### `WebServer._handle_speak` shape after edits
```python
def _handle_speak(self):
    body = request.get_json(silent=True) or {}
    text = (body.get("text") or "").strip()
    rewrite_mode = bool(body.get("rewrite", True))   # default ON
    if not text: return jsonify({"error": "text is required"}), 400

    cache_key = self._compute_hash(text, mode="rewrite" if rewrite_mode else "passthrough")

    with self._lock:
        hit = self._cache.lookup(cache_key)
        if hit is not None:
            ... (play cached, return cache_hit)

        # MISS — produce AUDIO_TEXT
        if rewrite_mode:
            try:
                audio_text = self._rewriter.rewrite(text)
            except (ClaudeCliUnavailable, ClaudeCliRewriteError) as exc:
                return jsonify({"error": f"rewrite failed: {exc}"}), 500
        else:
            audio_text = text

        # MISS — run TTS pipeline on AUDIO_TEXT, with cache_key as source_hash
        orchestrator = PipelineOrchestrator(
            source_hash=cache_key,
            cache_root=_cache_root(),
            tts_engine=self._tts,
        )
        future = self._tts_executor.submit(orchestrator.run, audio_text)
        rc = future.result()
        ...
```

### Cache-key extension in `_compute_hash`
```python
def _compute_hash(self, text: str, mode: str = "rewrite") -> str:
    parts = [text.encode("utf-8"), b"\x00",
             f"{self._profile.voice_id}:{self._profile.speed}".encode("utf-8")]
    if mode != "rewrite":
        parts += [b"\x00", mode.encode("utf-8")]
    return hashlib.sha256(b"".join(parts)).hexdigest()
```

The default `mode="rewrite"` produces the existing key shape (no
suffix), preserving compatibility with cache entries created by
`/speak`.

### UI checkbox
A single labelled checkbox above the textarea, default checked.
Submitted as `{"rewrite": <bool>}` in the POST body. While rewriting,
the hint text shows "rewriting…" then "generating…" then the result.

## Failure modes

| Mode | Handling |
|---|---|
| `claude` not on PATH | 500 with install hint. |
| Rewrite times out | 500 with timeout note. |
| `claude -p` returns non-zero | 500 with truncated stderr. |
| Rewrite produces empty output | 500 with explicit message. |
| User toggles rewrite OFF and pastes plain prose | Pipeline runs on the literal text; cache key in pass-through space. |

We **do not** silently fall back from rewrite-on to pass-through on
rewriter failure. Per the standing rule: fail loud.

## Invariants (beyond ADR-011's)

- **I-14.5 Worker-thread containment.** The CLI subprocess is spawned
  from the web request handler thread (not the TTS executor) — no MLX
  state interaction. The TTS pipeline that runs after the rewrite still
  goes through the executor as before.
- **I-14.6 Prompt-template-from-file.** The rewriter is constructed
  with the prompt loaded from `plugin/prompts/audio_rewrite_prompt.txt`
  at server startup. Editing the file requires a server restart to
  take effect — documented.

## Check gate

1. **Setup-time check.** Server startup logs whether `claude` is on
   PATH. If absent, server still starts but rewrite endpoint returns
   500 on use.
2. **Rewrite ON, novel text.** POST `{text: <markdown sample>, rewrite: true}`.
   Server logs show `claude -p` invocation; output written to logs;
   pipeline runs on the rewritten text; audio plays cleanly through
   mpv. The rewrite measurably differs from the source (e.g., bullet
   characters absent in spoken audio).
3. **Rewrite ON, repeat.** Same payload. Cache hit; no `claude`
   invocation; audio plays.
4. **Rewrite OFF, novel text.** POST `{text: same, rewrite: false}`.
   Different cache key (passthrough suffix); pipeline runs on the
   literal text; audio plays the markdown verbatim.
5. **Rewrite OFF same text repeat.** Cache hit on the passthrough key.
6. **`claude` removed from PATH (simulated).** Rewrite-on request
   returns 500 with the install hint; pass-through requests still work.

## Out of scope
- Anthropic API rewriter (Phase 15 candidate if CLI startup is too slow).
- Local-LM rewriter (Phase 16 candidate if offline matters).
- A "third toggle position" for rule-based stripping (could be added later).
- Updating slash command `speak.md` to read from the shared prompt file
  (a tidy-up for a future micro-phase; not required for v0.1).
