import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from docformat.config import LibreOfficeInfo
from docformat.server import create_app, resolve_workspace_roots


class ServerTests(unittest.TestCase):
    def test_health_reports_converter_state(self):
        with mock.patch(
            "docformat.server.discover_soffice",
            return_value=LibreOfficeInfo(False, None, "install LibreOffice"),
        ):
            app = create_app(token="test-token", soffice_path=None)
            status, headers, body = app.handle_json("GET", "/api/health", None, {})

        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertFalse(payload["libreOffice"]["found"])
        self.assertIn("modernize", payload["modes"])

    def test_job_api_requires_token(self):
        app = create_app(token="test-token", soffice_path=None)
        status, headers, body = app.handle_json(
            "POST",
            "/api/jobs",
            {"sources": [], "mode": "modernize", "recursive": True},
            {},
        )

        self.assertEqual(status, 403)

    def test_token_header_name_is_case_insensitive(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "a.zip"
            source.write_text("zip")
            app = create_app(token="test-token", soffice_path=None, run_async=False)
            status, headers, body = app.handle_json(
                "POST",
                "/api/jobs",
                {"sources": [str(source)], "outputDir": str(Path(tmp) / "out"), "mode": "modernize", "recursive": True},
                {"X-Docformat-Token": "test-token"},
            )

        self.assertEqual(status, 201)

    def test_job_api_creates_job_with_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "a.zip"
            source.write_text("zip")
            app = create_app(token="test-token", soffice_path=None, run_async=False)
            status, headers, body = app.handle_json(
                "POST",
                "/api/jobs",
                {"sources": [str(source)], "outputDir": str(Path(tmp) / "out"), "mode": "modernize", "recursive": True},
                {"X-DocFormat-Token": "test-token"},
            )

        self.assertEqual(status, 201)
        payload = json.loads(body)
        deadline = __import__("time").monotonic() + 3
        while payload["status"] in {"queued", "running"} and __import__("time").monotonic() < deadline:
            status, headers, body = app.handle_json(
                "GET",
                f"/api/jobs/{payload['id']}",
                None,
                {"X-DocFormat-Token": "test-token"},
            )
            payload = json.loads(body)

        self.assertEqual(payload["status"], "completed")
        self.assertEqual(payload["results"][0]["status"], "skipped")

    def test_ai_config_api_masks_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = create_app(token="test-token", soffice_path=None, run_async=False, ai_config_path=Path(tmp) / "ai.json")
            status, headers, body = app.handle_json(
                "POST",
                "/api/ai/config",
                {"baseUrl": "https://relay.example.com/v1", "apiKey": "sk-secret-value", "selectedModel": "gpt-test"},
                {"X-DocFormat-Token": "test-token"},
            )

            self.assertEqual(status, 200)
            self.assertNotIn("sk-secret-value", body)
            payload = json.loads(body)
            self.assertTrue(payload["hasApiKey"])
            self.assertEqual(payload["selectedModel"], "gpt-test")

    def test_ai_models_refresh_uses_configured_client(self):
        class FakeClient:
            def __init__(self, base_url, api_key):
                self.base_url = base_url
                self.api_key = api_key

            def list_models(self):
                return ["gpt-a", "gpt-b"]

        with tempfile.TemporaryDirectory() as tmp:
            app = create_app(
                token="test-token",
                soffice_path=None,
                run_async=False,
                ai_config_path=Path(tmp) / "ai.json",
                ai_client_class=FakeClient,
            )
            app.handle_json(
                "POST",
                "/api/ai/config",
                {"baseUrl": "https://relay.example.com/v1", "apiKey": "sk-secret-value", "selectedModel": "gpt-a"},
                {"X-DocFormat-Token": "test-token"},
            )
            status, headers, body = app.handle_json(
                "POST",
                "/api/ai/models/refresh",
                {},
                {"X-DocFormat-Token": "test-token"},
            )

        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["models"], ["gpt-a", "gpt-b"])
        self.assertNotIn("sk-secret-value", body)

    def test_ai_lexicon_preview_reads_files_and_reports_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lexicon_path = root / "words.csv"
            lexicon_path.write_text("错误词,正确词\n在见,再见\nopen ai,OpenAI\n", encoding="utf-8")
            empty_path = root / "empty.csv"
            empty_path.write_text("错误词,正确词\n", encoding="utf-8")
            app = create_app(token="test-token", soffice_path=None, run_async=False, ai_config_path=root / "ai.json")

            status, headers, body = app.handle_json(
                "POST",
                "/api/ai/lexicon/preview",
                {"paths": [str(lexicon_path), str(empty_path), str(root / "missing.csv")]},
                {"X-DocFormat-Token": "test-token"},
            )

        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertEqual(payload["totalValidEntries"], 2)
        self.assertEqual(payload["files"][0]["status"], "success")
        self.assertEqual(payload["files"][0]["count"], 2)
        self.assertEqual(payload["files"][0]["sample"][0], {"wrong": "在见", "correct": "再见"})
        self.assertEqual(payload["files"][1]["status"], "failed")
        self.assertIn("没有读取到有效词条", payload["files"][1]["error"])
        self.assertEqual(payload["files"][2]["status"], "failed")
        self.assertIn("词表文件不存在", payload["files"][2]["error"])

    def test_ai_correction_job_api_uses_token_and_returns_corrected_text(self):
        class FakeClient:
            def __init__(self, base_url, api_key):
                pass

            def chat_completion(self, model, messages):
                return messages[-1]["content"].split("待修正文稿：", 1)[-1].strip().replace("在见", "再见")

        with tempfile.TemporaryDirectory() as tmp:
            app = create_app(
                token="test-token",
                soffice_path=None,
                run_async=False,
                ai_config_path=Path(tmp) / "ai.json",
                ai_client_class=FakeClient,
            )
            app.handle_json(
                "POST",
                "/api/ai/config",
                {"baseUrl": "https://relay.example.com/v1", "apiKey": "sk-secret-value", "selectedModel": "gpt-a"},
                {"X-DocFormat-Token": "test-token"},
            )
            status, headers, body = app.handle_json(
                "POST",
                "/api/ai/correction-jobs",
                {"text": "大家在见", "filePaths": [], "userLexicon": ""},
                {"X-DocFormat-Token": "test-token"},
            )

        self.assertEqual(status, 201)
        payload = json.loads(body)
        self.assertEqual(payload["status"], "completed")
        self.assertEqual(payload["correctedText"], "大家再见")
        self.assertNotIn("sk-secret-value", body)

    def test_job_api_rejects_inline_ai_correction_without_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "a.doc"
            source.write_text("doc")
            app = create_app(token="test-token", soffice_path=None, run_async=False, ai_config_path=Path(tmp) / "ai.json")

            status, headers, body = app.handle_json(
                "POST",
                "/api/jobs",
                {
                    "sources": [str(source)],
                    "outputDir": str(Path(tmp) / "out"),
                    "mode": "target",
                    "targetFormat": "docx",
                    "enableAiCorrection": True,
                },
                {"X-DocFormat-Token": "test-token"},
            )

        self.assertEqual(status, 400)
        self.assertIn("API key", body)

    def test_job_api_accepts_target_format(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "a.zip"
            source.write_text("zip")
            app = create_app(token="test-token", soffice_path=None, run_async=False)

            status, headers, body = app.handle_json(
                "POST",
                "/api/jobs",
                {
                    "sources": [str(source)],
                    "outputDir": str(Path(tmp) / "out"),
                    "mode": "target",
                    "targetFormat": "docx",
                    "recursive": True,
                },
                {"X-DocFormat-Token": "test-token"},
            )

        self.assertEqual(status, 201)
        payload = json.loads(body)
        self.assertEqual(payload["mode"], "target")
        self.assertEqual(payload["targetFormat"], "docx")

    def test_job_api_passes_inline_ai_correction_to_job_manager(self):
        class FakeClient:
            def __init__(self, base_url, api_key):
                pass

            def chat_completion(self, model, messages):
                text = messages[-1]["content"].split("待修正文稿：", 1)[-1].strip()
                return text.replace("在见", "再见")

        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "draft.txt"
            source.write_text("大家在见", encoding="utf-8")
            app = create_app(
                token="test-token",
                soffice_path=None,
                run_async=False,
                ai_config_path=Path(tmp) / "ai.json",
                ai_client_class=FakeClient,
            )
            app.handle_json(
                "POST",
                "/api/ai/config",
                {"baseUrl": "https://relay.example.com/v1", "apiKey": "sk-secret-value", "selectedModel": "gpt-a"},
                {"X-DocFormat-Token": "test-token"},
            )

            status, headers, body = app.handle_json(
                "POST",
                "/api/jobs",
                {
                    "sources": [str(source)],
                    "outputDir": str(Path(tmp) / "out"),
                    "mode": "target",
                    "targetFormat": "txt",
                    "enableAiCorrection": True,
                    "userLexicon": "",
                    "correctionPrompt": "只修正错别字",
                    "lexiconEntries": [{"wrong": "在见", "correct": "再见"}],
                    "lexiconFilePaths": [],
                    "recursive": True,
                },
                {"X-DocFormat-Token": "test-token"},
            )
            payload = json.loads(body)
            corrected = Path(payload["results"][0]["target"]).read_text(encoding="utf-8")

        self.assertEqual(status, 201)
        self.assertTrue(payload["correctionEnabled"])
        self.assertEqual(payload["results"][0]["status"], "success")
        self.assertEqual(corrected, "大家再见")
        self.assertNotIn("sk-secret-value", body)

    def test_scan_api_lists_files_with_target_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "docs"
            source.mkdir()
            (source / "a.doc").write_text("doc")
            (source / "b.xls").write_text("xls")
            app = create_app(token="test-token", soffice_path=None, run_async=False)

            status, headers, body = app.handle_json(
                "POST",
                "/api/scan",
                {"sources": [str(source)], "recursive": True, "enableAiCorrection": False},
                {"X-DocFormat-Token": "test-token"},
            )

        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertEqual(payload["count"], 2)
        self.assertEqual([item["name"] for item in payload["files"]], ["a.doc", "b.xls"])
        self.assertEqual(payload["files"][0]["defaultTargetFormat"], "pdf")
        self.assertIn("docx", payload["files"][0]["supportedTargets"])

    def test_health_reports_container_runtime_roots(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "workspace"
            root.mkdir()
            with mock.patch.dict(
                os.environ,
                {"DOCFORMAT_CONTAINER": "1", "DOCFORMAT_WORKSPACE_ROOTS": str(root)},
                clear=False,
            ):
                app = create_app(token="test-token", soffice_path=None)
                status, headers, body = app.handle_json("GET", "/api/health", None, {})

        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertTrue(payload["runtime"]["container"])
        self.assertEqual(payload["runtime"]["workspaceRoots"], [str(root.resolve())])
        self.assertIn("使用已挂载到容器内的路径", payload["runtime"]["pathHint"])

    def test_browse_api_lists_allowed_workspace_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "workspace"
            docs = root / "Docs"
            docs.mkdir(parents=True)
            (docs / "a.doc").write_text("doc")
            (docs / ".hidden.doc").write_text("hidden")
            with mock.patch.dict(os.environ, {"DOCFORMAT_WORKSPACE_ROOTS": str(root)}, clear=False):
                app = create_app(token="test-token", soffice_path=None)
                status, headers, body = app.handle_json(
                    "GET",
                    f"/api/browse?path={docs}",
                    None,
                    {"X-DocFormat-Token": "test-token"},
                )

        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertEqual(payload["path"], str(docs.resolve()))
        self.assertEqual(payload["parent"], str(root.resolve()))
        self.assertEqual([entry["name"] for entry in payload["entries"]], ["a.doc"])
        self.assertEqual(payload["entries"][0]["kind"], "file")

    def test_browse_api_rejects_paths_outside_workspace_roots(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "workspace"
            outside = Path(tmp) / "outside"
            root.mkdir()
            outside.mkdir()
            with mock.patch.dict(os.environ, {"DOCFORMAT_WORKSPACE_ROOTS": str(root)}, clear=False):
                app = create_app(token="test-token", soffice_path=None)
                status, headers, body = app.handle_json(
                    "GET",
                    f"/api/browse?path={outside}",
                    None,
                    {"X-DocFormat-Token": "test-token"},
                )

        self.assertEqual(status, 400)
        self.assertIn("outside allowed workspace roots", body)

    def test_resolve_workspace_roots_uses_existing_env_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            existing = Path(tmp) / "existing"
            missing = Path(tmp) / "missing"
            existing.mkdir()
            with mock.patch.dict(os.environ, {"DOCFORMAT_WORKSPACE_ROOTS": f"{existing}:{missing}"}, clear=False):
                self.assertEqual(resolve_workspace_roots(), [existing.resolve()])


if __name__ == "__main__":
    unittest.main()
