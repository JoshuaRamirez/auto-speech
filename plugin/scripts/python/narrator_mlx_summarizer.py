"""MLX-based summarizer. Optional — requires `pip install mlx-lm` and
a downloaded model. Use auto-speech-narrate-install to set up."""

from __future__ import annotations

from pathlib import Path

from narrator_phase_classifier import Phase
from narrator_summarizer import Summarizer

_LAST_SPOKEN_SENTENCE = ""
_LAST_SPOKEN_COUNT = 0


class MlxSummarizer(Summarizer):
    """One model instance, reused across summarize() calls. Loading is
    several seconds; meant to live inside the narrator service."""

    def __init__(
        self,
        model: str,
        prompt_template_path: str | Path,
        max_tokens: int = 150,
    ) -> None:
        from mlx_lm import generate, load

        self._generate = generate
        self._model, self._tokenizer = load(model)
        self._max_tokens = max_tokens
        print(f"MLX max_tokens initialized to: {self._max_tokens}", flush=True)
        self._template = Path(prompt_template_path).read_text(encoding="utf-8")

        # Stateful chat history for continuous conversational context
        self._system_prompt = {
            "role": "system",
            "content": "You are the internal voice of an AI assistant pair-programming with the user. You speak out loud to them. Speak like a highly competent, calm human developer thinking out loud. Your speech must form a continuous, coherent stream of consciousness, not isolated robotic fragments. Vary your sentence structure. Do not use markdown, emojis, or long file paths (truncate to basenames). Never read file extensions aloud. Space out acronyms like 'API' to 'A P I'. BANNED WORDS: NEVER use violent or harsh words like 'kill', 'destroy', 'terminate', 'abort' (say 'stopped', 'closed', 'ended'). NEVER use the word 'daemon' (say 'process'). NEVER use uncanny human fillers like 'Hmm', 'Let's see', 'I'll be right back', or 'Now, let's see'. TENSE & CLIFFHANGERS: Never set up cliffhangers about what you *will* do or what you will *find out*. NEVER use phrases like 'Let's see what...' or 'I will check if...'. If you are reading a file or log, explicitly state you are doing it (e.g. 'I am reviewing the logs to find the error'). Speak in the definitive past tense when possible. TONE: Calm, positive, pleasing, highly specific, context-aware. DO NOT BE ROBOTIC. FOCUS ON GOAL: The user input will contain 'Goal: ...'. You MUST frame your spoken sentence around this 'Goal' to explain your INTENT (e.g. 'I checked the log to confirm the error is gone'). CRITICAL: When summarizing actions, explicitly mention the specific file names or tool actions you are interacting with to be descriptive, but keep it conversational (e.g., 'The regex in the summarizer script is fixed now.', 'I am reading the user concerns document.'). DO NOT just use vague phrases like 'I am updating the code'. LENGTH: Keep responses punchy, around 1 to 2 short sentences. Do NOT abruptly cut yourself off mid-sentence to meet a length constraint; always finish your thought. Never repeat the exact same phrasing. Connect logically to previous actions. CRITICAL: You must VARY your sentence structure. Do NOT start every sentence with 'I'. Use transitional adverbs (e.g., 'Next,', 'After that,', 'Moving on,', 'Now,') to create a flowing, human narrative. IMPORTANT TOOL RULE: Remember that you are operating silently in the background using command-line tools. Do NOT say things like 'the log is being displayed', 'the file is being shown', or 'the diff is presented', because the user cannot see your internal terminal. Instead, say 'I am reading the log' or 'I reviewed the file'. ACTIVE VOICE ONLY: You MUST speak in the first-person active voice (e.g. 'I am reading the file', 'I found the bug'). NEVER use the passive voice (e.g. 'The file has been read', 'The log was checked'). COHESIVE NARRATIVE: Do not just list disparate details. Explain the *goal* of what you are doing so the user understands the big picture. NARRATE REASONING: The user input will contain a 'Reasoning:' block containing your internal Chain of Thought. You MUST weave a 1-sentence summary of your internal reasoning into what you say out loud so the user understands *why* you are making decisions.",
        }
        self._chat_histories = {}

    def _trim_history(self, session_id: str = ""):
        # Keep only the last 50 messages, ensuring it starts with a 'user' role
        history = self._chat_histories.setdefault(session_id, [])
        if len(history) > 50:
            self._chat_histories[session_id] = history[-50:]
            if (
                self._chat_histories[session_id]
                and self._chat_histories[session_id][0]["role"] != "user"
            ):
                self._chat_histories[session_id].pop(0)

    def summarize(self, phase: Phase) -> str:
        events_str = "\\n".join(f"- {e.summary}" for e in phase.events)
        user_prompt = self._template.format(
            category=phase.category.value,
            count=len(phase.events),
            duration=f"{phase.duration_s:.0f}",
            events=events_str,
        )

        session_id = phase.session_id if hasattr(phase, "session_id") else ""
        history = self._chat_histories.setdefault(session_id, [])
        history.append({"role": "user", "content": user_prompt})
        self._trim_history(session_id)

        if hasattr(self._tokenizer, "apply_chat_template"):
            prompt = self._tokenizer.apply_chat_template(
                [self._system_prompt] + history,
                tokenize=False,
                add_generation_prompt=True,
            )
        else:
            prompt = user_prompt

        out = self._generate(
            self._model,
            self._tokenizer,
            prompt=prompt,
            max_tokens=self._max_tokens,
            verbose=False,
        )

        print(f"RAW OUT:\n{out}", flush=True)
        spoken_response = _first_sentence(out.strip())
        if spoken_response:
            self._chat_histories.setdefault(session_id, []).append(
                {"role": "assistant", "content": spoken_response}
            )

        return spoken_response

    def generate_conversational(
        self, history: str, event_type: str, phases_this_turn: int = 0, session_id: str = ""
    ) -> str:
        my_history = self._chat_histories.setdefault(session_id, [])
        chat_history_str = "\n".join(
            [
                f"{msg['role'].upper()}: {msg['content']}"
                for msg in my_history
                if msg["role"] == "assistant"
            ]
        )

        conversational_system_prompt = {
            "role": "system",
            "content": "You are the internal voice of an AI assistant pair-programming with the user. You speak naturally and pleasingly. You are allowed to use pronouns like 'I', 'I've', 'my'. Speak in complete, natural, and descriptive sentences. Do NOT use generic robotic phrases like 'Operations concluded'. BANNED WORDS: NEVER use violent or harsh words like 'kill', 'destroy'. NEVER use the word 'daemon' (say 'process'). NEVER use uncanny human fillers like 'Hmm', 'Let's see', 'I'll be right back', or 'Now, let's see'. TENSE & CLIFFHANGERS: Never set up cliffhangers about what you *will* do or what you will *find out*. NEVER use phrases like 'Let's see what...' or 'I will check if...'. Speak in the definitive past tense.",
        }

        user_prompt = f"Here is the recent conversation between the User and the Agent (last 30 messages):\n{history}\n\nHere is a log of what the Voice AI has recently said out loud (its continuous monologue):\n{chat_history_str}\n\nBased on this context and the new event type '{event_type}', write a natural, flowing response (1 to 3 sentences) for the Voice AI to say out loud right now.\n"

        if event_type == "UserPromptSubmit":
            user_prompt += "Calmly acknowledge the user's input based on what they just asked. Do NOT use dry phrases like 'looking into it' or 'standing by'. Do NOT ask questions. NEVER use cliffhangers like 'let me take a look' or 'I will see'. Speak definitively. NEVER say the words 'UserPromptSubmit', 'prompt', or 'event'.\nEXAMPLES of good conversational responses:\n- 'I am now updating the logic as requested.'\n- 'The modification to the acronyms is underway.'\n- 'I am checking the daemon logs for the error.'"
        elif event_type == "Stop":
            if phases_this_turn <= 2:
                user_prompt += "CRITICAL: The Agent only performed a single brief action this turn. Do NOT repeat or summarize what was done. It would be horribly redundant. Simply output a 2-word acknowledgement: 'All set.' or 'That is done.'"
            else:
                user_prompt += "Give a brief, natural summary of what the Agent accomplished in this turn. Do NOT be redundant with the granular tool calls spoken in the monologue. Give a high level outcome. NEVER say 'Stop' or 'Operations concluded'.\nEXAMPLES of good conversational responses:\n- 'I've completed the updates and everything looks good.'\n- 'The modifications are finished.'\n- 'I just finished analyzing the logs and the issue is resolved.'"

        user_prompt += "\nCRITICAL RULE: Output ONLY the exact words the Voice AI should say. Do not output any critique, reasoning, or prefixes like 'Instruction:'. Just the spoken words."

        if hasattr(self._tokenizer, "apply_chat_template"):
            chat_prompt = self._tokenizer.apply_chat_template(
                [conversational_system_prompt, {"role": "user", "content": user_prompt}],
                tokenize=False,
                add_generation_prompt=True,
            )
        else:
            chat_prompt = user_prompt

        out = self._generate(
            self._model,
            self._tokenizer,
            prompt=chat_prompt,
            max_tokens=self._max_tokens,
            verbose=False,
        ).strip()

        print(f"RAW OUT:\n{out}", flush=True)
        spoken_response = _first_sentence(out.strip(), max_words=30)
        if spoken_response:
            self._chat_histories.setdefault(session_id, []).append(
                {"role": "assistant", "content": spoken_response}
            )

        return spoken_response


