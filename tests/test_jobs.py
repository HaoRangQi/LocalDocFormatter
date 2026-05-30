import json
import os
import tempfile
import time
import unittest
from pathlib import Path

from docformat.jobs import JobManager


FAKE_SOFFICE = """#!/bin/sh
outdir=""
target=""
src=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --outdir)
      outdir="$2"
      shift 2
      ;;
    --convert-to)
      target="$2"
      shift 2
      ;;
    --headless|--nologo|--nofirststartwizard|--nodefault|--nolockcheck)
      shift
      ;;
    *)
      src="$1"
      shift
      ;;
  esac
done
if echo "$src" | grep -q "fail"; then
  echo "failed to open" >&2
  exit 1
fi
ext="${target%%:*}"
base="$(basename "$src")"
name="${base%.*}"
mkdir -p "$outdir"
printf "converted" > "$outdir/$name.$ext"
"""

SLOW_FAKE_SOFFICE = """#!/bin/sh
sleep 1
outdir=""
target=""
src=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --outdir)
      outdir="$2"
      shift 2
      ;;
    --convert-to)
      target="$2"
      shift 2
      ;;
    --headless|--nologo|--nofirststartwizard|--nodefault|--nolockcheck)
      shift
      ;;
    *)
      src="$1"
      shift
      ;;
  esac
done
ext="${target%%:*}"
base="$(basename "$src")"
name="${base%.*}"
mkdir -p "$outdir"
printf "converted" > "$outdir/$name.$ext"
"""

TEXT_FAKE_SOFFICE = """#!/bin/sh
outdir=""
target=""
src=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --outdir)
      outdir="$2"
      shift 2
      ;;
    --convert-to)
      target="$2"
      shift 2
      ;;
    --headless|--nologo|--nofirststartwizard|--nodefault|--nolockcheck)
      shift
      ;;
    *)
      src="$1"
      shift
      ;;
  esac
done
ext="${target%%:*}"
base="$(basename "$src")"
name="${base%.*}"
mkdir -p "$outdir"
cat "$src" > "$outdir/$name.$ext"
"""


class FakeCorrectionClient:
    messages = []

    def chat_completion(self, model, messages):
        self.__class__.messages.append(messages)
        text = messages[-1]["content"].split("待修正文稿：", 1)[-1].strip()
        return text.replace("在见", "再见")


