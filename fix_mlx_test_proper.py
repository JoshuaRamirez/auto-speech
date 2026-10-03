import re
with open("tests/test_mlx_summarizer.py", "r") as f:
    content = f.read()

replacement = """    def apply_chat_template(self, messages, tokenize=False, add_generation_prompt=True):
        user_msg = next((m["content"] for m in messages if m["role"] == "user"), messages[0]["content"])
        return f"<<CHAT>>{user_msg}<<END>>\""""

content = re.sub(
    r'    def apply_chat_template\(self, messages, tokenize=False, add_generation_prompt=True\):\n        user_msg = next\(\(m\["content"\] for m in messages if m\["role"\] == "user"\), messages\[0\]\["content"\]\)\n        return f"<<CHAT>>\{user_msg\}<<END>>"',
    "    def apply_chat_template(self, messages, tokenize=False, add_generation_prompt=True):\n        user_msg = next((m['content'] for m in messages if m.get('role') == 'user'), messages[0]['content'])\n        return f'<<CHAT>>{user_msg}<<END>>'",
    content
)

# wait I will just rewrite using a normal replace
