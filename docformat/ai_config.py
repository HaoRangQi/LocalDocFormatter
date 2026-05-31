from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import os


DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_CONFIG_PATH = Path.home() / "Library" / "Application Support" / "DocFormat" / "ai-config.json"


@dataclass(frozen=True)
class AIConfig:
    base_url: str = DEFAULT_BASE_URL
    api_key: str = ""
    selected_model: str = ""


def normalize_base_url(base_url: str) -> str:
    value = (base_url or DEFAULT_BASE_URL).strip().rstrip("/")
    if not value:
        return DEFAULT_BASE_URL
    if value.endswith("/v1"):
        return value
    return f"{value}/v1"


def mask_key(api_key: str) -> str:
    if not api_key:
        return ""
    if len(api_key) < 8:
        return "*" * len(api_key)
    return f"{api_key[:4]}...{api_key[-4:]}"


class AIConfigStore:
    def __init__(self, path: Path = DEFAULT_CONFIG_PATH) -> None:
        self.path = path

    def load(self) -> AIConfig:
        if not self.path.exists():
            return AIConfig()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return AIConfig()
        return AIConfig(
            base_url=normalize_base_url(str(data.get("base_url") or DEFAULT_BASE_URL)),
            api_key=str(data.get("api_key") or ""),
            selected_model=str(data.get("selected_model") or ""),
        )

    def save(self, base_url: str, api_key: str | None, selected_model: str) -> AIConfig:
        existing = self.load()
        config = AIConfig(
            base_url=normalize_base_url(base_url),
            api_key=existing.api_key if api_key is None else api_key,
            selected_model=(selected_model or "").strip(),
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp_path.write_text(
            json.dumps(
                {
                    "base_url": config.base_url,
                    "api_key": config.api_key,
                    "selected_model": config.selected_model,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        os.chmod(tmp_path, 0o600)
        os.replace(tmp_path, self.path)
        os.chmod(self.path, 0o600)
        return config

    def public_config(self) -> dict:
        config = self.load()
        return {
            "baseUrl": config.base_url,
            "selectedModel": config.selected_model,
            "hasApiKey": bool(config.api_key),
            "apiKeyMasked": mask_key(config.api_key),
        }
