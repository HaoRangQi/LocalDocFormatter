import json
import os
import stat
import tempfile
import unittest
from pathlib import Path

from docformat.ai_config import AIConfigStore, mask_key


class AIConfigTests(unittest.TestCase):
    def test_mask_key_never_returns_full_secret(self):
        self.assertEqual(mask_key("sk-abcdefghijklmnopqrstuvwxyz"), "sk-a...wxyz")
        self.assertEqual(mask_key("short"), "*****")
        self.assertEqual(mask_key(""), "")

    def test_save_load_and_public_config_are_masked(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ai-config.json"
            store = AIConfigStore(path)

            config = store.save(
                base_url="https://relay.example.com/v1/",
                api_key="sk-secret-value",
                selected_model="gpt-test",
            )

            self.assertEqual(config.base_url, "https://relay.example.com/v1")
            self.assertEqual(store.load().api_key, "sk-secret-value")
            public = store.public_config()
            self.assertTrue(public["hasApiKey"])
            self.assertEqual(public["apiKeyMasked"], "sk-s...alue")
            self.assertNotIn("sk-secret-value", json.dumps(public))
            mode = stat.S_IMODE(os.stat(path).st_mode)
            self.assertEqual(mode, 0o600)

    def test_partial_save_preserves_existing_key_when_omitted(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = AIConfigStore(Path(tmp) / "ai-config.json")
            store.save("https://api.openai.com/v1", "sk-old", "gpt-old")
            store.save("https://api.openai.com/v1", None, "gpt-new")

            config = store.load()
            self.assertEqual(config.api_key, "sk-old")
            self.assertEqual(config.selected_model, "gpt-new")


if __name__ == "__main__":
    unittest.main()
