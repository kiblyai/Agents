"""Transaction folders in, checked deadlines out: one cached model request per transaction."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Callable

from agentkit.docs import document_files, read_document
from agentkit.runner import Runner

from .check import Transaction, check_contract
from .config import TCConfig
from .extract import SYSTEM, ContractReading, build_prompt

# Bump when the prompt, schema or checks change, so cached replies from older logic are redone.
CACHE_VERSION = "1"


def transaction_folders(root: str | Path) -> list[Path]:
    """Each subfolder is one transaction; a folder holding documents directly is a single transaction."""
    root = Path(root)
    subs = sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith("."))
    return subs or [root]


def read_folder(folder: Path) -> tuple[list[tuple[str, str]], list[str]]:
    """(document name, text) in name order (contract first, then addenda), and the files skipped."""
    files, skipped = document_files(folder)
    files.sort(key=lambda p: (not p.stem.lower().startswith(("contract", "purchase", "agreement")), p.name))
    docs = []
    for p in files:
        try:
            text = "\n".join(body for _, body in read_document(p)).strip()
        except Exception as e:  # a broken file must not stop the others
            skipped.append(f"{p.name} ({type(e).__name__})")
            continue
        if text:
            docs.append((p.name, text))
        else:
            skipped.append(f"{p.name} (no readable text; scanned?)")
    return docs, skipped


async def read_all(folders: list[Path], cfg: TCConfig, runner: Runner, today: date,
                   progress: Callable[[str], None] = print) -> list[Transaction]:
    out = []
    for i, folder in enumerate(folders, 1):
        tag = f"[{i}/{len(folders)}] {folder.name}"
        docs, skipped = read_folder(folder)
        if not docs:
            out.append(Transaction(folder=folder.name, error="no readable documents" +
                                   (f" ({'; '.join(skipped)})" if skipped else "")))
            progress(f"{tag}: no readable documents")
            continue
        got, err = await runner.ask(SYSTEM, build_prompt(folder.name, docs), ContractReading, max_tokens=5000)
        if got is None:
            out.append(Transaction(folder=folder.name, documents=[n for n, _ in docs], error=err))
            progress(f"{tag}: {err}")
            continue
        t = check_contract(got, folder.name, docs, cfg, today)
        out.append(t)
        holds = sum(1 for f in t.all_flags() if f.hold)
        dated = sum(1 for d in t.deadlines if d.due)
        progress(f"{tag}: {dated} dated deadlines, closing {t.closing or '?'}, "
                 + ("ready" if t.ready else f"{holds} to fix"))
    return out
