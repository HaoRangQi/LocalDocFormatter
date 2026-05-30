import tempfile
import time
import unittest
from pathlib import Path

from docformat.jobs import JobManager


class PerformanceBoundaryTests(unittest.TestCase):
    def test_scan_many_files_skips_hidden_and_lock_files_with_bounded_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for index in range(300):
                (root / f"draft-{index:03d}.doc").write_text("doc")
            for index in range(50):
                (root / f".hidden-{index:03d}.doc").write_text("hidden")
                (root / f"~$lock-{index:03d}.docx").write_text("lock")
            manager = JobManager(soffice_path=None, run_async=False)

            started = time.monotonic()
            files = manager.scan_files([str(root)], recursive=False)
            elapsed = time.monotonic() - started

        self.assertEqual(len(files), 300)
        self.assertLess(elapsed, 1.5)
        self.assertTrue(all(not Path(item["source"]).name.startswith(("~$", ".")) for item in files))


if __name__ == "__main__":
    unittest.main()
