"""Reads emails (.eml files, or .txt with From/Subject/Date lines) with their attachments, and splits off quoted history."""

from __future__ import annotations

import html
import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from email import policy
from email.parser import BytesParser
from email.utils import parseaddr, parsedate_to_datetime
from pathlib import Path

EMAIL_TYPES = {".eml", ".txt"}
MAX_PART = 12000  # characters of any one message part or attachment sent to the model

# Where quoted history starts in a reply or forward
QUOTE_START = re.compile(
    r"^On\b[^\n]{0,200}(?:\n[^\n]{0,200})?\bwrote:[ \t]*$"
    r"|^-{2,}\s*(?:Original|Forwarded) Message\s*-{2,}"
    r"|^From:[^\n]+\n(?:(?:Sent|Date|To):[^\n]*\n)+"
    r"|^>", re.I | re.M)


@dataclass
class Attachment:
    name: str
    text: str


@dataclass
class Email:
    file: str
    sender: str = ""  # address
    sender_name: str = ""
    to: str = ""
    subject: str = ""
    sent: datetime | None = None
    message_id: str = ""
    latest: str = ""  # the newest message, without quoted history
    earlier: str = ""  # quoted replies and forwarded text below it
    attachments: list[Attachment] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)  # attachments that could not be read

    @property
    def day(self) -> date | None:
        return self.sent.date() if self.sent else None

    def all_text(self) -> str:
        """Everything the model is shown; the checks look for its values here."""
        parts = [self.subject, self.latest, self.earlier, *(a.text for a in self.attachments)]
        return "\n".join(p for p in parts if p)


def split_thread(body: str) -> tuple[str, str]:
    """The newest message, and the quoted or forwarded history below it."""
    body = body.replace("\r\n", "\n").strip()
    m = QUOTE_START.search(body)
    if not m:
        return body, ""
    return body[:m.start()].strip(), body[m.start():].strip()


def html_to_text(s: str) -> str:
    s = re.sub(r"(?is)<(script|style).*?</\1>", " ", s)
    s = re.sub(r"(?i)<br\s*/?>|</(p|div|tr|li|h\d)>", "\n", s)
    s = re.sub(r"(?i)</t[dh]>", " | ", s)
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s)
    return "\n".join(re.sub(r"[ \t\xa0]+", " ", line).strip() for line in s.splitlines())


def pdf_text(data: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    return "\n".join((p.extract_text() or "") for p in reader.pages)


def _date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        pass
    try:
        return datetime.fromisoformat(value.strip())
    except ValueError:
        return None


def _attachment(name: str, ctype: str, data: bytes) -> Attachment | None:
    low = name.lower()
    if ctype == "application/pdf" or low.endswith(".pdf"):
        text = pdf_text(data)
    elif ctype.startswith("text/") or low.endswith((".txt", ".csv")):
        text = data.decode("utf-8", "replace")
        if ctype == "text/html" or low.endswith((".htm", ".html")):
            text = html_to_text(text)
    else:
        return None
    text = text.strip()
    return Attachment(name, text) if text else None


def read_eml(path: Path) -> Email:
    with path.open("rb") as f:
        msg = BytesParser(policy=policy.default).parse(f)
    name, addr = parseaddr(str(msg.get("From", "")))
    e = Email(file=path.name, sender=addr, sender_name=name, to=str(msg.get("To", "")),
              subject=str(msg.get("Subject", "")), sent=_date(msg.get("Date")), message_id=str(msg.get("Message-ID", "")))
    body = msg.get_body(preferencelist=("plain", "html"))
    text = ""
    if body is not None:
        text = body.get_content()
        if body.get_content_type() == "text/html":
            text = html_to_text(text)
    e.latest, e.earlier = split_thread(text)
    for part in msg.iter_attachments():
        fname = part.get_filename() or "attachment"
        try:
            att = _attachment(fname, part.get_content_type(), part.get_payload(decode=True) or b"")
        except Exception:  # a broken attachment must not sink the email
            att = None
        if att:
            e.attachments.append(att)
        else:
            e.skipped.append(fname)
    return e


HEADER_LINE = re.compile(r"^(From|To|Subject|Date|Sent):\s*(.*)$", re.I)


def read_txt(path: Path) -> Email:
    """A pasted email: optional From/To/Subject/Date lines, a blank line, then the body."""
    lines = path.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n").split("\n")
    headers: dict[str, str] = {}
    i = 0
    while i < len(lines) and (m := HEADER_LINE.match(lines[i])):
        headers[m.group(1).lower()] = m.group(2).strip()
        i += 1
    name, addr = parseaddr(headers.get("from", ""))
    e = Email(file=path.name, sender=addr, sender_name=name, to=headers.get("to", ""),
              subject=headers.get("subject", ""), sent=_date(headers.get("date") or headers.get("sent")))
    e.latest, e.earlier = split_thread("\n".join(lines[i:]))
    return e


def read_inbox(folder: str | Path) -> tuple[list[Email], list[str]]:
    """Every .eml and .txt email in a folder, in file-name order, and the files that were skipped."""
    emails, skipped = [], []
    for path in sorted(Path(folder).iterdir()):
        if path.name.startswith(".") or not path.is_file():
            continue
        if path.suffix.lower() not in EMAIL_TYPES:
            skipped.append(f"{path.name} (not .eml or .txt)")
            continue
        try:
            emails.append(read_eml(path) if path.suffix.lower() == ".eml" else read_txt(path))
        except Exception as err:
            skipped.append(f"{path.name} ({type(err).__name__}: {str(err)[:80]})")
    return emails, skipped
