"""Only the external chat boundary for the isolated public flow acceptance run."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os


if os.environ.get("PUBLIC_FLOW_MOCK") != "1":
    raise SystemExit("isolated upstream must be enabled explicitly")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, _format, *_args):
        pass  # Do not log prompts, image data URLs, or synthetic authorization headers.

    def do_GET(self):
        if self.path != "/health":
            self.send_error(404)
            return
        self.send_response(200)
        self.end_headers()

    def do_POST(self):
        if self.path != "/api/v3/chat/completions":
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", "0"))
        if length < 1 or length > 8 * 1024 * 1024:
            self.send_error(413)
            return
        try:
            request = json.loads(self.rfile.read(length))
            assert isinstance(request.get("messages"), list)
        except (ValueError, AssertionError):
            self.send_error(400)
            return
        copy = {
            "title": "帆布手提袋",
            "body": "看看这款帆布手提袋的外观与细节，找到适合自己的日常搭配。",
            "hashtags": ["箱包", "帆布"],
            "facts_to_confirm": [],
        }
        payload = json.dumps({"choices": [{"message": {"content": json.dumps(copy, ensure_ascii=False)}}]}, ensure_ascii=False).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


ThreadingHTTPServer(("0.0.0.0", 18184), Handler).serve_forever()
