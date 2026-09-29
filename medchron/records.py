"""Reads a case file (a folder of PDFs) into numbered pages, and writes the combined, page-stamped PDF."""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

MIN_TEXT = 40  # characters; a page with less has no usable text layer (a scan) and needs OCR


@dataclass
class Page:
    n: int  # page number in the combined PDF, which is what the chronology cites
    file: str
    file_page: int
    text: str

    @property
    def needs_ocr(self) -> bool:
        return len(self.text.strip()) < MIN_TEXT

    @property
    def source(self) -> str:
        return f"{self.file} p.{self.file_page}"


@dataclass
class CaseFile:
    pages: list[Page] = field(default_factory=list)
    pdfs: list[Path] = field(default_factory=list)  # in combined order (OCR'd copies when --ocr is used)
    files_read: list[str] = field(default_factory=list)
    files_skipped: list[str] = field(default_factory=list)

    def page(self, n: int) -> Page:
        return self.pages[n - 1]


def natural_key(name: str) -> list:
    """Sort "2_er.pdf" before "10_pt.pdf"."""
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", name)]


def ocr_pdf(src: Path, dest_dir: Path, run=subprocess.run) -> Path:
    """Add a text layer to scanned pages with ocrmypdf (runs locally, so records never leave the machine)."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / src.name
    if dest.exists() and dest.stat().st_mtime >= src.stat().st_mtime:
        return dest
    result = run(["ocrmypdf", "--skip-text", "--quiet", str(src), str(dest)], capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ocrmypdf failed on {src.name}: {(result.stderr or '').strip()[:200]}")
    return dest


def read_case(folder: str | Path, *, ocr: bool = False, ocr_dir: Path | None = None) -> CaseFile:
    from pypdf import PdfReader

    if ocr and not shutil.which("ocrmypdf"):
        raise RuntimeError("--ocr needs ocrmypdf. On a Mac: brew install ocrmypdf")
    case = CaseFile()
    files = sorted((p for p in Path(folder).rglob("*") if p.is_file() and not p.name.startswith((".", "~$"))),
                   key=lambda p: natural_key(str(p.relative_to(folder))))
    for path in files:
        if path.suffix.lower() != ".pdf":
            case.files_skipped.append(f"{path.name} (not a PDF)")
            continue
        try:
            src = ocr_pdf(path, ocr_dir or Path(folder) / ".ocr") if ocr else path
            reader = PdfReader(str(src))
            if reader.is_encrypted and not reader.decrypt(""):
                case.files_skipped.append(f"{path.name} (password-protected)")
                continue
            texts = []
            for page in reader.pages:
                try:
                    texts.append(page.extract_text() or "")
                except Exception:  # one unreadable page should not lose the file
                    texts.append("")
        except Exception as e:  # a corrupt file should not stop the run
            case.files_skipped.append(f"{path.name} ({type(e).__name__}: {str(e)[:100]})")
            continue
        for i, text in enumerate(texts, 1):
            case.pages.append(Page(n=len(case.pages) + 1, file=path.name, file_page=i, text=text))
        case.pdfs.append(src)
        blank = sum(1 for p in case.pages[-len(texts):] if p.needs_ocr) if texts else 0
        case.files_read.append(f"{path.name} ({len(texts)} page{'' if len(texts) == 1 else 's'}" + (f", {blank} without text" if blank else "") + ")")
    return case


def write_combined(case: CaseFile, out_path: Path, bookmarks: list[tuple[str, int]], stamp: str = "Page") -> None:
    """Merge the PDFs in order, stamp "Page N" on every page, and add bookmarks.

    `bookmarks` are (title, page number) pairs, one per chronology entry, under a "Chronology" bookmark;
    each source file also gets a bookmark at its first page.
    """
    from pypdf import PdfReader, PdfWriter

    from agentkit.pdfgen import stamp_pdf

    writer = PdfWriter()
    starts: list[tuple[str, int]] = []
    for pdf in case.pdfs:
        starts.append((pdf.name, len(writer.pages)))
        writer.append(str(pdf), import_outline=False)
    total = len(writer.pages)
    for i, page in enumerate(writer.pages, 1):
        try:
            box = tuple(float(v) for v in page.mediabox)
            page.merge_page(PdfReader(_bytes_io(stamp_pdf(box, f"{stamp} {i} of {total}"))).pages[0])
        except Exception:  # an odd page stays unstamped rather than stopping the delivery
            continue
    if bookmarks:
        parent = writer.add_outline_item("Chronology", bookmarks[0][1] - 1)
        for title, n in bookmarks:
            if 1 <= n <= total:
                writer.add_outline_item(title[:120], n - 1, parent=parent)
    files = writer.add_outline_item("Source files", 0)
    for name, start in starts:
        writer.add_outline_item(name, start, parent=files)
    with open(out_path, "wb") as f:
        writer.write(f)


def _bytes_io(data: bytes):
    import io

    return io.BytesIO(data)
