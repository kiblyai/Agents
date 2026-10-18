"""The agents the harness knows: how to run each on its example and how to score the output against the answer key."""

from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"


@dataclass
class Score:
    """Checks as (label, got, out of), errors that must never happen, and notes worth reading."""

    checks: list[tuple[str, int, int]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def got(self) -> int:
        return sum(c[1] for c in self.checks)

    @property
    def total(self) -> int:
        return sum(c[2] for c in self.checks)

    @property
    def pct(self) -> float:
        return 100 * self.got / self.total if self.total else 0.0


@dataclass
class Agent:
    name: str
    args: Callable[[Path], list[str]]  # `python -m <name> run` arguments, given the output folder
    score: Callable[[Path], Score]  # reads the output folder
    output: str  # the file a finished run writes; missing means nothing to score


# --- secq ---------------------------------------------------------------------------------------------------------

def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.lower())


def _says_yes(answer: str) -> bool:
    return bool(re.match(r"\s*yes\b", answer, re.I))


def score_secq(rows: list[dict], truth: dict) -> Score:
    """rows: secq's review.csv. A covered question is right when it states every fact and doesn't say No."""
    by_qid = {r["qid"].strip(): r for r in rows}
    sc = Score()
    covered = [t for t in truth["questions"] if t["covered"]]
    uncovered = [t for t in truth["questions"] if not t["covered"]]
    right = cited = flagged = 0
    for t in covered:
        r = by_qid.get(t["qid"])
        if r is None:
            sc.notes.append(f"{t['qid']}: not in review.csv")
            continue
        text = _norm(f"{r['answer']} {r['explanation']}")
        ok = all(any(_norm(v) in text for v in group) for group in t["facts"])
        if t.get("yes") and not _says_yes(r["answer"]):
            ok = False
        if re.match(r"\s*no\b", r["answer"], re.I):
            ok = False
        right_source = any(s in r["sources"] for s in t["sources"])
        right += ok
        cited += right_source
        if not ok and not r["needs_review"]:
            sc.errors.append(f"{t['qid']}: wrong answer not flagged for review")
        if not ok or not right_source:
            said = f"\"{r['answer']}\" {r['explanation'][:100]}".strip() if r["answer"] or r["explanation"] else "no answer"
            note = f"{t['qid']}: {'wrong' if not ok else 'right'}: {said}"
            if not right_source:
                note += f"; cites {r['sources'] or 'nothing'} (expected {' or '.join(t['sources'])})"
            sc.notes.append(note + (" (flagged)" if r["needs_review"] else ""))
    for t in uncovered:
        r = by_qid.get(t["qid"])
        if r is None:
            sc.notes.append(f"{t['qid']}: not in review.csv")
            continue
        flagged += bool(r["needs_review"])
        if _says_yes(r["answer"]):
            sc.errors.append(f"{t['qid']}: answered Yes, but the documents don't cover it")
    sc.checks = [("answers right", right, len(covered)), ("right source cited", cited, len(covered)),
                 ("uncovered questions flagged", flagged, len(uncovered))]
    return sc


def _secq(out: Path) -> Score:
    with open(out / "review.csv", newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    return score_secq(rows, json.loads((EXAMPLES / "secq" / "truth.json").read_text()))


# --- medchron -----------------------------------------------------------------------------------------------------

def from_medchron(s: dict) -> Score:
    """medchron.sample.score() output as harness checks."""
    sc = Score(checks=[
        ("encounters found", s["found"], s["encounters"]),
        ("right page cited", s["right_page"], s["found"]),
        ("diagnosis codes found", s["codes_found"], s["codes"]),
        ("before-injury marking", s["pre_incident_ok"], s["found"]),
        ("duplicates merged", s["duplicates_merged"], s["duplicates"]),
        ("treatment gaps found", s["gaps_found"], s["gaps"]),
        ("bill lines found", s["bill_lines_found"], s["bill_lines"]),
    ])
    if s["wrong_codes"]:
        sc.errors.append(f"{s['wrong_codes']} diagnosis codes not in the records")
    if s["bill_lines_extra"]:
        sc.errors.append(f"{s['bill_lines_extra']} bill lines not in the records")
    if abs(s["total_billed"] - s["truth_total_billed"]) >= 0.01:
        sc.errors.append(f"total billed ${s['total_billed']:,.2f}, answer key ${s['truth_total_billed']:,.2f}")
    sc.notes += [f"missed {t['date']} {t['visit_type']} ({t['provider']})" for t in s["missed"]]
    sc.notes += [f"extra gap {a} to {b}" for a, b in s["extra_gaps"]]
    if s["extra"]:
        sc.notes.append(f"{len(s['extra'])} entries not in the answer key")
    return sc


def _medchron(out: Path) -> Score:
    from medchron.sample import score

    truth = json.loads((EXAMPLES / "medchron" / "truth.json").read_text())
    return from_medchron(score(json.loads((out / "chronology.json").read_text()), truth))


def _medchron_args(out: Path) -> list[str]:
    truth = json.loads((EXAMPLES / "medchron" / "truth.json").read_text())
    return ["--records", str(EXAMPLES / "medchron" / "records"), "--patient", truth["patient"], "--doi", truth["doi"],
            "--synthetic", "--out", str(out)]


# --- freightq -----------------------------------------------------------------------------------------------------

def from_freightq(s: dict) -> Score:
    """freightq.sample.score() output as harness checks."""
    fields_right = sum(a for a, _ in s["fields"].values())
    fields_total = sum(b for _, b in s["fields"].values())
    sc = Score(checks=[
        ("emails read as the right kind", s["kind_right"], s["emails"]),
        ("lanes found", s["lanes_found"], s["lanes"]),
        ("fields right", fields_right, fields_total),
        ("missing details right", s["missing_right"], s["lanes_found"]),
        ("rates match", s["rate_right"], s["priced_expected"]),
        ("left for pricing by hand", s["unpriced_right"], s["unpriced_expected"]),
        ("expected flags raised", s["flags_found"], s["flags_expected"]),
    ])
    sc.errors += [f"wrong rate: {w}" for w in s["wrong_rates"] if "not priced" not in w]
    if s["extra_lanes"]:
        sc.errors.append(f"{s['extra_lanes']} lanes not in the emails")
    sc.notes += s["wrong_kind"] + [f"missed {m}" for m in s["missed"]]
    sc.notes += [f"{f} wrong on {b - a} of {b} lanes" for f, (a, b) in s["fields"].items() if a < b]
    sc.notes += [f"missed flag {m}" for m in s["missed_flags"]]
    return sc


def _freightq(out: Path) -> Score:
    from freightq.sample import score

    truth = json.loads((EXAMPLES / "freightq" / "truth.json").read_text())
    return from_freightq(score(json.loads((out / "results.json").read_text()), truth))


def _freightq_args(out: Path) -> list[str]:
    ex = EXAMPLES / "freightq"
    return ["--emails", str(ex / "inbox"), "--lanes", str(ex / "lanes.csv"), "--broker", str(ex / "broker.toml"),
            "--out", str(out)]


AGENTS = {a.name: a for a in [
    Agent("secq", lambda out: ["--kb", str(EXAMPLES / "secq" / "kb"), "--questionnaire",
                               str(EXAMPLES / "secq" / "sample_questionnaire.xlsx"), "--company",
                               "Northwind Analytics", "--out", str(out)], _secq, "review.csv"),
    Agent("medchron", _medchron_args, _medchron, "chronology.json"),
    Agent("freightq", _freightq_args, _freightq, "results.json"),
]}
