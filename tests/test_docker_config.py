import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class DockerConfigTests(unittest.TestCase):
    def test_dockerfile_packages_runtime_and_container_defaults(self):
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

        self.assertIn("FROM python:3.12-slim-bookworm", dockerfile)
        self.assertIn("libreoffice", dockerfile)
        self.assertIn("fonts-noto-cjk", dockerfile)
        self.assertIn("DOCFORMAT_CONTAINER=1", dockerfile)
        self.assertIn("DOCFORMAT_WORKSPACE_ROOTS=/workspace", dockerfile)
        self.assertIn("DOCFORMAT_AI_CONFIG_PATH=/data/ai-config.json", dockerfile)
        self.assertIn("USER appuser", dockerfile)
        self.assertIn("HEALTHCHECK", dockerfile)

    def test_compose_binds_localhost_and_mounts_data_workspace(self):
        compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")

        self.assertIn("127.0.0.1:8765:8765", compose)
        self.assertIn("DOCFORMAT_CONTAINER: \"1\"", compose)
        self.assertIn("DOCFORMAT_WORKSPACE_ROOTS: \"/workspace\"", compose)
        self.assertIn("./docker-data:/data", compose)
        self.assertIn("~/Documents:/workspace/Documents", compose)
        self.assertIn("~/Downloads:/workspace/Downloads", compose)
        self.assertIn("healthcheck:", compose)

    def test_readme_documents_compose_and_direct_docker_run(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")

        self.assertIn("docker compose up --build", readme)
        self.assertIn("docker build -t localdocformatter:local .", readme)
        self.assertIn("docker run --rm", readme)
        self.assertIn("/workspace/Documents", readme)
        self.assertIn("DOCFORMAT_WORKSPACE_ROOTS", readme)


if __name__ == "__main__":
    unittest.main()