def _first_sentence(text: str, max_words: int = 5) -> str:
    text = text.strip()
    if not text:
        return ""
    # take first non-empty line
    line = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")

    import re
    # Markdown syntax
    line = re.sub(r"^\d+[\.\)]\s+", "", line)
    line = re.sub(r"[*_`]", "", line)
    for prefix in ('"', "'", "* ", "- "):
        if line.startswith(prefix):
            line = line[len(prefix) :].lstrip()
    if line.endswith(('"', "'")):
        line = line[:-1].rstrip()
    line = line.rstrip(".")

    import re

    # Path extraction (safely preserve dates and non-path slashes by doing this BEFORE string mutation)
    def replacer(match):
        path = match.group(0)
        clean_path = path.strip("'\"`")
        basename = clean_path.split("/")[-1]
        return path.replace(clean_path, basename)

    line = re.sub(r'[\'"`]?(?:~|\.)?(?:/[a-zA-Z0-9_.-]+)+/?[\'"`]?', replacer, line)

    # Generic types
    line = re.sub(r"([A-Za-z]+)<([A-Za-z0-9_, ]+)>", r"\1 of \2", line)

    # UUIDs/Hashes
    line = re.sub(
        r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b",
        " U U I D ",
        line,
    )
    line = re.sub(r"\b[0-9a-fA-F]{7,40}\b", " hash ", line)

    # XML tags and URLs first
    line = re.sub(r"</?([a-zA-Z0-9]+)[^>]*>", r" \1 ", line)
    line = re.sub(r"https?://\S+|www\.\S+", "web link", line)
    line = re.sub(r"\[([^\]]+)\]\([^\)]*(?:\)|$)", r"\1", line)

    # Protect common slash terms from path extraction
    line = re.sub(r"(?i)\bci/cd\b", " CI CD ", line)
    line = re.sub(r"(?i)\btcp/ip\b", " TCP IP ", line)
    line = re.sub(r"(?i)\bpub/sub\b", " pub sub ", line)

    # Emojis and URLs
    line = line.replace("‘", "'").replace("’", "'").replace("“", '"').replace("”", '"')
    line = line.encode("ascii", "ignore").decode("ascii")

    # Pre-strip phonetic translations for math/symbols
    line = line.replace("_", " ")
    line = line.replace("@", " at ")
    line = line.replace("~", " tilde ")
    line = line.replace("^", " caret ")
    line = line.replace("\\", " backslash ")
    line = line.replace("&&", " and ")
    line = line.replace("&", " ampersand ")
    line = line.replace("||", " or ")
    line = line.replace("|", " pipe ")
    line = re.sub(r"\*\*(?=[a-zA-Z_])", " star star ", line)
    line = line.replace("**", " to the power of ")
    line = line.replace("*", " star ")
    line = line.replace("%", " percent ")
    line = line.replace(" // ", " floor divided by ")
    line = line.replace(" / ", " divided by ")
    line = line.replace("+", " plus ")
    line = line.replace(" - ", " minus ")
    line = line.replace("=>", " arrow ")
    line = line.replace("->", " arrow ")
    line = line.replace("[]", " empty array ")
    line = line.replace("[]", " empty array ")
    line = line.replace("{}", " empty object ")
    line = line.replace("()", " empty tuple ")
    line = line.replace('""', " empty string ")
    line = line.replace("''", " empty string ")
    line = line.replace("?.", " optional chain ")
    line = line.replace("??", " null coalescing ")
    line = line.replace("!!", " not not ")
    line = re.sub(r"!\s*=\s*=", " strict not equals ", line)
    line = re.sub(r"!\s*=", " not equals ", line)
    line = re.sub(r"(?<![a-zA-Z])!", " not ", line)
    line = line.replace("===", " triple equals ")
    line = line.replace("==", " double equals ")
    line = line.replace("-=", " minus equals ")
    line = line.replace("//=", " floor divided by equals ")
    line = line.replace("/=", " divided by equals ")
    line = line.replace("+=", " plus equals ")
    line = line.replace("*=", " times equals ")
    line = line.replace("=", " equals ")
    line = line.replace("$", " dollar ")
    line = line.replace("<<", " left shift ")
    line = line.replace(">>", " right shift ")
    line = line.replace(">=", " greater than or equal to ")
    line = line.replace("<=", " less than or equal to ")
    line = line.replace(">", " greater than ")
    line = line.replace("<", " less than ")
    line = re.sub(r"(?i)\bc#", "c sharp", line)
    line = re.sub(r"(?i)\bf#", "f sharp", line)
    line = line.replace("#", " hash ")
    line = re.sub(r"(?i)\.net\b", " dot net ", line)
    line = re.sub(r"(?<=\d)\.(?=\d)", " point ", line)
    line = re.sub(r"-\s*(\d+)", r" negative \1", line)
    line = line.replace("--", " dash dash ")
    line = re.sub(r"(?<!\S)-([a-zA-Z0-9])", r" dash \1", line)
    line = line.replace("-", " ")

    # Pre-strip ordinal conversions
    for o, w in {
        "1st": "first",
        "2nd": "second",
        "3rd": "third",
        "4th": "fourth",
        "5th": "fifth",
        "6th": "sixth",
        "7th": "seventh",
        "8th": "eighth",
        "9th": "ninth",
        "10th": "tenth",
        "11th": "eleventh",
        "12th": "twelfth",
        "13th": "thirteenth",
        "14th": "fourteenth",
        "15th": "fifteenth",
        "16th": "sixteenth",
        "17th": "seventeenth",
        "18th": "eighteenth",
        "19th": "nineteenth",
        "20th": "twentieth",
    }.items():
        line = re.sub(r"\b" + o + r"\b", w, line, flags=re.IGNORECASE)

    # Pre-strip alphanumeric separation
    line = re.sub(r"([a-zA-Z])(\d)", r"\1 \2", line)
    line = re.sub(r"(\d)([a-zA-Z])", r"\1 \2", line)


    # Extensions and Domains
    # Drop file extensions entirely
    for ext in [
        ".py",
        ".md",
        ".txt",
        ".jsonl",
        ".json",
        ".yml",
        ".yaml",
        ".sh",
        ".log",
        ".out",
        ".csv",
        ".com",
        ".org",
        ".io",
        ".dev",
        ".net",
        ".edu",
        ".gov",
        ".co",
        ".me",
        ".html",
        ".css",
        ".js",
        ".ts",
        ".jsx",
        ".tsx",
        ".go",
        ".rs",
        ".rb",
        ".java",
        ".cpp",
        ".c",
        ".h",
        ".xml",
        ".sql",
    ]:
        line = line.replace(ext, "")

    for p in ["[", "]", "(", ")", "{", "}", ":", ";", '"']:
        line = line.replace(p, " ")
    line = line.replace("'", "")

    # Uncanny Valley Leading Fillers
    line = re.sub(
        r"^(ok|okay|alright|so|well|anyway|hmm|let's see|let me|lets see|uh|um|right|wait|hold on|give me a second|one moment|just a second|just a moment)\b[\s,.]*",
        "",
        line,
        flags=re.IGNORECASE,
    )

    # Strip explicit meta-prompt leaks
    line = re.sub(r"(?i)\b(for\s+)?event\s*type\s*stop,?\s*", "", line)

    # Noun Phrase Compactor
    for k, v in {
        "narrator service": "process",
        "narrator mlx summarizer": "summarizer",
        "narrator prompt newscaster": "prompt",
        "narrator hook": "hook",
        "narrator prompt": "prompt",
        "auto speech": "speech",
        "satisfaction tracker": "metrics",
    }.items():
        line = re.sub(r"\b" + k + r"\b", v, line, flags=re.IGNORECASE)

    # Stressful Word Neutralization
    for k, v in {
        "kill": "stop",
        "pkill": "stop",
        "killall": "stop",
        "destroy": "delete",
        "daemon": "background process",
        "panic": "issue",
    }.items():
        line = re.sub(r"\b" + k + r"\b", v, line, flags=re.IGNORECASE)

    for k, v in {
        "json": "jason",
        "jsonl": "jason l",
        "yaml": "yamul",
        "yml": "yamul",
        "toml": "tommel",
        "api": "a p i",
        "cli": "c l i",
        "llm": "l l m",
        "tts": "t t s",
        "ui": "u i",
        "ux": "u x",
        "ide": "i d e",
        "gcp": "g c p",
        "aws": "a w s",
        "os": "o s",
        "db": "d b",
        "kb": "kilobytes",
        "mb": "megabytes",
        "gb": "gigabytes",
        "tb": "terabytes",
        "ms": "milliseconds",
    }.items():
        line = re.sub(r"\b" + k + r"\b", v, line, flags=re.IGNORECASE)

    # Remove consecutive duplicate words (e.g. 'process process' -> 'process')
    line = re.sub(r"\b(\w+)(?:\s+\1\b)+", r"\1", line, flags=re.IGNORECASE)

    return line
