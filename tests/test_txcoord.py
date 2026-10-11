import asyncio
import csv
import json
import re
from datetime import date
from pathlib import Path

import pytest

from agentkit.llm import LLM
from agentkit.runner import Runner
from txcoord.check import check_contract
from txcoord.config import TCConfig, load_tc
from txcoord.dates import Calendar, compute, federal_holidays, parse_term, time_rules, words_to_number
from txcoord.extract import ContractReading
from txcoord.output import ics, write_all
from txcoord.pipeline import CACHE_VERSION, read_all, read_folder, transaction_folders
from txcoord.sample import make_sample, score

from .fakes import FakeClient, no_sleep, user_prompt

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "txcoord"
TODAY = date(2026, 10, 12)


@pytest.fixture(scope="module")
def case(tmp_path_factory):
    out = tmp_path_factory.mktemp("txcoord")
    truth = make_sample(out)
    return out, truth, load_tc(out / "tc.toml")


def oracle(truth: dict):
    by = {t["folder"]: t["reading"] for t in truth["transactions"]}

    def respond(kwargs):
        return json.dumps(by[re.search(r"^Transaction folder: (.+)$", user_prompt(kwargs), re.M).group(1)])
    return respond


def _run(case, tmp_path, responder=None):
    out, truth, cfg = case
    runner = Runner(LLM(FakeClient(responder or oracle(truth)), "test-model", rpm=0, sleep=no_sleep),
                    tmp_path / "cache", CACHE_VERSION)
    ts = asyncio.run(read_all(transaction_folders(out / "transactions"), cfg, runner, TODAY, progress=lambda _: None))
    return ts, runner


# --- dates --------------------------------------------------------------------------------------------------------

def test_federal_holidays_and_business_days():
    h = federal_holidays(2026)
    assert h[date(2026, 10, 12)] == "Columbus Day" and h[date(2026, 11, 26)] == "Thanksgiving Day"
    assert h[date(2026, 7, 3)] == "Independence Day"  # July 4, 2026 is a Saturday
    assert federal_holidays(2027)[date(2027, 12, 31)] == "New Year's Day (observed)"  # Jan 1, 2028 is a Saturday
    cal = Calendar()
    assert cal.add_business_days(date(2026, 10, 9), 1) == date(2026, 10, 13)  # Friday + 1 skips the weekend and Columbus Day
    assert cal.add_business_days(date(2026, 11, 25), 1) == date(2026, 11, 27)
    assert Calendar(extra=[date(2026, 11, 27)]).add_business_days(date(2026, 11, 25), 1) == date(2026, 11, 30)


def test_deadline_wording_is_read_in_code():
    cal = Calendar()
    anchors = {"acceptance": date(2026, 10, 2), "closing": date(2026, 11, 25)}

    def due(term, roll=True, days_mean="calendar"):
        return compute(parse_term(term, days_mean), anchors, cal, roll)[0]
    assert due("within ten (10) days after the Acceptance Date") == date(2026, 10, 13)  # Columbus Day rolls
    assert due("within ten (10) days after the Acceptance Date", roll=False) == date(2026, 10, 12)
    assert due("twenty-one (21) days after the Acceptance Date") == date(2026, 10, 23)
    assert due("within three (3) business days after the Effective Date") == date(2026, 10, 7)
    assert due("within 3 days after acceptance", days_mean="business") == date(2026, 10, 7)
    assert due("no later than five (5) days prior to Closing") == date(2026, 11, 20)
    assert due("within 24 hours before closing") == date(2026, 11, 24)
    assert due("on or before December 1, 2026") == date(2026, 12, 1)
    assert due("at closing") == date(2026, 11, 25)
    assert parse_term("7 days after receipt of the title commitment").kind == "event"
    assert parse_term("promptly").kind == "unknown"
    assert words_to_number("Four Hundred Twenty-Five Thousand Dollars") == 425000
    assert words_to_number("One Million Two Hundred Fifty Thousand and 00/100 Dollars") == 1250000
    assert words_to_number("Three Hundred Eighty-Nine Thousand Five Hundred Dollars") == 389500
    assert time_rules('"Days" means calendar days. If a deadline falls on a Saturday it is extended to the next '
                      'business day.') == ("calendar", True)
    assert time_rules("Time is of the essence.") == ("", None)


def test_settings(tmp_path):
    p = tmp_path / "tc.toml"
    p.write_text('[tc]\ndays_mean = "working"\n')
    with pytest.raises(ValueError, match="days_mean"):
        load_tc(p)
    p.write_text('[tc]\nextra_holidays = ["2026-11-27"]\n')
    assert load_tc(p).extra_holidays == [date(2026, 11, 27)]


# --- the whole example --------------------------------------------------------------------------------------------

def test_committed_example_matches_the_generator(tmp_path):
    truth = make_sample(tmp_path)
    assert truth == json.loads((EXAMPLE / "truth.json").read_text())
    for f in tmp_path.rglob("*"):
        if f.is_file():
            rel = f.relative_to(tmp_path)
            assert f.read_bytes() == (EXAMPLE / rel).read_bytes(), rel


