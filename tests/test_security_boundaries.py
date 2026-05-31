import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from docformat.ai_client import OpenAICompatibleError
from docformat.jobs import JobManager
from docformat.server import browse_workspace, create_app


class SecurityBoundaryTests(unittest.TestCase):
    def test_ai_config_response_never_exposes_full_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = create_app(token="test-token", soffice_path=None, run_async=False, ai_config_path=Path(tmp) / "ai.json")
            app.handle_json(
                "POST",
                "/api/ai/config",
                {"baseUrl": "https://relay.example.com/v1", "apiKey": "sk-sensitive-value", "selectedModel": "gpt-test"},
                {"X-DocFormat-Token": "test-token"},
            )

            status, headers, body = app.handle_json("GET", "/api/ai/config", None, {"X-DocFormat-Token": "test-token"})

        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertTrue(payload["hasApiKey"])
        self.assertNotIn("sk-sensitive-value", body)
        self.assertIn("apiKeyMasked", payload)

    def test_ai_config_key_reveal_is_separate_from_public_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = create_app(token="test-token", soffice_path=None, run_async=False, ai_config_path=Path(tmp) / "ai.json")
            app.handle_json(
                "POST",
                "/api/ai/config",
                {"baseUrl": "https://relay.example.com/v1", "apiKey": "sk-sensitive-value", "selectedModel": "gpt-test"},
                {"X-DocFormat-Token": "test-token"},
            )

            public_status, headers, public_body = app.handle_json("GET", "/api/ai/config", None, {"X-DocFormat-Token": "test-token"})
            reveal_status, headers, reveal_body = app.handle_json("GET", "/api/ai/config/key", None, {"X-DocFormat-Token": "test-token"})

        self.assertEqual(public_status, 200)
        self.assertEqual(reveal_status, 200)
        self.assertNotIn("sk-sensitive-value", public_body)
        self.assertIn("sk-sensitive-value", reveal_body)

    def test_v1_models_error_does_not_leak_api_key(self):
        class FailingClient:
            def __init__(self, base_url, api_key):
                self.api_key = api_key

            def list_models(self):
                raise OpenAICompatibleError(f"bad key {self.api_key}")

        with tempfile.TemporaryDirectory() as tmp:
            app = create_app(
                token="test-token",
                soffice_path=None,
                run_async=False,
                ai_config_path=Path(tmp) / "ai.json",
                ai_client_class=FailingClient,
            )
            app.handle_json(
                "POST",
                "/api/ai/config",
                {"baseUrl": "https://relay.example.com/v1", "apiKey": "sk-sensitive-value", "selectedModel": "gpt-test"},
                {"X-DocFormat-Token": "test-token"},
            )
            status, headers, body = app.handle_json(
                "GET",
                "/v1/models",
                None,
                {"X-DocFormat-Token": "test-token", "Authorization": "Bearer sk-sensitive-value"},
            )

        self.assertEqual(status, 502)
        self.assertNotIn("sk-sensitive-value", body)

    def test_conversion_report_does_not_store_ai_key_or_source_text(self):
        class FakeClient:
            def chat_completion(self, model, messages):
                text = messages[-1]["content"].split("待修正文稿：", 1)[-1].strip()
                return text.replace("在见", "再见")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "draft.txt"
            source.write_text("大家在见", encoding="utf-8")
            manager = JobManager(
                soffice_path=None,
                run_async=False,
                correction_client_factory=lambda config: FakeClient(),
            )

            job = manager.create_job(
                [str(source)],
                str(root / "out"),
                "target",
                recursive=True,
                target_format="txt",
                correction_config={"apiKey": "sk-sensitive-value", "selectedModel": "gpt-test"},
            )
            report = (root / "out" / "conversion-report.json").read_text(encoding="utf-8")

        self.assertEqual(job.results[0].status, "success")
        self.assertNotIn("sk-sensitive-value", report)
        self.assertNotIn("大家在见", report)

    def test_browse_workspace_rejects_symlink_escape(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "workspace"
            outside = Path(tmp) / "outside"
            root.mkdir()
            outside.mkdir()
            link = root / "escape"
            try:
                link.symlink_to(outside, target_is_directory=True)
            except OSError:
                self.skipTest("symlinks unavailable on this filesystem")

            with mock.patch.dict(os.environ, {"DOCFORMAT_WORKSPACE_ROOTS": str(root)}, clear=False):
                with self.assertRaises(ValueError) as raised:
                    browse_workspace(str(link))

        self.assertIn("outside allowed workspace roots", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
