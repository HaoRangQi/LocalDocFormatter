from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal
import csv
import json
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone

from .config import default_output_dir
from .converter import (
    ConversionError,
    LibreOfficeConverter,
    get_conversion_spec,
    is_skippable,
    source_family,
    supported_target_formats,
    unique_output_path,
)
from .text_correction import (
    correct_srt_text,
    correct_text_with_client,
    load_lexicon_file,
    normalize_lexicon_entries,
    parse_user_lexicon,
)


JobStatus = Literal["queued", "running", "completed", "cancelled", "failed"]
FileStatus = Literal["pending", "success", "failed", "skipped"]


@dataclass
class FileResult:
    source: str
    target: str | None = None
    status: FileStatus = "pending"
    error: str | None = None
    elapsed_seconds: float | None = None
    detected_family: str | None = None
    target_format: str | None = None
    ai_correction: bool = False


@dataclass
class Job:
    id: str
    sources: list[str]
    output_dir: str | None
    mode: str
    recursive: bool
    target_format: str | None = None
    correction_enabled: bool = False
    correction_model: str | None = None
    status: JobStatus = "queued"
    results: list[FileResult] = field(default_factory=list)
    error: str | None = None
    created_at: str = ""
    started_at: str | None = None
    finished_at: str | None = None
    cancelled: bool = False


