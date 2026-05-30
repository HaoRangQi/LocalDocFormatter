import tempfile
import unittest
from pathlib import Path

from docformat.text_correction import (
    BUILTIN_LEXICON,
    build_correction_messages,
    chunk_text,
    correct_srt_text,
    load_lexicon_file,
    parse_user_lexicon,
    normalize_lexicon_entries,
    unique_corrected_path,
)


class TextCorrectionTests(unittest.TestCase):
    def test_parse_user_lexicon_accepts_arrow_lines(self):
        pairs = parse_user_lexicon("张三丰 => 张三\nopen ai=>OpenAI\ninvalid")

        self.assertEqual(pairs, [("张三丰", "张三"), ("open ai", "OpenAI")])

    def test_prompt_forbids_rewriting_and_includes_lexicon(self):
        messages = build_correction_messages("这个是转译文稿", [("张三丰", "张三")])
        joined = "\n".join(message["content"] for message in messages)

        self.assertIn("禁止润色", joined)
        self.assertIn("只输出修正后的全文", joined)
        self.assertIn("张三丰 => 张三", joined)
        self.assertTrue(BUILTIN_LEXICON)

    def test_custom_prompt_overrides_default_system_prompt(self):
        messages = build_correction_messages("这个是转译文稿", [], system_prompt="只修错别字")

        self.assertEqual(messages[0]["content"], "只修错别字")

    def test_normalize_lexicon_entries_accepts_key_value_rows(self):
        entries = normalize_lexicon_entries(
            [
                {"wrong": "在见", "correct": "再见"},
                {"key": "open ai", "value": "OpenAI"},
                {"wrong": "", "correct": "跳过"},
            ]
        )

        self.assertEqual(entries, [("在见", "再见"), ("open ai", "OpenAI")])

    def test_load_lexicon_file_supports_csv_and_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            csv_path = Path(tmp) / "words.csv"
            csv_path.write_text("错误词,正确词\n在见,再见\n", encoding="utf-8")
            json_path = Path(tmp) / "words.json"
            json_path.write_text('[{"wrong": "帐号", "correct": "账号"}]', encoding="utf-8")

            self.assertEqual(load_lexicon_file(csv_path), [("在见", "再见")])
            self.assertEqual(load_lexicon_file(json_path), [("帐号", "账号")])

    def test_chunk_text_prefers_paragraph_boundaries(self):
        chunks = chunk_text("第一段\n\n第二段很长\n\n第三段", max_chars=8)

        self.assertGreater(len(chunks), 1)
        self.assertEqual("".join(chunks), "第一段\n\n第二段很长\n\n第三段")

    def test_srt_correction_preserves_indices_and_timestamps(self):
        def fake_correct(text):
            return text.replace("在见", "再见")

        source = "1\n00:00:01,000 --> 00:00:02,000\n大家在见\n\n2\n00:00:03,000 --> 00:00:04,000\n谢谢\n"

        result = correct_srt_text(source, fake_correct)

        self.assertIn("1\n00:00:01,000 --> 00:00:02,000\n大家再见", result)
        self.assertIn("2\n00:00:03,000 --> 00:00:04,000\n谢谢", result)

    def test_unique_corrected_path_does_not_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "draft.txt"
            source.write_text("a")
            existing = Path(tmp) / "draft.corrected.txt"
            existing.write_text("b")

            result = unique_corrected_path(source)

            self.assertEqual(result, Path(tmp) / "draft.corrected (1).txt")


if __name__ == "__main__":
    unittest.main()
