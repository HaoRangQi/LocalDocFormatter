import tempfile
import unittest
from pathlib import Path

from docformat.converter import (
    ConversionSpec,
    LibreOfficeConverter,
    get_conversion_spec,
    supported_target_formats,
    is_skippable,
    unique_output_path,
)


class ConversionMappingTests(unittest.TestCase):
    def test_modernize_maps_common_office_formats(self):
        self.assertEqual(get_conversion_spec(Path("a.doc"), "modernize").target_ext, ".docx")
        self.assertEqual(get_conversion_spec(Path("a.ods"), "modernize").target_ext, ".xlsx")
        self.assertEqual(get_conversion_spec(Path("a.ppt"), "modernize").target_ext, ".pptx")

    def test_pdf_uses_family_specific_filters(self):
        self.assertEqual(get_conversion_spec(Path("a.docx"), "pdf").filter_name, "writer_pdf_Export")
        self.assertEqual(get_conversion_spec(Path("a.xlsx"), "pdf").filter_name, "calc_pdf_Export")
        self.assertEqual(get_conversion_spec(Path("a.pptx"), "pdf").filter_name, "impress_pdf_Export")

    def test_unknown_extension_returns_none(self):
        self.assertIsNone(get_conversion_spec(Path("a.zip"), "modernize"))
        self.assertIsNone(get_conversion_spec(Path("a.zip"), "pdf"))

    def test_specific_target_format_is_limited_by_source_family(self):
        self.assertEqual(get_conversion_spec(Path("a.doc"), "target", "docx").target_ext, ".docx")
        self.assertEqual(get_conversion_spec(Path("a.xls"), "target", "xlsx").target_ext, ".xlsx")
        self.assertEqual(get_conversion_spec(Path("a.ppt"), "target", "pptx").target_ext, ".pptx")
        self.assertEqual(get_conversion_spec(Path("a.doc"), "target", "pdf").filter_name, "writer_pdf_Export")
        self.assertIsNone(get_conversion_spec(Path("a.xls"), "target", "docx"))
        self.assertIsNone(get_conversion_spec(Path("a.ppt"), "target", "xlsx"))

    def test_supported_targets_are_detected_from_extension(self):
        self.assertEqual(supported_target_formats(Path("a.doc")), ["docx", "pdf", "txt"])
        self.assertEqual(supported_target_formats(Path("a.xls")), ["xlsx", "pdf"])
        self.assertEqual(supported_target_formats(Path("a.ppt")), ["pptx", "pdf"])
        self.assertEqual(supported_target_formats(Path("a.doc"), correction_enabled=True), ["docx", "pdf", "txt", "md"])
        self.assertEqual(supported_target_formats(Path("a.srt"), correction_enabled=True), ["docx", "pdf", "txt", "md", "srt"])
        self.assertEqual(supported_target_formats(Path("a.xls"), correction_enabled=True), [])

    def test_skips_hidden_and_office_lock_files(self):
        self.assertTrue(is_skippable(Path(".hidden.doc")))
        self.assertTrue(is_skippable(Path("~$draft.docx")))
        self.assertFalse(is_skippable(Path("draft.docx")))


class OutputPathTests(unittest.TestCase):
    def test_unique_output_preserves_relative_folder_and_adds_suffix(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            existing = root / "nested" / "draft.docx"
            existing.parent.mkdir()
            existing.write_text("existing")

            result = unique_output_path(root, Path("nested/draft.doc"), ".docx")

            self.assertEqual(result, root / "nested" / "draft (1).docx")


class CommandTests(unittest.TestCase):
    def test_build_command_uses_filter_and_outdir(self):
        converter = LibreOfficeConverter("/Applications/LibreOffice.app/Contents/MacOS/soffice")
        spec = ConversionSpec(".doc", ".docx", "MS Word 2007 XML", "writer")

        command = converter.build_command(Path("/tmp/in.doc"), Path("/tmp/out"), spec)

        self.assertEqual(command[0], "/Applications/LibreOffice.app/Contents/MacOS/soffice")
        self.assertIn("--headless", command)
        self.assertIn("--convert-to", command)
        self.assertIn("docx:MS Word 2007 XML", command)
        self.assertIn("--outdir", command)
        self.assertIn("/tmp/out", command)
        self.assertEqual(command[-1], "/tmp/in.doc")


if __name__ == "__main__":
    unittest.main()
