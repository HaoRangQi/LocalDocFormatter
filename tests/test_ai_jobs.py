import tempfile
import unittest
from pathlib import Path

from docformat.ai_jobs import AICorrectionJobManager


class FakeClient:
    def __init__(self):
        self.calls = []

    def chat_completion(self, model, messages):
        self.calls.append((model, messages))
        text = messages[-1]["content"].split("待修正文稿：", 1)[-1].strip()
        return text.replace("在见", "再见").replace("open ai", "OpenAI")


class AIJobTests(unittest.TestCase):
    def test_pasted_text_job_returns_corrected_text_without_report_text(self):
        client = FakeClient()
        manager = AICorrectionJobManager(client_factory=lambda config: client, run_async=False)

        job = manager.create_job(
            config={"baseUrl": "http://example/v1", "apiKey": "sk-secret", "selectedModel": "gpt-test"},
            text="大家在见，open ai",
            file_paths=[],
            user_lexicon="",
        )

        self.assertEqual(job.status, "completed")
        self.assertEqual(job.corrected_text, "大家再见，OpenAI")
        self.assertIsNone(job.report_path)
        self.assertNotIn("sk-secret", str(job))

    def test_file_job_writes_corrected_file_and_report_without_source_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "draft.txt"
            source.write_text("大家在见", encoding="utf-8")
            client = FakeClient()
            manager = AICorrectionJobManager(client_factory=lambda config: client, run_async=False)

            job = manager.create_job(
                config={"baseUrl": "http://example/v1", "apiKey": "sk-secret", "selectedModel": "gpt-test"},
                text="",
                file_paths=[str(source)],
                user_lexicon="",
            )

            self.assertEqual(job.status, "completed")
            self.assertTrue((Path(tmp) / "draft.corrected.txt").exists())
            report_text = Path(job.report_path).read_text(encoding="utf-8")
            self.assertIn("draft.corrected.txt", report_text)
            self.assertNotIn("大家在见", report_text)
            self.assertNotIn("sk-secret", report_text)


if __name__ == "__main__":
    unittest.main()
