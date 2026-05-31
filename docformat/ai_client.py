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
        content = self._request_stream(
            "POST",
            "/chat/completions",
            {
                "model": model,
                "messages": messages,
                "temperature": 0,
                "stream": True,
            },
        )
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

    def _request_stream(self, method: str, path: str, body: dict) -> str:
        url = f"{self.base_url}{path}"
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers = {
            "Accept": "text/event-stream",
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        req = request.Request(url, data=data, headers=headers, method=method)
        try:
            with request.urlopen(req, timeout=self.timeout_seconds) as response:
                return _read_chat_completion_stream(response)
        except error.HTTPError as exc:
            message = _extract_error_message(exc)
            raise OpenAICompatibleError(f"AI provider returned HTTP {exc.code}: {message}") from exc
        except (error.URLError, TimeoutError, OSError) as exc:
            raise OpenAICompatibleError(f"AI provider request failed: {exc}") from exc


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


def _parse_chat_completion_stream(raw: str) -> str:
    chunks: list[str] = []
    for payload in _iter_sse_data(raw):
        if _append_chat_completion_event(payload, chunks):
            break
    return "".join(chunks)


def _read_chat_completion_stream(response) -> str:
    chunks: list[str] = []
    data_lines: list[str] = []
    for raw_line in response:
        line = raw_line.decode("utf-8").rstrip("\r\n")
        if not line:
            if data_lines and _append_chat_completion_event("\n".join(data_lines), chunks):
                break
            data_lines = []
            continue
        if line.startswith("data:"):
            data_lines.append(line.removeprefix("data:").lstrip())
    if data_lines:
        _append_chat_completion_event("\n".join(data_lines), chunks)
    return "".join(chunks)


def _append_chat_completion_event(payload: str, chunks: list[str]) -> bool:
    if payload == "[DONE]":
        return True
    try:
        event = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise OpenAICompatibleError("AI provider returned invalid stream JSON") from exc
    if not isinstance(event, dict):
        return False
    error_payload = event.get("error")
    if isinstance(error_payload, dict) and error_payload.get("message"):
        raise OpenAICompatibleError(str(error_payload["message"]))
    for choice in event.get("choices") or []:
        if not isinstance(choice, dict):
            continue
        delta = choice.get("delta")
        if isinstance(delta, dict) and isinstance(delta.get("content"), str):
            chunks.append(delta["content"])
        if isinstance(delta, dict) and isinstance(delta.get("refusal"), str):
            chunks.append(delta["refusal"])
        message = choice.get("message")
        if isinstance(message, dict) and isinstance(message.get("content"), str):
            chunks.append(message["content"])
    return False


def _iter_sse_data(raw: str):
    for event_block in raw.replace("\r\n", "\n").split("\n\n"):
        data_lines = []
        for line in event_block.split("\n"):
            if line.startswith("data:"):
                data_lines.append(line.removeprefix("data:").lstrip())
        if data_lines:
            yield "\n".join(data_lines)
