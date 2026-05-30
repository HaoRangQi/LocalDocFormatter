import unittest
from pathlib import Path


WEB_DIR = Path(__file__).resolve().parents[1] / "docformat" / "web"


class WebContractTests(unittest.TestCase):
    def test_ui_exposes_container_path_browser_contract(self):
        index = (WEB_DIR / "index.html").read_text(encoding="utf-8")
        script = (WEB_DIR / "app.js").read_text(encoding="utf-8")
        styles = (WEB_DIR / "styles.css").read_text(encoding="utf-8")

        self.assertIn('id="runtimePathHint"', index)
        self.assertIn('id="pathBrowser"', index)
        self.assertIn("/api/browse", script)
        self.assertIn("runtimeInfo.container", script)
        self.assertIn("openPathBrowser", script)
        self.assertIn(".path-browser", styles)

    def test_ui_keeps_token_header_for_mutating_and_browse_calls(self):
        script = (WEB_DIR / "app.js").read_text(encoding="utf-8")

        self.assertIn('"X-DocFormat-Token": token', script)
        self.assertIn("fetch(path", script)


if __name__ == "__main__":
    unittest.main()
