from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from shutil import which


@dataclass(frozen=True)
class LibreOfficeInfo:
    found: bool
    path: str | None
    install_hint: str


INSTALL_HINT = (
    "LibreOffice is required for conversions. Install it from "
    "https://www.libreoffice.org/download/download-libreoffice/ or run "
    "`brew install --cask libreoffice` on macOS."
)


def discover_soffice() -> LibreOfficeInfo:
    candidates = [
        Path("/Applications/LibreOffice.app/Contents/MacOS/soffice"),
        Path("/opt/homebrew/bin/soffice"),
        Path("/usr/local/bin/soffice"),
        Path("/opt/homebrew/bin/libreoffice"),
        Path("/usr/local/bin/libreoffice"),
    ]
    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            return LibreOfficeInfo(True, str(candidate), INSTALL_HINT)

    for binary in ("soffice", "libreoffice"):
        found = which(binary)
        if found:
            return LibreOfficeInfo(True, found, INSTALL_HINT)

    return LibreOfficeInfo(False, None, INSTALL_HINT)


def default_output_dir(source: Path) -> Path:
    if source.suffix:
        return source.parent / "converted"
    return source / "converted"
