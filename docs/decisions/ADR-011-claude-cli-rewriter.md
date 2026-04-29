# ADR-011 — Web rewriter via `claude -p` (CLI subprocess)

**Status:** Accepted
**Date:** 2026-04-29
**Depends on:** ADR-005 (audio-friendly rewrite via Claude),
ADR-008 (source-hash replay cache), ADR-010 (localhost web UI).

## Context
The web UI is pass-through today. Pasting markdown produces spoken
"asterisk asterisk", path slashes get pronounced literally, and code
fences become "backtick backtick backtick python." Quality is poor on
anything that wasn't already audio-friendly.

The slash command `/speak` solves this by having the in-session Claude
do a 12-rule rewrite as part of its own response. The web server has no
in-session Claude, so it needs another way to apply the same rewrite.

Three candidate paths were considered:

1. **Anthropic API call** (HTTPS to `api.anthropic.com`).
2. **Local LM via mlx-lm** (Qwen 2.5 14B Instruct or similar).
3. **Claude CLI subprocess** (`claude -p`).

## Decision

**Use the Claude CLI in `-p` (print) mode as the web server's rewriter.**

```
result = subprocess.run(
    ["claude", "-p", "--output-format", "text", "--allowed-tools", ""],
    input=PROMPT_TEMPLATE.format(source=text),
    capture_output=True, text=True, timeout=60, check=True,
)
audio_text = result.stdout.strip()
```

The 12-rule prompt is extracted into a shared file
(`plugin/prompts/audio_rewrite_prompt.txt`) so the slash command, the
CLI rewriter, and any future rewriter all read from a single source of
truth.

## Rationale

1. **Same Claude as `/speak`.** The CLI runs the same agent the user
   already authenticates against in their Claude Code session. Quality
   on the 12-rule rewrite is identical by construction — same model
   family, same prompt, same training.
2. **No API key management.** The CLI uses the user's existing Claude
   Code login. No `ANTHROPIC_API_KEY` env var. No secret storage. No
   separate billing.
3. **Stays inside the user's existing subscription.** The user already
   pays for Claude Code; this consumes some of that. The Anthropic API
   path would be a separate metered charge.
4. **No local model weights.** The local-LM path costs ~8 GB of disk,
   ~8 GB of RAM, and ~12 s of latency for measurably weaker
   instruction-following on the rewrite task. The CLI path is
   strictly better unless the user wants offline operation.
5. **Tool access can be denied.** Passing `--allowed-tools ""` reduces
   the agent to a pure text-in/text-out function — no file ops, no
   bash, no MCP servers. This both speeds startup and scopes the
   blast radius if the prompt ever did something unexpected.

## Tradeoffs accepted

- **CLI startup latency.** Every rewrite invocation pays the cost of
  spawning the agent runtime, loading settings, etc. Measured ~3–6 s
  cold on this machine, plus ~3–8 s of generation = **~6–14 s before
  TTS starts** on a fresh paste. The Anthropic API path would be
  ~700 ms total. We accept the slower path because (a) cached replays
  are unaffected (hash is on source, so a fresh paste only ever
  triggers one rewrite ever), and (b) the user explicitly preferred
  the no-API-key option.
- **Online dependency.** The CLI talks to Anthropic; the web server
  needs a working internet connection at rewrite time. Offline =
  rewrite fails, but we surface that loud (no silent fallback to
  pass-through). User who wants offline operation can disable the
  rewrite toggle in the UI; pass-through still works.
- **Plugin tax.** The CLI loads the user's `enabledPlugins`. We pass
  `--allowed-tools ""` to neutralize plugin tool surface, but plugin
  *load* still happens. Worth measuring; if startup is dominated by
  plugin load, a future ADR can add `--no-plugin-load` (or whatever
  flag becomes available) and depend on it.

## Cache-key implications

Two different audio paths now exist in the web server: rewrite-on and
rewrite-off (pass-through). Both could be invoked on the same source
text and produce **different audio**. The Phase 11 cache key
(`sha256(source || 0x00 || voice:speed)`) does not distinguish them
and would alias.

**Decision:** extend the cache-key derivation to include a "pipeline
mode" suffix:

- `/speak` slash command (which always rewrites) keeps producing the
  existing key shape — backwards compatible with already-promoted
  cache entries.
- Web server with rewrite ON produces the same key shape — slash
  command and web-rewrite share the cache for any given source.
- Web server with rewrite OFF (pass-through) appends `\x00passthrough`
  to the hashed input, producing a distinct key.

```
key_input_rewrite     = source || 0x00 || voice:speed
key_input_passthrough = source || 0x00 || voice:speed || 0x00 || "passthrough"
```

Existing cache entries continue to work; pass-through requests get
their own key space; no migration needed.

## Default UX

The web UI ships with **rewrite ON by default**. The rationale is that
"paste this and speak it" is overwhelmingly more useful when the
output is actually listenable. A user who already has audio-friendly
text — say, the rewrite from a prior `/speak` invocation — can
unchecking the toggle to skip rewrite and save the latency.

## Alternatives considered

- **Anthropic API directly.** Faster (~700 ms), but requires API key
  management, separate billing, and goes off the user's existing
  subscription. Rejected for v0.1; ADR-012 may revisit if CLI startup
  proves too slow in practice.
- **Local LM (Qwen 2.5 14B-4bit via mlx-lm).** ~8 GB RAM, weaker
  instruction following, ~12 s rewrite latency, no internet
  requirement. Rejected for v0.1 because the CLI path is strictly
  better when online; see Phase 15 placeholder if offline ever
  matters.
- **Rule-based stripper.** Adequate for prose, awkward for code-heavy
  content. Worth keeping as a Phase 14b option behind the same UI
  toggle if the CLI path ever becomes unavailable.

## Consequences

- New `ClaudeCliRewriter` class (L1 adapter — wraps a subprocess).
- New shared prompt file at `plugin/prompts/audio_rewrite_prompt.txt`.
- The slash command's `speak.md` keeps its inline prompt for now (it
  reads more naturally inline for a slash command body); a follow-on
  cleanup could pull it from the shared file with a templating step.
- `WebServer._handle_speak` gains a `rewrite` boolean; default true.
- Cache-key derivation gains a pipeline-mode suffix.
- UI gains an "Audio-friendly rewrite" checkbox.
- `setup/install.sh` adds a runtime check that `claude` is on PATH
  and emits a clear instruction if not. We do **not** auto-install
  Claude Code; that's the user's responsibility.

## Invariants

- **I-14.1 Single rewrite source-of-truth.** The 12-rule prompt lives
  in exactly one file. Every code path that rewrites reads from it.
- **I-14.2 Rewrite is opt-out, not opt-in.** Default is on; user can
  uncheck to skip it.
- **I-14.3 Distinct keys per pipeline mode.** rewrite-mode and
  passthrough-mode never collide in the cache for the same source.
- **I-14.4 Fail loud on rewriter error.** A failed CLI invocation
  (timeout, non-zero exit, empty output) returns an HTTP 500 with the
  underlying error. We do not silently fall back to pass-through.
