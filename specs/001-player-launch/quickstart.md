# Quickstart: validating the player launch

## Hermetic (always; no app, no sound)

```bash
nice -n 10 bash tests/run_all.sh
```

The suite sets `UI_UTILITY_SOCK` to its private dir, `UI_UTILITY_SILENT=1` and
`AUTO_SPEECH_PLAYER_LAUNCH=never`. Expected: `test_ui_utility_client.py` and the launcher tests in
`test_speech_history.py` pass. They cover the scenarios in [spec.md](spec.md): hand-over with
`hello` then `open` ([contracts/player-handover.md](contracts/player-handover.md)); off, held and
not installed send nothing; a hung, refusing or absent utility leaves `after_line` under 50 ms;
newest line wins; a dropped connection reconnects.

## Live (only on the Desk's word; makes a window, no sound)

1. The live narrator is restarted by the Desk onto the merged main.
2. `auto-speech history player-on`.
3. The next desk line is heard; the UI Utility's player shows it, ready to play, without taking
   focus. `auto-speech history status` says `player after each line: on (last: opened …)`.
4. `auto-speech history player-off` when done.
