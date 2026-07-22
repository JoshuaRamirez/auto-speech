# Security Policy

## Supported versions

| Version | Supported |
|---------|-----------|
| 0.1.x (latest release) | ✅ |
| anything older | ❌ |

Only the most recent release receives security fixes.

## Threat model

auto-speech is a **local-only** tool. Understanding its trust boundaries:

- **Web server**: the Flask app refuses to bind anything but loopback
  (`127.0.0.1` / `localhost` / `::1`). It is **unauthenticated by design** —
  it trusts every same-machine caller, like mpv's IPC socket does. Do not
  port-forward or reverse-proxy it. Browser cross-origin access is closed:
  CORS headers are reflected only for Chrome-extension origins, and
  ordinary web pages cannot complete a JSON preflight or read responses.
- **Claude Code hooks**: the installers edit `~/.claude/settings.json`
  (Stop hook for autoplay; optional narrator and SessionStart hooks). The
  optional bootstrap hook runs `uv sync` against the **committed lockfile**
  on session start — it never pulls source from the network. Supply-chain
  surface = the PyPI packages pinned in `uv.lock`.
- **Runtime downloads**: Kokoro-82M model weights (Apache-2.0) are fetched
  from Hugging Face on first synthesis; the spaCy `en_core_web_sm` model is
  fetched at install time. Installing the optional narrator
  (`/auto-speech-narrate-install`) additionally pulls `mlx-lm` from PyPI
  and an LLM (default: a 4-bit Qwen 3B) from Hugging Face. Nothing else
  is downloaded.
- **No secrets**: the tool stores no credentials and transmits nothing off
  the machine. All synthesis is local.

## Reporting a vulnerability

Use **GitHub private vulnerability reporting**: on the repository page, go
to **Security → Report a vulnerability**. Reports are acknowledged within
7 days.

Please do not open public issues for suspected vulnerabilities.