class JobManagerTests(unittest.TestCase):
    def test_create_job_converts_supported_files_and_writes_reports(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "docs"
            source.mkdir()
            (source / "a.doc").write_text("doc")
            (source / "ignored.zip").write_text("zip")
            fake_soffice = root / "soffice"
            fake_soffice.write_text(FAKE_SOFFICE)
            fake_soffice.chmod(0o755)

            manager = JobManager(str(fake_soffice), run_async=False)
            job = manager.create_job([str(source)], None, "modernize", recursive=True)

            self.assertEqual(job.status, "completed")
            statuses = [result.status for result in job.results]
            self.assertIn("success", statuses)
            self.assertIn("skipped", statuses)
            output_dir = source / "converted"
            self.assertTrue((output_dir / "a.docx").exists())
            self.assertTrue((output_dir / "conversion-report.json").exists())
            report = json.loads((output_dir / "conversion-report.json").read_text())
            self.assertEqual(report["id"], job.id)

    def test_cancel_marks_future_files_cancelled(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake_soffice = root / "soffice"
            fake_soffice.write_text(FAKE_SOFFICE)
            fake_soffice.chmod(0o755)
            source = root / "a.doc"
            source.write_text("doc")

            manager = JobManager(str(fake_soffice), run_async=False)
            job = manager.create_job([str(source)], str(root / "out"), "modernize", recursive=True)

            self.assertFalse(manager.cancel_job(job.id))
            self.assertEqual(manager.get_job(job.id).status, "completed")

    def test_async_job_can_be_cancelled_while_running(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake_soffice = root / "soffice"
            fake_soffice.write_text(SLOW_FAKE_SOFFICE)
            fake_soffice.chmod(0o755)
            for index in range(3):
                (root / f"{index}.doc").write_text("doc")

            manager = JobManager(str(fake_soffice), run_async=True)
            job = manager.create_job([str(root)], str(root / "out"), "modernize", recursive=False)
            self.assertIn(job.status, {"queued", "running"})

            self.assertTrue(manager.cancel_job(job.id))
            deadline = time.monotonic() + 5
            while manager.get_job(job.id).status in {"queued", "running"} and time.monotonic() < deadline:
                time.sleep(0.05)

            self.assertEqual(manager.get_job(job.id).status, "cancelled")

    def test_target_format_restricts_outputs_by_detected_source_format(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "sheet.xls"
            source.write_text("sheet")
            fake_soffice = root / "soffice"
            fake_soffice.write_text(FAKE_SOFFICE)
            fake_soffice.chmod(0o755)

            manager = JobManager(str(fake_soffice), run_async=False)
            bad_job = manager.create_job([str(source)], str(root / "bad"), "target", recursive=True, target_format="docx")
            good_job = manager.create_job([str(source)], str(root / "good"), "target", recursive=True, target_format="xlsx")

            self.assertEqual(bad_job.results[0].status, "skipped")
            self.assertEqual(bad_job.results[0].error, "Unsupported target format")
            self.assertEqual(good_job.results[0].status, "success")
            self.assertTrue((root / "good" / "sheet.xlsx").exists())

    def test_conversion_job_can_apply_ai_correction_before_export(self):
        with tempfile.TemporaryDirectory() as tmp:
            FakeCorrectionClient.messages = []
            root = Path(tmp)
            source = root / "draft.doc"
            source.write_text("大家在见", encoding="utf-8")
            fake_soffice = root / "soffice"
            fake_soffice.write_text(TEXT_FAKE_SOFFICE)
            fake_soffice.chmod(0o755)

            manager = JobManager(
                str(fake_soffice),
                run_async=False,
                correction_client_factory=lambda config: FakeCorrectionClient(),
            )
            job = manager.create_job(
                [str(source)],
                str(root / "out"),
                "target",
                recursive=True,
                target_format="docx",
                correction_config={"apiKey": "sk-secret", "selectedModel": "gpt-test"},
                correction_user_lexicon="",
            )

            self.assertEqual(job.status, "completed")
            self.assertEqual(job.results[0].status, "success")
            self.assertEqual(job.results[0].target, str(root / "out" / "draft.docx"))
            self.assertEqual((root / "out" / "draft.docx").read_text(encoding="utf-8"), "大家再见")
            report = (root / "out" / "conversion-report.json").read_text(encoding="utf-8")
            self.assertIn('"aiCorrection": true', report)
            self.assertNotIn("大家在见", report)
            self.assertNotIn("sk-secret", report)

    def test_conversion_job_uses_custom_prompt_and_lexicon_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            FakeCorrectionClient.messages = []
            root = Path(tmp)
            source = root / "draft.txt"
            source.write_text("大家在见", encoding="utf-8")
            lexicon_file = root / "words.csv"
            lexicon_file.write_text("错误词,正确词\n阿里妈妈,阿里巴巴\n", encoding="utf-8")

            manager = JobManager(
                str(root / "missing-soffice"),
                run_async=False,
                correction_client_factory=lambda config: FakeCorrectionClient(),
            )
            job = manager.create_job(
                [str(source)],
                str(root / "out"),
                "target",
                recursive=True,
                target_format="txt",
                correction_config={"apiKey": "sk-secret", "selectedModel": "gpt-test"},
                correction_user_lexicon="在见 => 再见",
                correction_prompt="只修正错别字，不解释",
                correction_entries=[{"wrong": "open ai", "correct": "OpenAI"}],
                correction_lexicon_files=[str(lexicon_file)],
            )

            self.assertEqual(job.status, "completed")
            joined = "\n".join(message["content"] for message in FakeCorrectionClient.messages[-1])
            self.assertIn("只修正错别字，不解释", joined)
            self.assertIn("在见 => 再见", joined)
            self.assertIn("open ai => OpenAI", joined)
            self.assertIn("阿里妈妈 => 阿里巴巴", joined)

    def test_scan_files_lists_candidates_with_default_targets(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "docs"
            source.mkdir()
            (source / "a.doc").write_text("doc")
            (source / "b.xls").write_text("xls")
            (source / "ignored.zip").write_text("zip")
            (source / "~$lock.doc").write_text("lock")

            manager = JobManager(None, run_async=False)
            rows = manager.scan_files([str(source)], recursive=True, correction_enabled=False)

            names = [row["name"] for row in rows]
            self.assertEqual(names, ["a.doc", "b.xls", "ignored.zip"])
            self.assertEqual(rows[0]["defaultTargetFormat"], "pdf")
            self.assertIn("docx", rows[0]["supportedTargets"])
            self.assertEqual(rows[1]["defaultTargetFormat"], "pdf")
            self.assertIn("xlsx", rows[1]["supportedTargets"])
            self.assertEqual(rows[2]["supportedTargets"], [])
            self.assertIsNone(rows[2]["defaultTargetFormat"])

    def test_job_uses_per_file_target_formats_from_table(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "docs"
            source.mkdir()
            doc = source / "a.doc"
            sheet = source / "b.xls"
            doc.write_text("doc")
            sheet.write_text("xls")
            fake_soffice = root / "soffice"
            fake_soffice.write_text(FAKE_SOFFICE)
            fake_soffice.chmod(0o755)

            manager = JobManager(str(fake_soffice), run_async=False)
            job = manager.create_job(
                [str(source)],
                str(root / "out"),
                "target",
                recursive=True,
                file_options=[
                    {"source": str(doc), "targetFormat": "docx"},
                    {"source": str(sheet), "targetFormat": "pdf"},
                ],
            )

            self.assertEqual(job.status, "completed")
            self.assertTrue((root / "out" / "a.docx").exists())
            self.assertTrue((root / "out" / "b.pdf").exists())
            self.assertEqual([result.target_format for result in job.results], ["docx", "pdf"])

    def test_file_options_limit_job_to_selected_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "docs"
            source.mkdir()
            selected = source / "selected.doc"
            removed = source / "removed.doc"
            selected.write_text("doc")
            removed.write_text("doc")
            fake_soffice = root / "soffice"
            fake_soffice.write_text(FAKE_SOFFICE)
            fake_soffice.chmod(0o755)

            manager = JobManager(str(fake_soffice), run_async=False)
            job = manager.create_job(
                [str(source)],
                str(root / "out"),
                "target",
                recursive=True,
                file_options=[{"source": str(selected), "targetFormat": "pdf"}],
            )

            self.assertEqual(len(job.results), 1)
            self.assertEqual(job.results[0].source, str(selected))
            self.assertTrue((root / "out" / "selected.pdf").exists())
            self.assertFalse((root / "out" / "removed.pdf").exists())


if __name__ == "__main__":
    unittest.main()
