import asyncio
import json
import re
from datetime import date
from pathlib import Path

import pytest

from agentkit.llm import LLM
from agentkit.runner import Runner
from grants.draft import DraftReply, check_draft, gather, load_docs, load_request, read_budget
from grants.funders import apply_rules, geography, grant_range, read_funders
from grants.org import load_org
from grants.output import write_draft, write_prospects
from grants.pipeline import CACHE_VERSION, draft_letter, prospect_all
from grants.prospect import FitReply, check_fit
from grants.sample import make_sample, score

from .fakes import FakeClient, no_sleep, user_prompt

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "grants"
TODAY = date(2026, 10, 12)


@pytest.fixture(scope="module")
def case(tmp_path_factory):
    out = tmp_path_factory.mktemp("grants")
    truth = make_sample(out)
    org = load_org(out / "org.toml")
    return out, truth, org


def _funders(out):
    return read_funders(out / "funders.csv", out / "guidelines")


# --- a perfect stand-in model -------------------------------------------------------------------------------------

DRAFT = [  # (section, text, phrases whose sources it cites)
    ("Introduction and request", "Riverbend Youth Literacy Project helps children in Millbrook County read at grade "
     "level by third grade. We respectfully ask the Foundation for $25,000 to support Summer Reading Bridge, our "
     "six-week summer reading program, through the Riverbend Community Foundation's education grants.", ["O1"]),
    ("The need", "In Millbrook County, 41% of third graders scored below proficient on the 2025 Ohio state reading "
     "test, compared with 35% statewide. 19% of the county's children live below the poverty line, and teachers "
     "report that many students lose two to three months of reading progress over the summer.",
     ["41% of third graders", "19% of Millbrook", "two to three months"]),
    ("The program", "Summer Reading Bridge runs for six weeks at 4 library sites for children finishing grades 1 to 4. "
     "Each day includes small-group reading instruction, free books to take home and a healthy lunch, which fits the "
     "Foundation's priority on early literacy and summer learning.", ["at 4 library sites", "F1"]),
    ("Outcomes and evaluation", "In summer 2026, 160 children attended and 82% kept or improved their spring reading "
     "level. We compare each child's June and August reading levels and count children who attend at least 20 of the "
     "30 program days as completers.", ["160 children attended", "20 of the 30"]),
    ("Organization and budget", "Founded in 2012, Riverbend now works in 9 schools and 4 public library branches with a "
     "staff of 8 and about 120 volunteers. The program's $95,000 budget includes $48,000 for site coordinator "
     "stipends and $18,000 for books and materials.", ["Founded in 2012", "A staff of 8", "B1"]),
]


def sources_in(prompt: str) -> dict[str, str]:
    block = prompt.split("SOURCES\n", 1)[1].split("\n\nReturn JSON", 1)[0]
    out, last = {}, None
    for line in block.splitlines():
        m = re.match(r"^\[([A-Z]\d+(?:-\d+)?)\] (.*)$", line)
        if m:
            last = m.group(1)
            out[last] = m.group(2)
        elif last:
            out[last] += "\n" + line
    return out


def cite(srcs: dict[str, str], keys: list[str]) -> list[str]:
    return [k if k in srcs else next(i for i, t in srcs.items() if k in t) for k in keys]


def oracle(truth: dict, sections=DRAFT):
    replies = {f["funder"]: f["reply"] for f in truth["funders"] if f["reply"]}

    def respond(kwargs):
        prompt = user_prompt(kwargs)
        if prompt.startswith("OUR NONPROFIT"):
            name = re.search(r"^Funder: (.+)$", prompt, re.M).group(1)
            return json.dumps(replies[name])
        srcs = sources_in(prompt)
        return json.dumps({"sections": [{"name": n, "text": t, "sources": cite(srcs, k)} for n, t, k in sections]})
    return respond


def _runner(tmp_path, responder):
    return Runner(LLM(FakeClient(responder), "test-model", rpm=0, sleep=no_sleep), tmp_path / "cache", CACHE_VERSION)


# --- settings and rules -------------------------------------------------------------------------------------------

