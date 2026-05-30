import http.client
import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib import request

from docformat.server import RequestHandler, create_app


class HTTPAPITests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.token = "test-token"
        app = create_app(token=self.token, soffice_path=None, run_async=False, ai_config_path=self.root / "ai.json")

        class BoundHandler(RequestHandler):
            pass

        BoundHandler.app = app
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), BoundHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def get_json(self, path, token=True):
        headers = {}
        if token:
            headers["X-DocFormat-Token"] = self.token
        req = request.Request(f"{self.base_url}{path}", headers=headers)
        with request.urlopen(req, timeout=5) as response:
            return response.status, json.loads(response.read().decode("utf-8"))

    def post_json(self, path, payload, token=True):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["X-DocFormat-Token"] = self.token
        req = request.Request(
            f"{self.base_url}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with request.urlopen(req, timeout=5) as response:
            return response.status, json.loads(response.read().decode("utf-8"))

    def test_static_index_and_health_are_served(self):
        with request.urlopen(f"{self.base_url}/", timeout=5) as response:
            index = response.read().decode("utf-8")
        status, health = self.get_json("/api/health", token=False)

        self.assertIn("DocFormat", index)
        self.assertEqual(status, 200)
        self.assertIn("libreOffice", health)
        self.assertIn("runtime", health)

    def test_app_config_bootstraps_browser_token(self):
        with request.urlopen(f"{self.base_url}/app-config.js", timeout=5) as response:
            body = response.read().decode("utf-8")

        self.assertEqual(response.status, 200)
        self.assertIn("window.DOCFORMAT_TOKEN", body)
        self.assertIn(self.token, body)

    def test_mutating_api_rejects_missing_token(self):
        with self.assertRaises(request.HTTPError) as raised:
            self.post_json("/api/scan", {"sources": [str(self.root)], "recursive": True}, token=False)

        self.assertEqual(raised.exception.code, 403)

    def test_scan_api_returns_detected_files_over_http(self):
        source = self.root / "docs"
        source.mkdir()
        (source / "a.doc").write_text("doc")
        (source / "b.xls").write_text("xls")

        status, payload = self.post_json("/api/scan", {"sources": [str(source)], "recursive": True})

        self.assertEqual(status, 200)
        self.assertEqual(payload["count"], 2)
        self.assertEqual([item["name"] for item in payload["files"]], ["a.doc", "b.xls"])

    def test_invalid_json_returns_400(self):
        parsed = http.client.urlsplit(self.base_url)
        connection = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=5)
        try:
            connection.request(
                "POST",
                "/api/scan",
                body=b"{not-json",
                headers={"Content-Type": "application/json", "X-DocFormat-Token": self.token},
            )
            response = connection.getresponse()
            body = response.read().decode("utf-8")
        finally:
            connection.close()

        self.assertEqual(response.status, 400)
        self.assertIn("Invalid JSON", body)

    def test_static_path_traversal_is_not_served(self):
        with self.assertRaises(request.HTTPError) as raised:
            request.urlopen(f"{self.base_url}/../server.py", timeout=5)

        self.assertEqual(raised.exception.code, 404)


if __name__ == "__main__":
    unittest.main()
