from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil
import subprocess
import tempfile
import time


@dataclass(frozen=True)
class ConversionSpec:
    source_ext: str
    target_ext: str
    filter_name: str | None
    family: str


class ConversionError(RuntimeError):
    pass


WRITER_INPUTS = {".doc", ".dot", ".rtf", ".odt", ".ott", ".txt", ".html", ".htm", ".docx"}
CALC_INPUTS = {".xls", ".xlt", ".ods", ".ots", ".csv", ".xlsx"}
IMPRESS_INPUTS = {".ppt", ".pps", ".pot", ".odp", ".otp", ".pptx"}
MARKDOWN_INPUTS = {".md"}
SUBTITLE_INPUTS = {".srt"}

MODERNIZE_SPECS = {
    **{ext: ConversionSpec(ext, ".docx", "MS Word 2007 XML", "writer") for ext in WRITER_INPUTS - {".docx"}},
    **{ext: ConversionSpec(ext, ".xlsx", "Calc Office Open XML", "calc") for ext in CALC_INPUTS - {".xlsx"}},
    **{ext: ConversionSpec(ext, ".pptx", "Impress Office Open XML", "impress") for ext in IMPRESS_INPUTS - {".pptx"}},
}

PDF_FILTERS = {
    "writer": "writer_pdf_Export",
    "calc": "calc_pdf_Export",
    "impress": "impress_pdf_Export",
}

TARGET_FILTERS = {
    ("writer", "docx"): "MS Word 2007 XML",
    ("writer", "txt"): "Text",
    ("calc", "xlsx"): "Calc Office Open XML",
    ("impress", "pptx"): "Impress Office Open XML",
}


def _family_for_ext(ext: str) -> str | None:
    if ext in WRITER_INPUTS:
        return "writer"
    if ext in CALC_INPUTS:
        return "calc"
    if ext in IMPRESS_INPUTS:
        return "impress"
    return None


def source_family(path: Path) -> str | None:
    ext = path.suffix.lower()
    if ext in MARKDOWN_INPUTS:
        return "writer"
    if ext in SUBTITLE_INPUTS:
        return "subtitle"
    return _family_for_ext(ext)


def supported_target_formats(path: Path, correction_enabled: bool = False) -> list[str]:
    ext = path.suffix.lower()
    family = _family_for_ext(ext)
    if correction_enabled:
        if ext in SUBTITLE_INPUTS:
            return ["docx", "pdf", "txt", "md", "srt"]
        if family == "writer" or ext in MARKDOWN_INPUTS:
            return ["docx", "pdf", "txt", "md"]
        return []

    if family == "writer":
        return ["docx", "pdf", "txt"]
    if family == "calc":
        return ["xlsx", "pdf"]
    if family == "impress":
        return ["pptx", "pdf"]
    return []


def get_conversion_spec(path: Path, mode: str, target_format: str | None = None) -> ConversionSpec | None:
    ext = path.suffix.lower()
    if mode == "modernize":
        return MODERNIZE_SPECS.get(ext)
    if mode == "pdf":
        family = _family_for_ext(ext)
        if family is None:
            return None
        return ConversionSpec(ext, ".pdf", PDF_FILTERS[family], family)
    if mode == "target":
        target = (target_format or "").strip().lower().lstrip(".")
        if target == "auto":
            return MODERNIZE_SPECS.get(ext)
        if target not in supported_target_formats(path, correction_enabled=False):
            return None
        family = _family_for_ext(ext)
        if family is None:
            return None
        if target == "pdf":
            return ConversionSpec(ext, ".pdf", PDF_FILTERS[family], family)
        return ConversionSpec(ext, f".{target}", TARGET_FILTERS.get((family, target)), family)
    return None


def is_skippable(path: Path) -> bool:
    return any(part.startswith(".") for part in path.parts) or path.name.startswith("~$")


def unique_output_path(output_dir: Path, relative_source: Path, target_ext: str) -> Path:
    target_dir = output_dir / relative_source.parent
    stem = relative_source.stem
    candidate = target_dir / f"{stem}{target_ext}"
    index = 1
    while candidate.exists():
        candidate = target_dir / f"{stem} ({index}){target_ext}"
        index += 1
    return candidate


class LibreOfficeConverter:
    def __init__(self, soffice_path: str, timeout_seconds: int = 120) -> None:
        self.soffice_path = soffice_path
        self.timeout_seconds = timeout_seconds

    def build_command(self, source: Path, output_dir: Path, spec: ConversionSpec) -> list[str]:
        convert_to = spec.target_ext.lstrip(".")
        if spec.filter_name:
            convert_to = f"{convert_to}:{spec.filter_name}"
        return [
            self.soffice_path,
            "--headless",
            "--nologo",
            "--nofirststartwizard",
            "--nodefault",
            "--nolockcheck",
            "--convert-to",
            convert_to,
            "--outdir",
            str(output_dir),
            str(source),
        ]

    def convert_one(self, source: Path, output_path: Path, spec: ConversionSpec) -> Path:
        started = time.monotonic()
        with tempfile.TemporaryDirectory(prefix="docformat-") as tmp:
            tmp_dir = Path(tmp)
            command = self.build_command(source, tmp_dir, spec)
            try:
                completed = subprocess.run(
                    command,
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout_seconds,
                )
            except subprocess.TimeoutExpired as exc:
                raise ConversionError(f"conversion timed out after {self.timeout_seconds}s") from exc
            except OSError as exc:
                raise ConversionError(f"failed to start LibreOffice: {exc}") from exc

            if completed.returncode != 0:
                message = (completed.stderr or completed.stdout or "LibreOffice conversion failed").strip()
                raise ConversionError(message)

            expected = tmp_dir / f"{source.stem}{spec.target_ext}"
            if not expected.exists():
                candidates = list(tmp_dir.glob(f"*{spec.target_ext}"))
                if len(candidates) == 1:
                    expected = candidates[0]
                else:
                    raise ConversionError("LibreOffice finished but no converted output was produced")

            output_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(expected), str(output_path))
        _ = started
        return output_path
