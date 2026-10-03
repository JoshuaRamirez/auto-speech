import re

with open("plugin/scripts/python/web_server.py", "r") as f:
    content = f.read()

old_seek = """    def _handle_seek(self):
        return jsonify({"error": "not supported by native audio sink"}), 501"""

new_seek = """    def _handle_seek(self):
        body = request.get_json(silent=True) or {}
        target = _text_field(body, "target")
        if target is None:
            return jsonify({"error": "target must be a string"}), 400
        if not target:
            return jsonify({"error": "target is required"}), 400
        return jsonify({"error": "not supported by native audio sink"}), 501"""

content = content.replace(old_seek, new_seek)

with open("plugin/scripts/python/web_server.py", "w") as f:
    f.write(content)
