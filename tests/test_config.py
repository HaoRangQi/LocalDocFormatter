import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from docformat.config import default_output_dir, discover_soffice


class ConfigTests(unittest.TestCase):
    def test_default_output_dir_for_file_uses_source_parent(self):
        self.assertEqual(default_output_dir(Path("/tmp/source/a.doc")), Path("/tmp/source/converted"))

    def test_default_output_dir_for_directory_uses_directory(self):
        self.assertEqual(default_output_dir(Path("/tmp/source")), Path("/tmp/source/converted"))

    def test_discovers_soffice_from_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = Path(tmp)
            fake = bin_dir / "soffice"
            fake.write_text("#!/bin/sh\n")
            fake.chmod(0o755)
            with (
                mock.patch("docformat.config.Path.exists", return_value=False),
                mock.patch("docformat.config.which", return_value=str(fake)),
                mock.patch.dict(os.environ, {"PATH": str(bin_dir)}),
            ):
                info = discover_soffice()

        self.assertTrue(info.found)
        self.assertEqual(info.path, str(fake))


if __name__ == "__main__":
    unittest.main()