class JobManager:
    def __init__(self, soffice_path: str | None = None, run_async: bool = True, correction_client_factory=None) -> None:
        self.soffice_path = soffice_path
        self.run_async = run_async
        self.correction_client_factory = correction_client_factory
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def create_job(
        self,
        sources: list[str],
        output_dir: str | None,
        mode: str,
        recursive: bool = True,
        target_format: str | None = None,
        correction_config: dict | None = None,
        correction_user_lexicon: str = "",
        correction_prompt: str | None = None,
        correction_entries: list[dict] | None = None,
        correction_lexicon_files: list[str] | None = None,
        file_options: list[dict] | None = None,
    ) -> Job:
        job = Job(
            id=uuid.uuid4().hex,
            sources=sources,
            output_dir=output_dir,
            mode=mode,
            recursive=recursive,
            target_format=target_format,
            correction_enabled=bool(correction_config),
            correction_model=str((correction_config or {}).get("selectedModel") or (correction_config or {}).get("selected_model") or ""),
            created_at=_now(),
        )
        with self._lock:
            self._jobs[job.id] = job
        if self.run_async:
            thread = threading.Thread(
                target=self._run_job,
                args=(
                    job,
                    correction_config,
                    correction_user_lexicon,
                    correction_prompt,
                    correction_entries,
                    correction_lexicon_files,
                    file_options,
                ),
                daemon=True,
            )
            thread.start()
        else:
            self._run_job(
                job,
                correction_config,
                correction_user_lexicon,
                correction_prompt,
                correction_entries,
                correction_lexicon_files,
                file_options,
            )
        return job

    def get_job(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def cancel_job(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.status not in {"queued", "running"}:
                return False
            job.cancelled = True
            return True

    def collect_files(self, sources: list[str], recursive: bool) -> list[Path]:
        files: list[Path] = []
        for source_text in sources:
            source = Path(source_text).expanduser()
            if source.is_file():
                files.append(source)
            elif source.is_dir():
                iterator = source.rglob("*") if recursive else source.glob("*")
                files.extend(path for path in iterator if path.is_file())
        return sorted(files, key=lambda path: str(path).lower())

    def scan_files(self, sources: list[str], recursive: bool, correction_enabled: bool = False) -> list[dict]:
        base_roots = [_source_base(Path(source).expanduser()) for source in sources]
        rows = []
        for path in self.collect_files(sources, recursive):
            relative = _relative_to_nearest_base(path, base_roots)
            if is_skippable(relative):
                continue
            targets = supported_target_formats(path, correction_enabled=correction_enabled)
            default_target = _default_target_for_scan(targets)
            try:
                size = path.stat().st_size
            except OSError:
                size = 0
            rows.append(
                {
                    "source": str(path),
                    "name": path.name,
                    "relativePath": str(relative),
                    "sizeBytes": size,
                    "detectedFamily": source_family(path),
                    "supportedTargets": targets,
                    "defaultTargetFormat": default_target,
                }
            )
        return rows

    def _run_job(
        self,
        job: Job,
        correction_config: dict | None = None,
        correction_user_lexicon: str = "",
        correction_prompt: str | None = None,
        correction_entries: list[dict] | None = None,
        correction_lexicon_files: list[str] | None = None,
        file_options: list[dict] | None = None,
    ) -> None:
        job.status = "running"
        job.started_at = _now()
        final_status: JobStatus = "failed"
        output_root = _resolve_output_root(job.sources, job.output_dir)
        converter = LibreOfficeConverter(self.soffice_path) if self.soffice_path else None
        files = self.collect_files(job.sources, job.recursive)
        base_roots = [_source_base(Path(source).expanduser()) for source in job.sources]
        correction_client = self.correction_client_factory(correction_config) if correction_config and self.correction_client_factory else None
        user_lexicon = _collect_user_lexicon(correction_user_lexicon, correction_entries, correction_lexicon_files)
        file_option_map = _file_option_map(file_options)
        if file_option_map:
            files = [path for path in files if str(path) in file_option_map]

        try:
            for source in files:
                if job.cancelled:
                    job.results.append(FileResult(source=str(source), status="skipped", error="Job cancelled"))
                    continue

                relative = _relative_to_nearest_base(source, base_roots)
                if is_skippable(relative):
                    job.results.append(FileResult(source=str(source), status="skipped", error="Hidden or temporary file"))
                    continue

                if job.correction_enabled:
                    allowed_targets = supported_target_formats(source, correction_enabled=True)
                    target_format = _target_for_source(source, file_option_map, job.target_format or "docx")
                    if target_format not in allowed_targets:
                        job.results.append(
                            FileResult(
                                source=str(source),
                                status="skipped",
                                error="Unsupported target format",
                                detected_family=source_family(source),
                                target_format=target_format,
                                ai_correction=True,
                            )
                        )
                        continue
                    if correction_client is None:
                        job.results.append(
                            FileResult(
                                source=str(source),
                                status="failed",
                                error="AI correction client is not configured",
                                detected_family=source_family(source),
                                target_format=target_format,
                                ai_correction=True,
                            )
                        )
                        continue
                    result = FileResult(
                        source=str(source),
                        target=str(unique_output_path(output_root, relative, f".{target_format}")),
                        status="pending",
                        detected_family=source_family(source),
                        target_format=target_format,
                        ai_correction=True,
                    )
                    job.results.append(result)
                    started = time.monotonic()
                    try:
                        _convert_with_correction(
                            source,
                            Path(result.target),
                            converter,
                            correction_client,
                            target_format,
                            job,
                            user_lexicon,
                            correction_prompt,
                        )
                        result.status = "success"
                    except ConversionError as exc:
                        result.status = "failed"
                        result.error = str(exc)
                    finally:
                        result.elapsed_seconds = round(time.monotonic() - started, 3)
                    continue

                per_file_target = _target_for_source(source, file_option_map, job.target_format)
                spec = get_conversion_spec(source, job.mode, per_file_target)
                if spec is None:
                    error = "Unsupported target format" if job.mode == "target" else "Unsupported format"
                    job.results.append(
                        FileResult(
                            source=str(source),
                            status="skipped",
                            error=error,
                            detected_family=source_family(source),
                            target_format=(per_file_target or spec.target_ext.lstrip(".") if spec else per_file_target),
                        )
                    )
                    continue

                target = unique_output_path(output_root, relative, spec.target_ext)
                started = time.monotonic()
                result = FileResult(
                    source=str(source),
                    target=str(target),
                    status="pending",
                    detected_family=source_family(source),
                    target_format=spec.target_ext.lstrip("."),
                )
                job.results.append(result)
                if converter is None:
                    result.status = "failed"
                    result.error = "LibreOffice was not found"
                    result.elapsed_seconds = round(time.monotonic() - started, 3)
                    continue

                try:
                    converter.convert_one(source, target, spec)
                    result.status = "success"
                except ConversionError as exc:
                    result.status = "failed"
                    result.error = str(exc)
                finally:
                    result.elapsed_seconds = round(time.monotonic() - started, 3)

            if job.cancelled:
                final_status = "cancelled"
            elif any(result.status == "failed" for result in job.results):
                final_status = "failed" if not any(result.status == "success" for result in job.results) else "completed"
            else:
                final_status = "completed"
        except Exception as exc:  # noqa: BLE001 - keep batch tool alive and report.
            final_status = "failed"
            job.error = str(exc)
        finally:
            job.finished_at = _now()
            _write_reports(output_root, job, final_status)
            job.status = final_status


def job_to_dict(job: Job, status_override: JobStatus | None = None) -> dict:
    return {
        "id": job.id,
        "sources": job.sources,
        "outputDir": job.output_dir,
        "mode": job.mode,
        "recursive": job.recursive,
        "targetFormat": job.target_format,
        "correctionEnabled": job.correction_enabled,
        "correctionModel": job.correction_model,
        "status": status_override or job.status,
        "error": job.error,
        "createdAt": job.created_at,
        "startedAt": job.started_at,
        "finishedAt": job.finished_at,
        "results": [result_to_dict(result) for result in job.results],
    }


def result_to_dict(result: FileResult) -> dict:
    return {
        "source": result.source,
        "target": result.target,
        "status": result.status,
        "error": result.error,
        "elapsedSeconds": result.elapsed_seconds,
        "detectedFamily": result.detected_family,
        "targetFormat": result.target_format,
        "aiCorrection": result.ai_correction,
    }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _resolve_output_root(sources: list[str], output_dir: str | None) -> Path:
    if output_dir:
        return Path(output_dir).expanduser()
    if sources:
        return default_output_dir(Path(sources[0]).expanduser())
    return Path.cwd() / "converted"


def _source_base(source: Path) -> Path:
    if source.is_dir():
        return source
    return source.parent


def _relative_to_nearest_base(path: Path, bases: list[Path]) -> Path:
    for base in sorted(bases, key=lambda item: len(str(item)), reverse=True):
        try:
            return path.relative_to(base)
        except ValueError:
            continue
    return Path(path.name)


def _write_reports(output_root: Path, job: Job, status: JobStatus) -> None:
    output_root.mkdir(parents=True, exist_ok=True)
    json_path = output_root / "conversion-report.json"
    csv_path = output_root / "conversion-report.csv"
    json_path.write_text(json.dumps(job_to_dict(job, status), ensure_ascii=False, indent=2), encoding="utf-8")

    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "source",
                "target",
                "status",
                "error",
                "elapsedSeconds",
                "detectedFamily",
                "targetFormat",
                "aiCorrection",
            ],
        )
        writer.writeheader()
        for result in job.results:
            writer.writerow(result_to_dict(result))


