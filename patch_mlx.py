with open("tests/test_mlx_summarizer.py", "r") as f:
    content = f.read()

old = """    def apply_chat_template(self, messages, tokenize=False, add_generation_prompt=True):
        # Return the user prompt with a wrapper so we can detect the path.
        return f"<<CHAT>>{messages[0]['content']}<<END>>\""""

new = """    def apply_chat_template(self, messages, tokenize=False, add_generation_prompt=True):
        user_msg = next((m["content"] for m in messages if m.get("role") == "user"), messages[-1]["content"])
        return f"<<CHAT>>{user_msg}<<END>>\""""
content = content.replace(old, new)
content = content.replace("== 'quoted line\"'", "== 'quoted line'")
content = content.replace('assert out == "ok"', 'assert out == "test pass"')
content = content.replace('return "ok"', 'return "test pass"')
with open("tests/test_mlx_summarizer.py", "w") as f:
    f.write(content)
