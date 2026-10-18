import csv
import json
from pathlib import Path

import harness.__main__ as cli
from harness.agents import AGENTS, EXAMPLES, from_freightq, from_medchron, score_secq

SECQ_TRUTH = json.loads((EXAMPLES / "secq" / "truth.json").read_text())

PERFECT = {
    "1.1": ("Yes", "Customer data is encrypted at rest with AES-256 using AWS KMS.", "security-overview.md § Encryption"),
    "1.2": ("TLS 1.2 or higher", "All data in transit is encrypted with TLS 1.2 or higher.", "security-overview.md § Encryption"),
    "1.3": ("35 days", "Backups are taken daily, encrypted, and kept for 35 days.", "data-retention.md § Backups"),
    "2.1": ("Yes", "MFA is required for all employees through Okta.", "security-overview.md § Access control"),
    "2.2": ("Yes", "All employees pass a background check.", "security-overview.md § People"),
    "3.1": ("Within 72 hours", "We notify affected customers within 72 hours.", "incident-response.md § Customer notification"),
    "3.2": ("Yes", "We run a 24/7 on-call rotation.", "incident-response.md § Coverage"),
    "4.1": ("Yes", "We hold a SOC 2 Type II report.", "security-overview.md § Compliance"),
    "4.2": ("", "Not covered by the documents.", ""),
    "4.3": ("", "Not covered by the documents.", ""),
}


def _rows(**changes) -> list[dict]:
    rows = []
    for qid, (answer, explanation, sources) in PERFECT.items():
        row = {"qid": qid, "answer": answer, "explanation": explanation, "sources": sources,
               "needs_review": "yes" if qid in ("4.2", "4.3") else ""}
        row.update(changes.get(qid.replace(".", "_"), {}))
        rows.append(row)
    return rows


def _write_secq(out: Path, rows: list[dict], summary: dict | None = None) -> None:
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "review.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    (out / "run_summary.json").write_text(json.dumps(summary or {"llm_requests": 2, "cost_usd": 0.01}))


def test_answer_key_covers_every_question_in_the_sample():
    from secq.sheet import open_workbook, read_questions

    _, questions = read_questions(open_workbook(EXAMPLES / "secq" / "sample_questionnaire.xlsx"), None, None)
    assert {q.qid for q in questions} == {t["qid"] for t in SECQ_TRUTH["questions"]}


def test_perfect_secq_draft_scores_full_marks():
    sc = score_secq(_rows(), SECQ_TRUTH)
    assert (sc.got, sc.total, sc.errors, sc.notes) == (18, 18, [], [])


def test_yes_on_an_uncovered_question_is_an_error():
    sc = score_secq(_rows(**{"4_2": {"answer": "Yes", "explanation": "We are ISO 27001 certified."}}), SECQ_TRUTH)
    assert sc.errors == ["4.2: answered Yes, but the documents don't cover it"]


def test_wrong_answer_is_an_error_only_when_not_flagged():
    wrong = {"answer": "30 days", "explanation": "Backups are kept for 30 days."}
    sc = score_secq(_rows(**{"1_3": wrong}), SECQ_TRUTH)
    assert sc.errors == ["1.3: wrong answer not flagged for review"]
    assert sc.checks[0] == ("answers right", 7, 8)
    sc = score_secq(_rows(**{"1_3": {**wrong, "needs_review": "yes"}}), SECQ_TRUTH)
    assert sc.errors == [] and sc.notes[0].endswith("(flagged)")


def test_no_is_wrong_even_when_the_facts_appear():
    sc = score_secq(_rows(**{"3_2": {"answer": "No", "explanation": "We don't run 24/7 coverage."}}), SECQ_TRUTH)
    assert sc.checks[0] == ("answers right", 7, 8)


def test_medchron_and_freightq_scores_map_to_checks():
    from freightq.sample import score as fq_score
    from medchron.sample import score as mc_score

    truth = json.loads((EXAMPLES / "medchron" / "truth.json").read_text())
    sc = from_medchron(mc_score({"entries": [], "gaps": [], "bills": [], "total_billed": 0.0}, truth))
    assert sc.got == 0 and sc.total > 0 and any("total billed" in e for e in sc.errors)
    truth = json.loads((EXAMPLES / "freightq" / "truth.json").read_text())
    sc = from_freightq(fq_score({"emails": []}, truth))
    assert sc.total > 0 and sc.errors == []


def test_run_scores_records_history_and_compares(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "OUT", tmp_path)
    monkeypatch.setattr(cli, "HISTORY", tmp_path / "history.jsonl")
    calls = []

    def fake_run(name, out, model):
        calls.append((name, out, model))
        _write_secq(out, _rows())
        return 0

    monkeypatch.setattr(cli, "run_agent", fake_run)
    assert cli.main(["run", "--agents", "secq", "--model", "some/model:free"]) == 0
    assert calls == [("secq", tmp_path / "some-model-free" / "secq", "some/model:free")]
    assert cli.main(["run", "--agents", "secq", "--model", "some/model:free"]) == 0
    out = capsys.readouterr().out
    assert "PASS" in out and "100% -> 100%" in out
    history = [json.loads(line) for line in (tmp_path / "history.jsonl").read_text().splitlines()]
    assert [h["verdict"] for h in history] == ["PASS", "PASS"] and history[0]["model"] == "some/model:free"


def test_stopped_run_is_incomplete_and_crash_is_reported(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "OUT", tmp_path)
    monkeypatch.setattr(cli, "HISTORY", tmp_path / "history.jsonl")

    def fake_run(name, out, model):
        if name == "secq":
            _write_secq(out, _rows(), {"stopped_early": "daily request limit reached"})
            return 0
        out.mkdir(parents=True, exist_ok=True)
        return 1

    monkeypatch.setattr(cli, "run_agent", fake_run)
    assert cli.main(["run", "--agents", "secq,medchron"]) == 1
    out = capsys.readouterr().out
    assert "INCOMPLETE" in out and "stopped early: daily request limit" in out
    assert "CRASHED" in out and "medchron" in out


def test_every_agent_runs_its_own_example():
    for name, agent in AGENTS.items():
        args = agent.args(Path("/tmp/out"))
        assert "--out" in args and args[args.index("--out") + 1] == "/tmp/out"
        assert all(Path(a).exists() for a in args if a.startswith(str(EXAMPLES))), name
    assert "--synthetic" in AGENTS["medchron"].args(Path("x"))
