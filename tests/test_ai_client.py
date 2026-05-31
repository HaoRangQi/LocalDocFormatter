import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from docformat.ai_client import OpenAICompatibleClient, OpenAICompatibleError


class FakeOpenAIHandler(BaseHTTPRequestHandler):
    requests = []

    def do_GET(self):  # noqa: N802
        self.__class__.requests.append((self.command, self.path, self.headers.get("Authorization"), None))
        if self.path == "/v1/models":
            self._json(200, {"data": [{"id": "gpt-a"}, {"id": "gpt-b"}]})
        else:
            self._json(404, {"error": {"message": "missing"}})

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", "0") or "0")
        body = self.rfile.read(length).decode("utf-8")
        self.__class__.requests.append((self.command, self.path, self.headers.get("Authorization"), body))
        if self.path == "/v1/chat/completions":
            self._sse(
                200,
                [
                    {"choices": [{"delta": {"role": "assistant"}}]},
                    {"choices": [{"delta": {"content": "修正后"}}]},
                    {"choices": [{"delta": {"content": "文本"}}]},
                    {"choices": [{"delta": {"refusal": ""}}]},
                    "[DONE]",
                ],
            )
        elif self.path == "/v1/bad":
            self._json(401, {"error": {"message": "bad key"}})
        else:
            self._json(404, {"error": {"message": "missing"}})

    def log_message(self, format, *args):
        return

    def _json(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _sse(self, status, events):
        body = "".join(
            f"data: {json.dumps(event, ensure_ascii=False) if not isinstance(event, str) else event}\n\n"
            for event in events
        ).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class ClientTests(unittest.TestCase):
    def setUp(self):
        FakeOpenAIHandler.requests = []
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeOpenAIHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}/v1"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def test_list_models_uses_base_url_and_bearer_token(self):
        client = OpenAICompatibleClient(self.base_url, "sk-test")

        models = client.list_models()

        self.assertEqual(models, ["gpt-a", "gpt-b"])
        self.assertEqual(FakeOpenAIHandler.requests[0][0:3], ("GET", "/v1/models", "Bearer sk-test"))

    def test_chat_completion_uses_streaming_chat_completions(self):
        client = OpenAICompatibleClient(self.base_url, "sk-test")

        result = client.chat_completion("gpt-a", [{"role": "user", "content": "原文"}])

        self.assertEqual(result, "修正后文本")
        request_body = json.loads(FakeOpenAIHandler.requests[-1][3])
        self.assertEqual(request_body["model"], "gpt-a")
        self.assertEqual(request_body["temperature"], 0)
        self.assertTrue(request_body["stream"])
        self.assertEqual(FakeOpenAIHandler.requests[-1][0:3], ("POST", "/v1/chat/completions", "Bearer sk-test"))

    def test_http_errors_are_sanitized(self):
        client = OpenAICompatibleClient(self.base_url, "sk-secret")

        with self.assertRaises(OpenAICompatibleError) as raised:
            client._request("POST", "/bad", {})

        self.assertIn("401", str(raised.exception))
        self.assertNotIn("sk-secret", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