def test_end_to_end_on_the_synthetic_files_scores_full_marks(case, tmp_path):
    out, truth, cfg = case
    ts, runner = _run(case, tmp_path)
    assert runner.llm.usage.requests == 3
    write_all(tmp_path, ts, cfg, TODAY)
    s = score(json.loads((tmp_path / "results.json").read_text()), truth)
    assert (s["anchors_right"], s["files"], s["wrong_anchors"]) == (3, 3, [])
    assert (s["deadlines_right"], s["deadlines"], s["wrong_deadlines"]) == (17, 17, [])
    assert (s["ready_right"], s["flags_found"], s["flags_expected"], s["extra_holds"]) == (3, 4, 4, [])

    maple = tmp_path / "101_maple_ave"
    with open(maple / "timeline.csv") as f:
        rows = list(csv.DictReader(f))
    insp = next(r for r in rows if r["deadline"] == "Inspection objections")
    assert insp["date"] == "2026-10-13" and insp["status"] == "soon"
    assert "2026-10-12 is Columbus Day, so it moves to 2026-10-13" in insp["how"]
    assert rows[-1]["deadline"] == "Title objections" and rows[-1]["date"] == ""
    cal = (maple / "calendar.ics").read_bytes().decode()
    assert cal.startswith("BEGIN:VCALENDAR\r\n") and cal.count("BEGIN:VEVENT") == 8 and "DTSTART;VALUE=DATE:20261125" in cal
    assert all(len(line.encode()) <= 75 for line in cal.split("\r\n"))
    checklist = (maple / "checklist.md").read_text()
    assert "- [ ] Tue Oct 13, 2026: Make sure the inspection is done" in checklist
    intro = (maple / "emails" / "intro.txt").read_text()
    assert intro.startswith("To: rachel.moore@harborrealty.example, tom.reyes@lakeviewhomes.example")
    assert "contract and addenda" in intro and "- Wed Nov 25, 2026: Closing" in intro
    reminders = sorted(p.name for p in (maple / "emails" / "reminders").iterdir())
    assert reminders[0] == "2026-10-12_inspection_objections.txt" and "2026-10-30_loan_commitment.txt" in reminders
    loan = (maple / "emails" / "reminders" / "2026-10-30_loan_commitment.txt").read_text()
    assert loan.startswith("To: rachel.moore@harborrealty.example, loans@erieshorebank.example")
    with open(tmp_path / "review.csv") as f:
        review = list(csv.DictReader(f))
    assert [r["check"][:30] for r in review if r["must_fix"]] == ["the price in words is $389,500",
                                                                  "Inspection period ends 2026-11"]
    with open(tmp_path / "all_deadlines.csv") as f:
        assert next(csv.DictReader(f))["date"] == "2026-09-30"


def test_the_model_over_reaching_is_caught(case, tmp_path):
    out, truth, cfg = case
    base = oracle(truth)

    def sloppy(kwargs):
        r = json.loads(base(kwargs))
        if r["property"].startswith("101 Maple"):
            r["closing_date"] = "2026-11-20"  # missed the addendum
            r["changes"] = []
            r["deadlines"][3]["term"] = "within fifteen (15) days after the Acceptance Date"  # misread
            r["contacts"][0]["email"] = "rachel@harbor.example"  # invented
        if r["property"].startswith("22 Birch"):
            r["acceptance_date"] = "2026-09-29"  # not in the contract
        return json.dumps(r)

    ts, _ = _run(case, tmp_path, sloppy)
    maple, birch = ts[0], ts[1]
    codes = {f.code: f for f in maple.all_flags()}
    assert maple.closing == date(2026, 11, 25) and codes["closing_change_not_applied"].hold
    assert codes["deadline_not_in_text"].hold and "contact_email_not_in_text" in codes
    assert [c["email"] for c in maple.contacts][:1] == [""]
    assert birch.acceptance is None and not birch.ready
    assert all(d.due is None for d in birch.deadlines if d.name != "Closing" and d.name != "Possession")


def test_ics_escapes_text():
    from txcoord.check import Deadline, Transaction
    t = Transaction(folder="x", address="1 Main St, Town", deadlines=[Deadline("Closing", "", "", "both",
                                                                               date(2026, 1, 5), "a; b")])
    out = ics(t, TCConfig(remind_days=[1]), date(2026, 1, 1))
    assert "SUMMARY:1 Main St\\, Town: Closing" in out and "TRIGGER:-P1D" in out


# --- command line -------------------------------------------------------------------------------------------------

def test_cli_files_run_and_score(tmp_path, monkeypatch, capsys):
    from txcoord import __main__ as cli

    ex = tmp_path / "ex"
    cli.main(["sample", "--out", str(ex)])
    truth = json.loads((ex / "truth.json").read_text())
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(cli.LLM, "from_settings",
                        classmethod(lambda cls, s: LLM(FakeClient(oracle(truth)), s.model, rpm=0, sleep=no_sleep)))
    cli.main(["files", "--files", str(ex / "transactions")])
    assert "3 transactions = 3 model requests" in capsys.readouterr().out
    out = tmp_path / "out"
    cli.main(["run", "--files", str(ex / "transactions"), "--tc", str(ex / "tc.toml"), "--today", "2026-10-12",
              "--out", str(out), "--model", "x:free"])
    printed = capsys.readouterr().out
    assert "WARNING: free models may log prompts" in printed and "Done: 2 of 3 files ready, 1 to fix" in printed
    cli.main(["score", "--run", str(out), "--truth", str(ex / "truth.json")])
    report = capsys.readouterr().out
    assert "Deadlines worked out right: 17 of 17 (100%)" in report
    assert "Expected checks raised: 4 of 4 (100%); other must-fix checks: 0" in report
