---
description: Reconcile the auto-speech venv with the committed lock (uv sync). Does NOT git-pull — update source yourself, then run this.
allowed-tools: Bash
---

You are executing `/auto-speech-update` for the auto-speech plugin.

This brings the project venv in sync with the committed `uv.lock` (running
`uv sync`, keeping the `narrate` extra if mlx-lm is already installed). It
does **not** run `git pull` — pull source changes yourself first if you want
them, then run this to reconcile dependencies. Safe to run anytime; a no-op
when already in sync.

Run this single Bash command and respond with its output verbatim inside a
fenced code block:

```
bash /Users/joshua/Developer/auto-speech/setup/bootstrap.sh --force
```

If the output ends with `sync complete`, confirm in one line that the venv is
now reconciled with the lock. If it reports a failure, surface the tail of
`/tmp/auto-speech-sync.log` and the most likely cause (e.g. network, or a
darwin-only wheel on a non-macOS host).
