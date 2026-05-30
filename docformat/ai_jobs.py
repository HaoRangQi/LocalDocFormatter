from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal
import csv
import json
import threading
import time
import uuid
from datetime import datetime, timezone

from .ai_client import OpenAICompatibleClient, OpenAICompatibleError
from .text_correction import (
    correct_srt_text,
    correct_text_with_client,
    is_supported_text_file,
    parse_user_lexicon,
    unique_corrected_path,
)


AIJobStatus = Literal["queued", "running", "completed", "cancelled", "failed"]
AIFileStatus = Literal["success", "failed", "skipped"]


@dataclass
class AICorrectionResult:
    source: str | None = None
    target: str | None = None
    status: AIFileStatus = "success"
    error: str | None = None
    elapsed_seconds: float | None = None


@dataclass
class AICorrectionJob:
    id: str
    model: str
    status: AIJobStatus = "queued"
    corrected_text: str | None = None
    results: list[AICorrectionResult] = field(default_factory=list)
    report_path: str | None = None
    error: str | None = None
    created_at: str = ""
    started_at: str | None = None
    finished_at: str | None = None
    cancelled: bool = False

    def __repr__(self) -> str:
        return f"AICorrectionJob(id={self.id!r}, model={self.model!r}, status={self.status!r})"


class AICorrectionJobManager:
    def __init__(self, client_factory=None, run_async: bool = True) -> None:
        self.client_factory = client_factory or _default_client_factory
        self.run_async = run_async
        self._jobs: dict[str, AICorrectionJob] = {}
        self._lock = threading.Lock()

    def create_job(
        self,
        config: dict,
        text: str,
        file_paths: list[str],
        user_lexicon: str,
    ) -> AICorrectionJob:
        model = str(config.get("selectedModel") or config.get("selected_model") or "").strip()
        job = AICorrectionJob(id=uuid.uuid4().hex, model=model, created_at=_now())
        with self._lock:
            self._jobs[job.id] = job
        if self.run_async:
            thread = threading.Thread(target=self._run_job, args=(job, config, text, file_paths, user_lexicon), daemon=True)
            thread.start()
        else:
            self._run_job(job, config, text, file_paths, user_lexicon)
        return job

    def get_job(self, job_id: str) -> AICorrectionJob | None:
        with self._lock:
            return self._jobs.get(job_id)

    def cancel_job(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.status not in {"queued", "running"}:
                return False
            job.cancelled = True
            return True

    def _run_job(
        self,
        job: AICorrectionJob,
        config: dict,
        text: str,
        file_paths: list[str],
        user_lexicon_text: str,
    ) -> None:
        job.status = "running"
        job.started_at = _now()
        final_status: AIJobStatus = "failed"
        started = time.monotonic()
        try:
            client = self.client_factory(config)
            user_lexicon = parse_user_lexicon(user_lexicon_text)
            if text.strip():
                job.corrected_text = correct_text_with_client(client, job.model, text, user_lexicon)
            for file_path in file_paths:
                if job.cancelled:
                    job.results.append(AICorrectionResult(source=file_path, status="skipped", error="Job cancelled"))
                    continue
                result = self._correct_file(client, job.model, Path(file_path).expanduser(), user_lexicon)
                job.results.append(result)
            if job.cancelled:
                final_status = "cancelled"
            elif any(result.status == "failed" for result in job.results):
                final_status = "failed" if not job.corrected_text and not any(r.status == "success" for r in job.results) else "completed"
            else:
                final_status = "completed"
        except Exception as exc:  # noqa: BLE001 - report task failures without leaking config.
            job.error = sanitize_error(str(exc), str(config.get("apiKey") or config.get("api_key") or ""))
            final_status = "failed"
        finally:
            job.finished_at = _now()
            if job.results:
                report_root = Path(job.results[0].target or job.results[0].source or ".").parent
                job.report_path = str(_write_ai_report(report_root, job, final_status, round(time.monotonic() - started, 3)))
            job.status = final_status

    def _correct_file(self, client, model: str, source: Path, user_lexicon: list[tuple[str, str]]) -> AICorrectionResult:
        started = time.monotonic()
        if not source.exists() or not source.is_file():
            return AICorrectionResult(source=str(source), status="failed", error="File not found", elapsed_seconds=0)
        if not is_supported_text_file(source):
            return AICorrectionResult(source=str(source), status="skipped", error="Unsupported text format", elapsed_seconds=0)
        target = unique_corrected_path(source)
        try:
            raw = source.read_text(encoding="utf-8")
            if source.suffix.lower() == ".srt":
                corrected = correct_srt_text(raw, lambda part: correct_text_with_client(client, model, part, user_lexicon))
            else:
                corrected = correct_text_with_client(client, model, raw, user_lexicon)
            target.write_text(corrected, encoding="utf-8")
            return AICorrectionResult(
                source=str(source),
                target=str(target),
                status="success",
                elapsed_seconds=round(time.monotonic() - started, 3),
            )
        except (OSError, OpenAICompatibleError, RuntimeError) as exc:
            return AICorrectionResult(
                source=str(source),
                target=str(target),
                status="failed",
                error=sanitize_error(str(exc), getattr(client, "api_key", "")),
                elapsed_seconds=round(time.monotonic() - started, 3),
            )


def ai_job_to_dict(job: AICorrectionJob) -> dict:
    return {
        "id": job.id,
        "model": job.model,
        "status": job.status,
        "correctedText": job.corrected_text,
        "results": [ai_result_to_dict(result) for result in job.results],
        "reportPath": job.report_path,
        "error": job.error,
        "createdAt": job.created_at,
        "startedAt": job.started_at,
        "finishedAt": job.finished_at,
    }


def ai_result_to_dict(result: AICorrectionResult) -> dict:
    return {
        "source": result.source,
        "target": result.target,
        "status": result.status,
        "error": result.error,
        "elapsedSeconds": result.elapsed_seconds,
    }


def _default_client_factory(config: dict) -> OpenAICompatibleClient:
    return OpenAICompatibleClient(
        str(config.get("baseUrl") or config.get("base_url") or ""),
        str(config.get("apiKey") or config.get("api_key") or ""),
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_ai_report(output_root: Path, job: AICorrectionJob, status: AIJobStatus, elapsed_seconds: float) -> Path:
    output_root.mkdir(parents=True, exist_ok=True)
    json_path = output_root / "ai-correction-report.json"
    csv_path = output_root / "ai-correction-report.csv"
    payload = {
        "id": job.id,
        "model": job.model,
        "status": status,
        "error": job.error,
        "elapsedSeconds": elapsed_seconds,
        "results": [ai_result_to_dict(result) for result in job.results],
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["source", "target", "status", "error", "elapsedSeconds"])
        writer.writeheader()
        for result in job.results:
            writer.writerow(ai_result_to_dict(result))
    return json_path


def sanitize_error(message: str, api_key: str) -> str:
    if api_key:
        return message.replace(api_key, "[redacted]")
    return message
