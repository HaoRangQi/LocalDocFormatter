from __future__ import annotations

from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
import json
import mimetypes
import os
import secrets
import subprocess
import sys
import webbrowser

from . import __version__
from .ai_client import OpenAICompatibleClient, OpenAICompatibleError
from .ai_config import AIConfigStore
from .ai_jobs import AICorrectionJobManager, ai_job_to_dict
from .ai_jobs import sanitize_error
from .config import discover_soffice
from .converter import CALC_INPUTS, IMPRESS_INPUTS, MODERNIZE_SPECS, WRITER_INPUTS
from .converter import is_skippable
from .jobs import JobManager, job_to_dict
from .text_correction import LexiconFileError, load_lexicon_file


STATIC_DIR = Path(__file__).parent / "web"


@dataclass
class DocFormatApp:
    token: str
    manager: JobManager
    ai_config_store: AIConfigStore
    ai_job_manager: AICorrectionJobManager
    ai_client_class: type = OpenAICompatibleClient
    explicit_soffice_path: str | None = None

    def handle_json(
        self,
        method: str,
        path: str,
        payload: dict | None,
        headers: dict[str, str],
    ) -> tuple[int, dict[str, str], str]:
        parsed = urlparse(path)
        route = parsed.path

        if method == "GET" and route == "/api/health":
            return self._json(HTTPStatus.OK, self.health_payload())

        if route.startswith("/api/ai/"):
            return self._handle_ai(method, route, payload, headers)

        if route.startswith("/api/pick"):
            if not self._authorized(headers):
                return self._json(HTTPStatus.FORBIDDEN, {"error": "Invalid local token"})
            query = parse_qs(parsed.query)
            kind = query.get("kind", ["files"])[0]
            return self._json(HTTPStatus.OK, {"paths": pick_paths(kind)})

        if method == "GET" and route == "/api/browse":
            if not self._authorized(headers):
                return self._json(HTTPStatus.FORBIDDEN, {"error": "Invalid local token"})
            query = parse_qs(parsed.query)
            requested_path = query.get("path", [""])[0]
            try:
                return self._json(HTTPStatus.OK, browse_workspace(requested_path))
            except BrowseError as exc:
                return self._json(exc.status, {"error": str(exc)})

        if method == "POST" and route == "/api/scan":
            if not self._authorized(headers):
                return self._json(HTTPStatus.FORBIDDEN, {"error": "Invalid local token"})
            payload = payload or {}
            sources = payload.get("sources") or []
            recursive = bool(payload.get("recursive", True))
            enable_ai_correction = bool(payload.get("enableAiCorrection", False))
            if not isinstance(sources, list) or not all(isinstance(item, str) for item in sources):
                return self._json(HTTPStatus.BAD_REQUEST, {"error": "sources must be a list of paths"})
            if not sources:
                return self._json(HTTPStatus.BAD_REQUEST, {"error": "sources cannot be empty"})
            files = self.manager.scan_files(sources, recursive=recursive, correction_enabled=enable_ai_correction)
            return self._json(HTTPStatus.OK, {"count": len(files), "files": files})

        if method == "POST" and route == "/api/jobs":
            if not self._authorized(headers):
                return self._json(HTTPStatus.FORBIDDEN, {"error": "Invalid local token"})
            payload = payload or {}
            sources = payload.get("sources") or []
            mode = payload.get("mode") or "modernize"
            target_format = str(payload.get("targetFormat") or "").strip().lower().lstrip(".") or None
            enable_ai_correction = bool(payload.get("enableAiCorrection", False))
            user_lexicon = str(payload.get("userLexicon") or "")
            correction_prompt = str(payload.get("correctionPrompt") or "")
            lexicon_entries = payload.get("lexiconEntries") or []
            lexicon_file_paths = payload.get("lexiconFilePaths") or []
            file_options = payload.get("files") or payload.get("fileOptions") or []
            output_dir = payload.get("outputDir") or None
            recursive = bool(payload.get("recursive", True))
            if not isinstance(sources, list) or not all(isinstance(item, str) for item in sources):
                return self._json(HTTPStatus.BAD_REQUEST, {"error": "sources must be a list of paths"})
            if not isinstance(lexicon_entries, list) or not all(isinstance(item, dict) for item in lexicon_entries):
                return self._json(HTTPStatus.BAD_REQUEST, {"error": "lexiconEntries must be a list of key-value objects"})
            if not isinstance(lexicon_file_paths, list) or not all(isinstance(item, str) for item in lexicon_file_paths):
                return self._json(HTTPStatus.BAD_REQUEST, {"error": "lexiconFilePaths must be a list of paths"})
            if not isinstance(file_options, list) or not all(isinstance(item, dict) for item in file_options):
                return self._json(HTTPStatus.BAD_REQUEST, {"error": "files must be a list of file option objects"})
            if not sources:
                return self._json(HTTPStatus.BAD_REQUEST, {"error": "sources cannot be empty"})
            if mode not in {"modernize", "pdf", "target"}:
                return self._json(HTTPStatus.BAD_REQUEST, {"error": "mode must be modernize, pdf, or target"})
            correction_config = None
            if enable_ai_correction:
                config = self.ai_config_store.load()
                if not config.api_key:
                    return self._json(HTTPStatus.BAD_REQUEST, {"error": "API key is required for AI correction"})
                if not config.selected_model:
                    return self._json(HTTPStatus.BAD_REQUEST, {"error": "Selected model is required for AI correction"})
                correction_config = {
                    "baseUrl": config.base_url,
                    "apiKey": config.api_key,
                    "selectedModel": config.selected_model,
                }
                mode = "target"
                target_format = target_format or "docx"
            job = self.manager.create_job(
                sources,
                output_dir,
                mode,
                recursive,
                target_format=target_format,
                correction_config=correction_config,
                correction_user_lexicon=user_lexicon,
                correction_prompt=correction_prompt,
                correction_entries=lexicon_entries,
                correction_lexicon_files=lexicon_file_paths,
                file_options=file_options,
            )
            return self._json(HTTPStatus.CREATED, job_to_dict(job))

        if route.startswith("/api/jobs/"):
            parts = route.strip("/").split("/")
            if len(parts) not in {3, 4}:
                return self._json(HTTPStatus.NOT_FOUND, {"error": "Not found"})
            job_id = parts[2]
            if not self._authorized(headers):
                return self._json(HTTPStatus.FORBIDDEN, {"error": "Invalid local token"})
            job = self.manager.get_job(job_id)
            if job is None:
                return self._json(HTTPStatus.NOT_FOUND, {"error": "Job not found"})
            if method == "GET" and len(parts) == 3:
                return self._json(HTTPStatus.OK, job_to_dict(job))
            if method == "POST" and len(parts) == 4 and parts[3] == "cancel":
                cancelled = self.manager.cancel_job(job_id)
                return self._json(HTTPStatus.OK, {"cancelled": cancelled, "job": job_to_dict(job)})

        return self._json(HTTPStatus.NOT_FOUND, {"error": "Not found"})

    def _handle_ai(
        self,
        method: str,
        route: str,
        payload: dict | None,
        headers: dict[str, str],
    ) -> tuple[int, dict[str, str], str]:
        if not self._authorized(headers):
            return self._json(HTTPStatus.FORBIDDEN, {"error": "Invalid local token"})

        if route == "/api/ai/config" and method == "GET":
            return self._json(HTTPStatus.OK, self.ai_config_store.public_config())

        if route == "/api/ai/config" and method == "POST":
            payload = payload or {}
            base_url = str(payload.get("baseUrl") or "").strip()
            api_key = payload.get("apiKey")
            if api_key == "":
                api_key = None
            selected_model = str(payload.get("selectedModel") or "").strip()
            self.ai_config_store.save(base_url, api_key, selected_model)
            return self._json(HTTPStatus.OK, self.ai_config_store.public_config())

        if route == "/api/ai/models/refresh" and method == "POST":
            config = self.ai_config_store.load()
            if not config.api_key:
                return self._json(HTTPStatus.BAD_REQUEST, {"error": "API key is required"})
            try:
                models = self.ai_client_class(config.base_url, config.api_key).list_models()
            except OpenAICompatibleError as exc:
                return self._json(HTTPStatus.BAD_GATEWAY, {"error": sanitize_error(str(exc), config.api_key)})
            return self._json(HTTPStatus.OK, {"models": models})

        if route == "/api/ai/lexicon/preview" and method == "POST":
            payload = payload or {}
            paths = payload.get("paths") or []
            if not isinstance(paths, list) or not all(isinstance(item, str) for item in paths):
                return self._json(HTTPStatus.BAD_REQUEST, {"error": "paths must be a list of paths"})
            previews = [_preview_lexicon_file(path) for path in paths]
            total = sum(item["count"] for item in previews if item["status"] == "success")
            return self._json(HTTPStatus.OK, {"files": previews, "totalValidEntries": total})

        if route == "/api/ai/correction-jobs" and method == "POST":
            payload = payload or {}
            config = self.ai_config_store.load()
            if not config.api_key:
                return self._json(HTTPStatus.BAD_REQUEST, {"error": "API key is required"})
            if not config.selected_model:
                return self._json(HTTPStatus.BAD_REQUEST, {"error": "Selected model is required"})
            text = str(payload.get("text") or "")
            file_paths = payload.get("filePaths") or []
            user_lexicon = str(payload.get("userLexicon") or "")
            if not isinstance(file_paths, list) or not all(isinstance(item, str) for item in file_paths):
                return self._json(HTTPStatus.BAD_REQUEST, {"error": "filePaths must be a list of paths"})
            if not text.strip() and not file_paths:
                return self._json(HTTPStatus.BAD_REQUEST, {"error": "text or filePaths is required"})
            job = self.ai_job_manager.create_job(
                {
                    "baseUrl": config.base_url,
                    "apiKey": config.api_key,
                    "selectedModel": config.selected_model,
                },
                text,
                file_paths,
                user_lexicon,
            )
            return self._json(HTTPStatus.CREATED, ai_job_to_dict(job))

        if route.startswith("/api/ai/correction-jobs/"):
            parts = route.strip("/").split("/")
            if len(parts) not in {4, 5}:
                return self._json(HTTPStatus.NOT_FOUND, {"error": "Not found"})
            job_id = parts[3]
            job = self.ai_job_manager.get_job(job_id)
            if job is None:
                return self._json(HTTPStatus.NOT_FOUND, {"error": "AI correction job not found"})
            if method == "GET" and len(parts) == 4:
                return self._json(HTTPStatus.OK, ai_job_to_dict(job))
            if method == "POST" and len(parts) == 5 and parts[4] == "cancel":
                cancelled = self.ai_job_manager.cancel_job(job_id)
                return self._json(HTTPStatus.OK, {"cancelled": cancelled, "job": ai_job_to_dict(job)})

        return self._json(HTTPStatus.NOT_FOUND, {"error": "Not found"})

    def health_payload(self) -> dict:
        info = discover_soffice()
        if self.explicit_soffice_path:
            info = type(info)(True, self.explicit_soffice_path, info.install_hint)
        workspace_roots = resolve_workspace_roots()
        return {
            "version": __version__,
            "token": self.token,
            "libreOffice": {
                "found": info.found,
                "path": info.path,
                "installHint": info.install_hint,
            },
            "modes": {
                "modernize": sorted(MODERNIZE_SPECS),
                "pdf": sorted(WRITER_INPUTS | CALC_INPUTS | IMPRESS_INPUTS),
            },
            "runtime": {
                "container": is_container_runtime(),
                "workspaceRoots": [str(path) for path in workspace_roots],
                "pathHint": runtime_path_hint(workspace_roots),
            },
        }

    def _authorized(self, headers: dict[str, str]) -> bool:
        return any(key.lower() == "x-docformat-token" and value == self.token for key, value in headers.items())

    @staticmethod
    def _json(status: HTTPStatus | int, payload: dict) -> tuple[int, dict[str, str], str]:
        body = json.dumps(payload, ensure_ascii=False)
        return int(status), {"Content-Type": "application/json; charset=utf-8"}, body


