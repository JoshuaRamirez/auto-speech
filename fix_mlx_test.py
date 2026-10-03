import re
with open("tests/test_mlx_summarizer.py", "r") as f:
    content = f.read()

# Fix 1: apply_chat_template mock
new_mock = """    def apply_chat_template(self, messages, tokenize=False, add_generation_prompt=True):
        # Return the user prompt with a wrapper so we can detect the path.
        # Find the first user message.
        user_msg = next((m["content"] for m in messages if m["role"] == "user"), messages[0]["content"])
        return f"<<CHAT>>{user_msg}<<END>>\""""
content = re.sub(
    r'    def apply_chat_template\(self, messages, tokenize=False, add_generation_prompt=True\):\n        # Return the user prompt with a wrapper so we can detect the path\.\n        return f"<<CHAT>>\{messages\[0\]\[\'content\'\]\}<<END>>"',
    new_mock.strip(),
    content
)

# Fix 2: 'quoted line'
content = content.replace("== 'quoted line\"'", "== 'quoted line'")

with open("tests/test_mlx_summarizer.py", "w") as f:
    f.write(content)