def test_org_and_request_settings(case, tmp_path):
    out, _, org = case
    assert org.state == "OH" and [p.name for p in org.programs] == ["Reading Buddies", "Summer Reading Bridge",
                                                                     "Family Literacy Nights"]
    req = load_request(out / "request.toml")
    assert (req.funder, req.amount, len(req.sections), req.word_limit) == ("Riverbend Community Foundation", 25000, 5,
                                                                            500)
    bad = tmp_path / "o.toml"
    bad.write_text('[org]\nname = "X"\nmission = "Y"\nstate = "Ohio"\n')
    with pytest.raises(ValueError, match="at least one \\[\\[program\\]\\]"):
        load_org(bad)
    bad.write_text('[request]\nfunder = "X"\n')
    with pytest.raises(ValueError, match="needs program, amount"):
        load_request(bad)


def test_grant_sizes_and_geography_are_read_in_code(case):
    _, _, org = case
    assert grant_range("$10,000 - $30,000") == (10000, 30000)
    assert grant_range("Up to $10,000") == (0, 10000) and grant_range("$50,000+") == (50000, 0)
    assert grant_range("$5k-$15k") == (5000, 15000) and grant_range("varies") is None
    assert geography("Millbrook County", org)[0] == "ok"
    assert geography("Franklin County, Ohio", org)[0] == "no"
    assert geography("Ohio, Michigan and Indiana", org)[0] == "ok"
    assert geography("the Midwest", org)[0] == "ok" and geography("New England", org)[0] == "no"
    assert geography("Nationwide", org)[0] == "ok" and geography("Our region", org)[0] == "unclear"


def test_funders_are_ruled_out_in_code(case):
    out, truth, org = case
    funders = _funders(out)
    for f in funders:
        apply_rules(f, org, TODAY)
    by = {f.name: f for f in funders}
    for t in truth["funders"]:
        f = by[t["funder"]]
        if t["ruled_out"]:
            assert len(f.out) == 1 and t["ruled_out"] in f.out[0], f.name
        else:
            assert f.out == [], f.name
    assert by["Harmon Family Foundation"].guidelines_source == "harmon-family-foundation.md"
    assert by["Buckeye Kids Reading Initiative"].notes == ["deadline 2026-10-20 is only 8 days away"]
    assert by["Great Lakes Literacy Fund"].rolling


def test_fit_checks_quotes_programs_and_asks(case):
    out, truth, org = case
    f = next(x for x in _funders(out) if x.name == "Great Lakes Literacy Fund")
    apply_rules(f, org, TODAY)
    r = FitReply.model_validate({"fit": 95, "best_program": "Tutoring", "priorities_matched": [
        {"priority": "tutoring", "quote": "We fund tutoring in every state"}], "suggested_ask": 90000,
        "deadline": "2026-12-01"})
    p = check_fit(r, f, org)
    assert p.fit == 40 and p.best_program == "" and p.suggested_ask == 50000
    assert p.checks == ["1 priorities dropped: the quote is not in the funder's information",
                        "fit lowered from 95 to 40: no priority quoted from the funder's own words",
                        "'Tutoring' is not one of our programs",
                        "suggested ask $90,000 is outside $20,000-$50,000; using $50,000",
                        "the model gives a deadline of 2026-12-01; the funder list says rolling"]


# --- the whole example --------------------------------------------------------------------------------------------

def test_committed_example_matches_the_generator(tmp_path):
    truth = make_sample(tmp_path)
    assert truth == json.loads((EXAMPLE / "truth.json").read_text())
    for f in tmp_path.rglob("*"):
        if f.is_file():
            rel = f.relative_to(tmp_path)
            assert f.read_bytes() == (EXAMPLE / rel).read_bytes(), rel


