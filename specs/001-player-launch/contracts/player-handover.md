# Contract use: what auto-speech sends the UI Utility, and what it reads back

The governing contract is the UI Utility's own, `~/Developer/UI-Utility/docs/external/CONTRACT.md`
v1 rev 1.1 (read only). This file names the subset auto-speech uses, so tests and review can check
against it.

## Requests sent (one JSON line each, `"v":1`, a `"req"` id `as-<n>`)

| When | Request | Reply needed |
|---|---|---|
| once per connection | `{"v":1,"cmd":"hello","req":…}` | `ok:true`, `contract:1`, `"player"` in `pieces` |
| each heard line | `{"v":1,"cmd":"open","req":…,"panel":"player","id":"auto-speech","clip":{"path":…},"text":{"words":[…]},"title":…,"play":false}` | `ok:true`, `id:"auto-speech"` |
| daemon shutdown | none: the connection is closed; the panel is left to the user | n/a |

Not used: `play`, `hold`, `seek`, `select`, `show`, `status`, `subscribe`, `close` (the user owns
the panel once it is open).

## Lines read
- A line with `"req"` equal to a pending request is its reply.
- A line with `"event"` is an event. `closed` and `error` are noted for the log; the rest are
  read and dropped.
- A line that is not a JSON object is logged once and dropped.

## Errors handled
`ok:false` with `error` in `bad-line | bad-version | unknown-cmd | missing | bad-value | no-panel`
→ outcome `refused`, detail `<error>: <detail>`, connection kept. No reply in 5 s → outcome
`timeout`, connection dropped. EOF or socket error → connection dropped, next line reconnects.

## Launch
- `ui-utility launch` (+ `--silent` when `UI_UTILITY_SILENT=1`) if on PATH, else
  `open -g -a "UI Utility"` (+ `--env UI_UTILITY_SILENT=1`, `--env UI_UTILITY_SOCK=<path>` when
  set). Never when `AUTO_SPEECH_PLAYER_LAUNCH=never`.
- Then connect, retrying for up to 10 s.
