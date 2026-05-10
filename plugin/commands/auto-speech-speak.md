---
description: Speak a prior Claude response aloud (local TTS). Arg = N-th most recent (default 1).
argument-hint: "[n]"
allowed-tools: Bash
---

You are executing the `/speak` slash command for the `auto-speech` plugin.

## Argument
The user invoked `/speak $ARGUMENTS`. Parse the argument:
- If empty, unset, or whitespace-only → `ORDINAL=1`.
- Else, trim whitespace. If it's a positive integer → use that value as `ORDINAL`.
- Else → stop and respond to the user with a one-line error:
  "speak: argument must be a positive integer, got `<value>`". Do not proceed.

## Step 1 — Extract the source message and compute its cache key

Run this single Bash command, substituting `ORDINAL`. It writes the
extracted source text to a temp file and prints the cache-key hash to
stdout on the same invocation (the hash is derived from the source text
plus the active voice_id and speed).

```
set -euo pipefail
SRC_FILE=$(mktemp -t auto-speech-src-XXXX)
/Users/joshua/Developer/auto-speech/plugin/scripts/shell/run_extract.sh --ordinal ORDINAL > "$SRC_FILE"
HASH=$(/Users/joshua/Developer/auto-speech/plugin/scripts/shell/compute_hash.sh < "$SRC_FILE")
echo "SRC_FILE=$SRC_FILE"
echo "HASH=$HASH"
cat "$SRC_FILE"
```

If the first subcommand exits with code `2` ("no such turn") or code `3`
("no transcript"), stop and pass the stderr message to the user verbatim.
Do not proceed.

From the output, record three values:
- `SOURCE_HASH` — the 64-hex-char string after `HASH=`.
- `SRC_FILE` — the temp file path after `SRC_FILE=` (keep for step 4).
- `SOURCE_TEXT` — the full verbatim message text that follows those two lines.

## Step 2 — Rewrite for audio

Rewrite `SOURCE_TEXT` into an audio-friendly transcript following these rules
in order (higher rules win ties):

1. **Lossless content.** Every fact, number, proper noun, file path, URL,
   example, caveat, and named entity in the source must appear somewhere in
   your output. Restate in different words if needed, but never drop.

2. **Speakable prose only.** Plain text. No markdown, no bullet characters,
   no code fences, no headers, no tables. Sentences a human can read aloud
   naturally.

3. **Code blocks.** For each code block: state the language, describe what
   the code does in one or two sentences, quote only identifiers, short
   literals, or lines that a listener truly needs to hear verbatim. Do not
   read character-by-character.

4. **Symbols.** Replace ambiguous symbols with the word a speaker would use:
   `->` → "arrow", `=>` → "fat arrow", `*` as punctuation → "asterisk",
   `*` as multiplication → "times", `/` in paths → "slash", `|` → "pipe",
   `&&` → "and", `||` → "or", `==` → "equals equals", and so on.

5. **Paths and URLs.** Spell paths as a speaker would read them (for example,
   `/Users/joshua/Developer/auto-speech` becomes "slash Users slash joshua
   slash Developer slash auto-speech"). For long URLs, speak the domain and
   describe the path (for example, "a github dot com URL for the Kokoro
   model page").

6. **Tables.** Render each row as a sentence. For a table with columns Risk,
   Likelihood, Mitigation, say "First risk: install fails; likelihood medium;
   mitigation is to pin a known version. Second risk: ..."

7. **Lists.** Use ordinal words. "First, ... Second, ... Finally, ..." or
   "Three things matter. One, ... Two, ... Three, ...". Never emit bullet
   characters.

8. **Numbers and units.** Read them naturally. 1.5 → "one point five";
   "5 GB" → "five gigabytes"; "42%" → "forty-two percent".

9. **Acronyms.** Pronounce universally-lettered acronyms as letters (TTS →
   "T-T-S", API → "A-P-I") unless the context makes a spelled-out form
   clearer.

10. **Inline formatting.** Strip markdown bold/italic/code spans; keep the
    words themselves unchanged.

11. **No meta.** Do not announce the rewrite. Do not say "here is the
    audio-friendly version". Just produce the spoken form.

12. **Natural rhythm.** Prefer shorter sentences. Break compound sentences
    at semicolons and dashes. Insert explicit sentence boundaries where the
    source used newlines for pacing.

Hold the rewritten text as `AUDIO_TEXT`.

## Step 3 — Speak it

Run this Bash command, passing `AUDIO_TEXT` on stdin via a heredoc. Substitute
the actual rewritten text in place of `{AUDIO_TEXT}`, the ORDINAL value
in place of ORDINAL, and the `SOURCE_HASH` recorded in step 1:

```
/Users/joshua/Developer/auto-speech/plugin/scripts/shell/run_speak.sh --ordinal ORDINAL --source-hash SOURCE_HASH <<'__AUTO_SPEECH_EOF__'
{AUDIO_TEXT}
__AUTO_SPEECH_EOF__
```

Use the exact heredoc delimiter shown (`__AUTO_SPEECH_EOF__`) so the
rewritten text may safely contain any other token.

Passing `--source-hash` enables the replay cache: on a second invocation
of `/speak` for the same message, the cache hit skips both the rewrite
pass and the TTS pipeline entirely. On a miss (the first invocation),
the pipeline runs normally and the resulting audio is promoted into the
cache so the next time is instant.

## Step 4 — Report

When the Bash command returns:
- Exit 0: respond to the user with a single line: "spoke message #ORDINAL
  (N chars)" where N is the character count of `AUDIO_TEXT`.
- Any other exit: respond with "speak failed: exit <code>" and include the
  stderr output.

Do not output the source text, the rewritten text, or any explanation of
what you did beyond the single status line.