def _convert_with_correction(
    source: Path,
    output_path: Path,
    converter: LibreOfficeConverter | None,
    correction_client,
    target_format: str | None,
    job: Job,
    user_lexicon: list[tuple[str, str]],
    correction_prompt: str | None,
) -> None:
    target = (target_format or "docx").strip().lower().lstrip(".")
    if target == "srt":
        text = _extract_text_for_correction(source, converter)
        corrected = correct_srt_text(
            text,
            lambda part: correct_text_with_client(
                correction_client,
                _correction_model(job),
                part,
                user_lexicon,
                system_prompt=correction_prompt,
            ),
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(corrected, encoding="utf-8")
        return

    text = _extract_text_for_correction(source, converter)
    corrected = correct_text_with_client(correction_client, _correction_model(job), text, user_lexicon, system_prompt=correction_prompt)
    if target in {"txt", "md"}:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(corrected, encoding="utf-8")
        return
    if converter is None:
        raise ConversionError("LibreOffice was not found")
    with tempfile.TemporaryDirectory(prefix="docformat-corrected-") as tmp:
        corrected_source = Path(tmp) / f"{source.stem}.txt"
        corrected_source.write_text(corrected, encoding="utf-8")
        family = source_family(corrected_source)
        spec = get_conversion_spec(corrected_source, "target", target)
        if spec is None or family != "writer":
            raise ConversionError("Unsupported target format")
        converter.convert_one(corrected_source, output_path, spec)


def _extract_text_for_correction(source: Path, converter: LibreOfficeConverter | None) -> str:
    ext = source.suffix.lower()
    if ext in {".txt", ".md", ".srt"}:
        return source.read_text(encoding="utf-8")
    if converter is None:
        raise ConversionError("LibreOffice was not found")
    with tempfile.TemporaryDirectory(prefix="docformat-extract-") as tmp:
        tmp_dir = Path(tmp)
        spec = get_conversion_spec(source, "target", "txt")
        if spec is None:
            raise ConversionError("Unsupported format for AI correction")
        extracted = converter.convert_one(source, tmp_dir / f"{source.stem}.txt", spec)
        return extracted.read_text(encoding="utf-8", errors="replace")


def _correction_model(job: Job) -> str:
    return job.correction_model or "default"


def _collect_user_lexicon(
    text_lexicon: str,
    entries: list[dict] | None,
    file_paths: list[str] | None,
) -> list[tuple[str, str]]:
    pairs = parse_user_lexicon(text_lexicon)
    pairs.extend(normalize_lexicon_entries(entries))
    for file_path in file_paths or []:
        path = Path(str(file_path)).expanduser()
        if path.exists() and path.is_file():
            pairs.extend(load_lexicon_file(path))
    return pairs


def _default_target_for_scan(targets: list[str]) -> str | None:
    if "pdf" in targets:
        return "pdf"
    return targets[0] if targets else None


def _file_option_map(file_options: list[dict] | None) -> dict[str, str]:
    mapped: dict[str, str] = {}
    for option in file_options or []:
        if not isinstance(option, dict):
            continue
        source = str(option.get("source") or "").strip()
        target = str(option.get("targetFormat") or "").strip().lower().lstrip(".")
        if source and target:
            mapped[str(Path(source).expanduser())] = target
    return mapped


def _target_for_source(source: Path, file_option_map: dict[str, str], fallback: str | None) -> str | None:
    return file_option_map.get(str(source), fallback)
