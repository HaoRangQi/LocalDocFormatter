from __future__ import annotations

from urllib import error, request
import json

from .ai_config import normalize_base_url


class OpenAICompatibleError(RuntimeError):
    pass


class OpenAICompatibleClient:
    def __init__(self, base_url: str, api_key: str, timeout_seconds: int = 60) -> None:
        self.base_url = normalize_base_url(base_url)
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds

    def list_models(self) -> list[str]:
        payload = self._request("GET", "/models")
        data = payload.get("data", [])
        models = [str(item.get("id")) for item in data if isinstance(item, dict) and item.get("id")]
        return sorted(models)

    def chat_completion(self, model: str, messages: list[dict[str, str]]) -> str:
        payload = self._request(
            "POST",
            "/chat/completions",
            {
                "model": model,
                "messages": messages,
                "temperature": 0,
            },
        )
        try:
            content = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise OpenAICompatibleError("AI response did not include message content") from exc
        if not isinstance(content, str) or not content.strip():
            raise OpenAICompatibleError("AI response was empty")
        return content

    def _request(self, method: str, path: str, body: dict | None = None) -> dict:
        url = f"{self.base_url}{path}"
        data = None
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = request.Request(url, data=data, headers=headers, method=method)
        try:
            with request.urlopen(req, timeout=self.timeout_seconds) as response:
                raw = response.read().decode("utf-8")
        except error.HTTPError as exc:
            message = _extract_error_message(exc)
            raise OpenAICompatibleError(f"AI provider returned HTTP {exc.code}: {message}") from exc
        except (error.URLError, TimeoutError, OSError) as exc:
            raise OpenAICompatibleError(f"AI provider request failed: {exc}") from exc
        try:
            parsed = json.loads(raw or "{}")
        except json.JSONDecodeError as exc:
            raise OpenAICompatibleError("AI provider returned invalid JSON") from exc
        if not isinstance(parsed, dict):
            raise OpenAICompatibleError("AI provider returned an unexpected response")
        return parsed


def _extract_error_message(exc: error.HTTPError) -> str:
    try:
        raw = exc.read().decode("utf-8")
        payload = json.loads(raw)
        if isinstance(payload, dict):
            error_payload = payload.get("error")
            if isinstance(error_payload, dict) and error_payload.get("message"):
                return str(error_payload["message"])
            if payload.get("message"):
                return str(payload["message"])
    except Exception:
        pass
    return exc.reason or "request failed"

