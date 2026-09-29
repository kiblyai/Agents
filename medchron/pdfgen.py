"""A tiny PDF writer (no extra dependencies): text pages for the synthetic records, and page-number stamps."""

from __future__ import annotations

import textwrap

LETTER = (612.0, 792.0)


def _esc(text: str) -> str:
    text = text.encode("latin-1", "replace").decode("latin-1")
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _pdf(page_streams: list[bytes], size: tuple[float, float]) -> bytes:
    """Assemble a PDF whose pages use Helvetica (/F1) and Helvetica-Bold (/F2)."""
    w, h = size
    n = len(page_streams)
    font1, font2 = 3 + 2 * n, 4 + 2 * n
    objs: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [" + b" ".join(b"%d 0 R" % (3 + 2 * i) for i in range(n)) + b"] /Count %d >>" % n,
    ]
    for i, stream in enumerate(page_streams):
        objs.append(b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %.0f %.0f] /Contents %d 0 R "
                    b"/Resources << /Font << /F1 %d 0 R /F2 %d 0 R >> >> >>" % (w, h, 4 + 2 * i, font1, font2))
        objs.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
    for name in (b"Helvetica", b"Helvetica-Bold"):
        objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /" + name + b" /Encoding /WinAnsiEncoding >>")
    out, offsets = b"%PDF-1.4\n", []
    for i, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    return out + b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)


FONT_SIZE, LEADING, MARGIN, WRAP = 10, 13, 54, 95
LINES_PER_PAGE = int((LETTER[1] - 2 * MARGIN) // LEADING)


def wrap_page(lines: list[str], footer: str = "") -> list[list[str]]:
    """Wrap long lines and split one logical page into as many printed pages as it needs.

    A line starting with "## " is a bold heading, "**" a bold line. Every printed page repeats the footer.
    """
    wrapped: list[str] = []
    for line in lines:
        bold = "**" if line.startswith("**") else "## " if line.startswith("## ") else ""
        body = line[len(bold):]
        parts = textwrap.wrap(body, WRAP, subsequent_indent="  ") or [""]
        wrapped += [bold + p for p in parts]
    room = LINES_PER_PAGE - (2 if footer else 0)
    pages = [wrapped[i:i + room] for i in range(0, len(wrapped), room)] or [[]]
    return [p + ["", footer] if footer else p for p in pages]


def text_pdf(pages: list[list[str]], size: tuple[float, float] = LETTER) -> bytes:
    """One printed page per list of lines (use wrap_page first for long pages)."""
    streams = []
    for lines in pages:
        ops = [b"BT", b"%d TL" % LEADING, b"%.0f %.0f Td" % (MARGIN, size[1] - MARGIN)]
        for line in lines:
            font, size_pt, text = b"F1", FONT_SIZE, line
            if line.startswith("## "):
                font, size_pt, text = b"F2", 12, line[3:]
            elif line.startswith("**"):
                font, text = b"F2", line[2:]
            ops.append(b"/%s %d Tf (%s) Tj T*" % (font, size_pt, _esc(text).encode("latin-1")))
        ops.append(b"ET")
        streams.append(b"\n".join(ops))
    return _pdf(streams, size)


def stamp_pdf(box: tuple[float, float, float, float], text: str, font_size: int = 8) -> bytes:
    """A one-page PDF with `text` in the bottom-right corner of the page box (llx, lly, urx, ury)."""
    llx, lly, urx, ury = box
    width = len(text) * font_size * 0.55
    x, y = urx - 24 - width, lly + 12
    stream = b"BT /F2 %d Tf %.1f %.1f Td (%s) Tj ET" % (font_size, x, y, _esc(text).encode("latin-1"))
    return _pdf([stream], (urx, ury))