def create_app(
    token: str | None = None,
    soffice_path: str | None = None,
    run_async: bool = True,
    ai_config_path: Path | None = None,
    ai_client_class: type = OpenAICompatibleClient,
) -> DocFormatApp:
    if soffice_path is None:
        discovered = discover_soffice()
        soffice_path = discovered.path
    if ai_config_path is None and os.environ.get("DOCFORMAT_AI_CONFIG_PATH"):
        ai_config_path = Path(os.environ["DOCFORMAT_AI_CONFIG_PATH"])
    ai_config_store = AIConfigStore(ai_config_path) if ai_config_path else AIConfigStore()

    def ai_client_factory(config: dict) -> object:
        return ai_client_class(str(config.get("baseUrl") or ""), str(config.get("apiKey") or ""))

    return DocFormatApp(
        token=token or secrets.token_urlsafe(24),
        manager=JobManager(soffice_path, run_async=run_async, correction_client_factory=ai_client_factory),
        ai_config_store=ai_config_store,
        ai_job_manager=AICorrectionJobManager(client_factory=ai_client_factory, run_async=run_async),
        ai_client_class=ai_client_class,
        explicit_soffice_path=soffice_path,
    )


class RequestHandler(BaseHTTPRequestHandler):
    app: DocFormatApp

    def do_GET(self) -> None:  # noqa: N802 - stdlib API
        if self.path.startswith("/api/"):
            self._handle_api(None)
        else:
            self._serve_static()

    def do_POST(self) -> None:  # noqa: N802 - stdlib API
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            self._write_json(HTTPStatus.BAD_REQUEST, {"error": "Invalid JSON"})
            return
        self._handle_api(payload)

    def log_message(self, format: str, *args: object) -> None:
        sys.stderr.write("[DocFormat] " + format % args + "\n")

    def _handle_api(self, payload: dict | None) -> None:
        headers = {key: value for key, value in self.headers.items()}
        status, response_headers, body = self.app.handle_json(self.command, self.path, payload, headers)
        self.send_response(status)
        for key, value in response_headers.items():
            self.send_header(key, value)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))

    def _write_json(self, status: HTTPStatus, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_static(self) -> None:
        parsed = urlparse(self.path)
        relative = parsed.path.lstrip("/") or "index.html"
        if relative == "app-config.js":
            body = f"window.DOCFORMAT_TOKEN = {json.dumps(self.app.token)};\n".encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/javascript; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        target = (STATIC_DIR / relative).resolve()
        if not str(target).startswith(str(STATIC_DIR.resolve())) or not target.exists() or not target.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        body = target.read_bytes()
        content_type = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _preview_lexicon_file(path_text: str) -> dict:
    path = Path(path_text).expanduser()
    try:
        pairs = load_lexicon_file(path)
    except LexiconFileError as exc:
        return {
            "path": str(path),
            "status": "failed",
            "count": 0,
            "sample": [],
            "error": str(exc),
        }
    return {
        "path": str(path),
        "status": "success",
        "count": len(pairs),
        "sample": [{"wrong": wrong, "correct": correct} for wrong, correct in pairs[:5]],
        "error": None,
    }


def pick_paths(kind: str) -> list[str]:
    if sys.platform != "darwin":
        return []
    if kind == "directory":
        script = 'POSIX path of (choose folder with prompt "选择文件夹")'
    else:
        script = (
            'set chosenFiles to choose file with prompt "选择要转换的文件" with multiple selections allowed\n'
            "set outputPaths to {}\n"
            "repeat with chosenFile in chosenFiles\n"
            "set end of outputPaths to POSIX path of chosenFile\n"
            "end repeat\n"
            'set AppleScript\'s text item delimiters to "\n"\n'
            "outputPaths as text"
        )
    try:
        completed = subprocess.run(
            ["osascript", "-e", script],
            check=False,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if completed.returncode != 0:
        return []
    return [line for line in completed.stdout.splitlines() if line.strip()]


class BrowseError(ValueError):
    def __init__(self, status: HTTPStatus, message: str) -> None:
        super().__init__(message)
        self.status = status


def is_container_runtime() -> bool:
    value = os.environ.get("DOCFORMAT_CONTAINER", "").strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    return Path("/.dockerenv").exists()


def resolve_workspace_roots() -> list[Path]:
    configured = os.environ.get("DOCFORMAT_WORKSPACE_ROOTS", "").strip()
    raw_paths: list[str]
    if configured:
        raw_paths = []
        for chunk in configured.replace(",", os.pathsep).split(os.pathsep):
            value = chunk.strip()
            if value:
                raw_paths.append(value)
    else:
        raw_paths = ["/workspace"] if is_container_runtime() else []

    roots: list[Path] = []
    seen: set[str] = set()
    for raw_path in raw_paths:
        path = Path(raw_path).expanduser().resolve()
        if not path.exists() or not path.is_dir():
            continue
        key = str(path)
        if key not in seen:
            roots.append(path)
            seen.add(key)
    return roots


def runtime_path_hint(workspace_roots: list[Path]) -> str:
    if not workspace_roots:
        return "macOS 本机运行时可用原生选择框；Docker 运行时请挂载目录到 /workspace。"
    joined = "、".join(str(path) for path in workspace_roots)
    if is_container_runtime():
        return f"Docker 模式：使用已挂载到容器内的路径，例如 {joined}。"
    return f"可浏览的工作目录：{joined}。"


def browse_workspace(requested_path: str = "") -> dict:
    roots = resolve_workspace_roots()
    if not roots:
        raise BrowseError(HTTPStatus.BAD_REQUEST, "No workspace roots are configured")

    if requested_path.strip():
        target = Path(requested_path).expanduser().resolve()
    else:
        target = roots[0]

    if not _is_under_any_root(target, roots):
        raise BrowseError(HTTPStatus.BAD_REQUEST, "Requested path is outside allowed workspace roots")
    if not target.exists():
        raise BrowseError(HTTPStatus.NOT_FOUND, "Requested path does not exist")
    if not target.is_dir():
        raise BrowseError(HTTPStatus.BAD_REQUEST, "Requested path must be a directory")

    nearest_root = _nearest_root(target, roots)
    parent = None
    if nearest_root is not None and target != nearest_root:
        candidate_parent = target.parent.resolve()
        if _is_under_any_root(candidate_parent, roots):
            parent = str(candidate_parent)

    entries = []
    try:
        children = list(target.iterdir())
    except OSError as exc:
        raise BrowseError(HTTPStatus.BAD_REQUEST, f"Cannot read directory: {exc}") from exc

    for child in sorted(children, key=lambda item: (not item.is_dir(), item.name.lower())):
        if is_skippable(Path(child.name)):
            continue
        try:
            stat = child.stat()
        except OSError:
            continue
        entries.append(
            {
                "name": child.name,
                "path": str(child),
                "kind": "directory" if child.is_dir() else "file",
                "sizeBytes": 0 if child.is_dir() else stat.st_size,
            }
        )

    return {
        "path": str(target),
        "parent": parent,
        "roots": [str(path) for path in roots],
        "entries": entries,
    }


def _is_under_any_root(path: Path, roots: list[Path]) -> bool:
    resolved = path.resolve()
    return any(resolved == root or root in resolved.parents for root in roots)


def _nearest_root(path: Path, roots: list[Path]) -> Path | None:
    resolved = path.resolve()
    candidates = [root for root in roots if resolved == root or root in resolved.parents]
    return max(candidates, key=lambda root: len(str(root)), default=None)


def main() -> None:
    host = os.environ.get("DOCFORMAT_HOST", "127.0.0.1")
    port = int(os.environ.get("DOCFORMAT_PORT", "8765"))
    app = create_app()

    class BoundHandler(RequestHandler):
        pass

    BoundHandler.app = app
    server = ThreadingHTTPServer((host, port), BoundHandler)
    url = f"http://{host}:{port}/"
    print(f"DocFormat running at {url}")
    print("Press Ctrl+C to stop.")
    try:
        if os.environ.get("DOCFORMAT_NO_BROWSER") != "1":
            webbrowser.open(url)
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping DocFormat.")
    finally:
        server.server_close()