def test_end_to_end_prospect_and_draft_score_full_marks(case, tmp_path):
    out, truth, org = case
    runner = _runner(tmp_path, oracle(truth))
    prospects = asyncio.run(prospect_all(_funders(out), org, runner, TODAY, progress=lambda _: None))
    assert runner.llm.usage.requests == 4
    rows = write_prospects(tmp_path, prospects)
    assert [r["funder"] for r in rows[:3]] == ["Buckeye Kids Reading Initiative", "Riverbend Community Foundation",
                                               "Great Lakes Literacy Fund"]
    assert rows[0]["priorities"].startswith("summer reading for grades K-5: \"We fund summer")

    funders = _funders(out)
    req = load_request(out / "request.toml")
    funder = next(f for f in funders if f.name == req.funder)
    d, ev = asyncio.run(draft_letter(req, org, funder, load_docs(out / "docs"), read_budget(out / "budget.csv"),
                                     [f.name for f in funders], runner, TODAY))
    assert runner.llm.usage.requests == 5
    assert d.flags == [] and d.ready and len(d.sections) == 5
    assert {"O1", "F1", "B1"} <= set(ev.items) and len(ev.items) > 10  # small document sets go in whole
    write_draft(tmp_path, d, org, funder, ev, TODAY)
    s = score(json.loads((tmp_path / "prospects.json").read_text()), json.loads((tmp_path / "draft.json").read_text()),
              truth)
    assert (s["status_right"], s["funders"], s["wrong_status"]) == (8, 8, [])
    assert (s["ruled_out_right"], s["ruled_out_expected"]) == (4, 4)
    assert (s["program_right"], s["program_expected"]) == (3, 3)
    assert s["draft"]["ready"] and s["draft"]["sections"] == 5 and s["draft"]["must_fix"] == []

    import docx
    doc = docx.Document(str(tmp_path / "draft_riverbend_community_foundation.docx"))
    text = "\n".join(p.text for p in doc.paragraphs)
    assert "Dear Ms. Gomez," in text and "Sincerely," in text and "Ana Torres, Executive Director" in text
    assert "Checks and sources (delete this page before sending)" in text and "[B1] budget: Budget for Summer" in text
    md = (tmp_path / "draft_riverbend_community_foundation.md").read_text()
    assert md.startswith("# Letter of inquiry: Summer Reading Bridge") and "## The need" in md


def test_a_reused_or_padded_draft_is_caught(case, tmp_path):
    out, truth, org = case
    sloppy = [list(x) for x in DRAFT]
    sloppy[0][1] = ("Riverbend Youth Literacy Project requests $30,000 from the Great Lakes Literacy Fund for Summer "
                    "Reading Bridge.")
    sloppy[1][1] += " Last year we served 500 students across [COUNTY]."
    sloppy[2][1] = " ".join(["Summer Reading Bridge helps children read."] * 30)
    funders = _funders(out)
    req = load_request(out / "request.toml")
    funder = next(f for f in funders if f.name == req.funder)
    runner = _runner(tmp_path, oracle(truth, [tuple(x) for x in sloppy]))
    d, _ = asyncio.run(draft_letter(req, org, funder, load_docs(out / "docs"), read_budget(out / "budget.csv"),
                                    [f.name for f in funders], runner, TODAY))
    holds = [f.text for f in d.flags if f.hold]
    assert not d.ready
    assert "asks for $30,000; the request is $25,000" in holds
    assert "names another funder: Great Lakes Literacy Fund" in holds
    assert "states 500, which the cited sources don't say" in holds and "placeholder '[COUNTY]'" in holds
    assert "180 words; the limit is 120" in holds
    r = check_draft(DraftReply.model_validate({"sections": []}), req, org, funder, gather(req, org, funder,
                    load_docs(out / "docs"), {}), [], {})
    assert [f.text for f in r.flags] == ["section missing"] * 5


# --- command line -------------------------------------------------------------------------------------------------

def test_cli_funders_prospect_draft_and_score(tmp_path, monkeypatch, capsys):
    from grants import __main__ as cli

    ex = tmp_path / "ex"
    cli.main(["sample", "--out", str(ex)])
    truth = json.loads((ex / "truth.json").read_text())
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(cli.LLM, "from_settings",
                        classmethod(lambda cls, s: LLM(FakeClient(oracle(truth)), s.model, rpm=0, sleep=no_sleep)))
    base = ["--org", str(ex / "org.toml"), "--funders", str(ex / "funders.csv"), "--guidelines", str(ex / "guidelines"),
            "--today", "2026-10-12"]
    cli.main(["funders", *base])
    assert "8 funders; 4 left to score = 4 model requests." in capsys.readouterr().out
    out = tmp_path / "out"
    cli.main(["prospect", *base, "--out", str(out), "--model", "x:free"])
    printed = capsys.readouterr().out
    assert "WARNING: free models may log prompts" in printed
    assert "Done: 3 strong, 0 possible, 1 weak, 4 ruled out; 4 model requests" in printed
    cli.main(["draft", *base, "--request", str(ex / "request.toml"), "--docs", str(ex / "docs"), "--budget",
              str(ex / "budget.csv"), "--out", str(out)])
    assert "ready to edit; 1 model request" in capsys.readouterr().out
    cli.main(["score", "--run", str(out), "--truth", str(ex / "truth.json")])
    report = capsys.readouterr().out
    assert "Funders sorted right (strong / possible / weak / ruled out): 8 of 8 (100%)" in report
    assert "Draft: 5 of 5 sections" in report and "ready to edit" in report
