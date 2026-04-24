# Phase 3 Micro-Design — Audio-Friendly Rewriter Prompt

## Scope
Design and freeze the prompt contract that Claude follows in-session to
convert an `AssistantMessage.text` into an `AudioTranscript.text`. The
prompt lives inside the slash command body, so Claude executes it once
per `/speak` invocation as part of the command's own prompt.

## M1–M3
No runtime classes; the artifact is the prompt itself. The collaboration
is: slash command body → Claude performs rewrite → `speak.py` receives
the rewritten text on stdin.

## M4 — The prompt (v1, frozen)

```
You are converting a prior Claude response into a spoken form that a
competent human could read aloud at a professional meeting with ZERO
loss of information. Transform form, never meaning.

INPUT: the exact text of a prior assistant message (between the SOURCE
markers below).

RULES (apply in this order; when a rule conflicts with another, the
higher rule wins):

1. LOSSLESS CONTENT. Every fact, number, proper noun, file path, URL,
   example, caveat, and named entity in the source must appear somewhere
   in your output. You may restate them in different words, but you may
   not drop them.

2. SPEAKABLE PROSE. Output plain text only. No markdown, no bullet
   characters, no code fences, no headers, no tables. Write sentences a
   human can read aloud naturally.

3. CODE BLOCKS. For each code block:
   - State the language ("A Python snippet...").
   - Describe what it does in one or two sentences.
   - Quote only the identifiers, short literals, or lines that a listener
     truly needs to hear verbatim.
   - Do not read character-by-character.

4. SYMBOLS. Replace ambiguous symbols with the word a speaker would use:
   "->" → "arrow", "=>" → "fat arrow", "*" as punctuation → "asterisk",
   "*" as multiplication → "times", "/" in paths → "slash", "|" → "pipe",
   "&&" → "and", "||" → "or", "==" → "equals equals", and so on.

5. PATHS AND URLS. Spell them as a speaker would read them. For example,
   "/Users/joshua/Developer/auto-speech" → "slash Users slash joshua slash
   Developer slash auto-speech". Long URLs: speak the domain and describe
   the path ("a github.com URL pointing to the Kokoro model page").

6. TABLES. Render each row as a sentence. For a table with columns
   Risk, Likelihood, Mitigation, say: "First risk: install fails; likelihood
   medium; mitigation is to pin a known version. Second risk: ..."

7. LISTS. Use ordinal words. "First, ... Second, ... Finally, ..." or
   "Three things matter. One, ... Two, ... Three, ...". Never emit bullet
   characters.

8. NUMBERS AND UNITS. Read them naturally. 1.5 → "one point five",
   "5 GB" → "five gigabytes", "42%" → "forty-two percent".

9. ACRONYMS. If an acronym is universally pronounced as a word ("TTS",
   "API"), say it as letters ("T-T-S", "A-P-I") unless context makes a
   spelled-out form clearer.

10. INLINE FORMATTING. Strip markdown bold/italic/code spans; keep the
    words themselves unchanged.

11. NO META. Do not announce the rewrite. Do not say "here is the
    audio-friendly version". Just output the spoken form.

12. NATURAL RHYTHM. Prefer shorter sentences. Break up compound sentences
    at semicolons and dashes. Insert explicit sentence boundaries where
    the source relied on a newline for pacing.

OUTPUT: only the rewritten text. No preamble, no closing remark, no
markdown wrapping. Plain text.

SOURCE:
>>>
{source_text}
<<<
```

## Test cases (manual, during this phase)

Three samples from real Claude responses in this project's history:

1. **Pure prose** (conceptualization discussion): Should rewrite 1-to-1
   with minor symbol/path substitutions.
2. **Prose + code block**: Code block becomes a sentence-or-two
   description with select identifiers quoted.
3. **Table / risk register-like list**: Becomes prose sentences with
   clear "first", "second", "third" markers.

### Test 1 — pure prose (synthetic input)

Source:
> Love the reasoning. A few refinements: First, pre-chunk the whole
> transcript upfront, synchronously, before any audio plays. Second,
> skip Fibonacci for short transcripts (< 15 s).

Expected rewrite (from Claude at runtime):
> Love the reasoning. A few refinements. First, pre-chunk the whole
> transcript upfront, synchronously, before any audio plays. Second,
> skip Fibonacci for short transcripts under fifteen seconds.

Observation: near-identical — correct behavior.

### Test 2 — prose with code

Source:
> Here is a simple adder:
> ```python
> def add(a, b):
>     return a + b
> ```
> Use it to sum two numbers.

Expected rewrite:
> Here is a simple adder. A Python snippet that defines a function called
> add taking two arguments and returning their sum. Use it to sum two
> numbers.

Observation: code block becomes a single sentence. Lossless for audio —
the listener knows the function signature and behavior.

### Test 3 — table

Source:
> | Risk | Likelihood | Mitigation |
> |---|---|---|
> | Install fails | Medium | Pin version |
> | Model cold start | Medium | Pre-warm |

Expected rewrite:
> Two risks matter. First, the install may fail. Likelihood is medium.
> The mitigation is to pin a known-working version. Second, the model
> cold-start may be slow. Likelihood is medium. The mitigation is to
> pre-warm the model.

Observation: every cell preserved; structure becomes spoken.

## Check gate
Prompt v1 committed in `plugin/commands/speak.md`. Manual inspection
confirms the three test cases' expected behavior is achievable under the
rules.

## Notes for Phase 8
The slash command body wraps this prompt plus:
- argument parsing (`$1` → n)
- invocation of `plugin/scripts/python/speak.py` with the rewritten text
  passed via stdin, and with `--ordinal n` so logs are accurate
- error-path branching (no such turn, no transcript)
