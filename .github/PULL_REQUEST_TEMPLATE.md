## Summary

<!-- What does this change and why? -->

## How tested

<!-- e.g. `bash tests/run_all.sh` (full + --hermetic + --web); manual audible
     check if playback behavior changed -->

## Checklist

- [ ] `uvx ruff check plugin/scripts/python tests` clean
- [ ] `shellcheck -S warning plugin/scripts/shell/*.sh setup/*.sh tests/*.sh` clean
- [ ] `uv lock --check` (if dependencies changed)
- [ ] Docs updated (README / OPERATIONS / CONTRIBUTING) if behavior changed
