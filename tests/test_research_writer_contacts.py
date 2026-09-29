import asyncio

from leadagent.config import WriterConfig, load_spec
from leadagent.contacts import SyntaxOnlyVerifier, load_companies, load_contacts, pick_contacts
from leadagent.models import CompanyResearch, Contact, Page, Signal
from datetime import date

from leadagent.research import evidence_on_page, finalize, is_stale, latest_date, verify
from leadagent.writer import problems

TODAY = date(2026, 9, 29)
PAGES = [
    Page(url="https://acme.com/", text="Acme builds payroll software for dental clinics."),
    Page(url="https://acme.com/careers", text="We are hiring a Head of Growth to lead marketing in the US."),
]


def test_verify_drops_uncited_signals_and_flags_unsupported_evidence():
    r = CompanyResearch(signals=[
        Signal(type="hiring", evidence="hiring a Head of Growth to lead marketing", source_url="https://www.acme.com/careers/"),
        Signal(type="funding", evidence="raised a $50M Series B", source_url="https://acme.com/careers"),
        Signal(type="launch", evidence="launched in Japan", source_url="https://acme.com/made-up"),
    ])
    out = verify(r, PAGES)
    assert [s.type for s in out.signals] == ["hiring", "funding"]
    assert [s.verified for s in out.signals] == [True, False]


def test_why_now_comes_only_from_a_verified_current_signal():
    # the Supabase case: model picks an unverified claim as the headline; we fall back to the verified one
    r = CompanyResearch(why_now_index=1, signals=[
        Signal(type="hiring", evidence="hiring a Head of Growth to lead marketing", source_url="https://acme.com/careers"),
        Signal(type="funding", evidence="raised $1 billion", source_url="https://acme.com/careers"),
    ])
    out = finalize(r, PAGES, TODAY)
    assert out.why_now == "hiring a Head of Growth to lead marketing"


def test_why_now_empty_without_verified_signal():
    r = CompanyResearch(signals=[Signal(evidence="opened a Tokyo office", source_url="https://acme.com/")], why_now_index=0)
    assert finalize(r, PAGES, TODAY).why_now == ""


def test_old_news_is_not_a_reason_to_reach_out_now():
    # the Resend case: a Dec 2024 raise called "recent" in Sep 2026
    pages = [Page(url="https://resend.com/about", text="Resend raises $18M Series A (Dec 4, 2024). We are hiring engineers.")]
    r = CompanyResearch(why_now_index=0, signals=[
        Signal(type="funding", evidence="Resend raises $18M Series A (Dec 4, 2024)", source_url="https://resend.com/about"),
        Signal(type="hiring", evidence="We are hiring engineers", source_url="https://resend.com/about"),
    ])
    out = finalize(r, pages, TODAY)
    assert [s.stale for s in out.signals] == [True, False]
    assert out.why_now == "We are hiring engineers"


def test_latest_date_parsing():
    assert latest_date("Series A (Dec 4, 2024)") == (2024, 12)
    assert latest_date("2026-09", "launched in 2023") == (2026, 9)
    assert latest_date("In 2023, we launched") == (2023, 12)
    assert latest_date("no dates here", None) is None
    assert not is_stale(Signal(evidence="released September 24, 2026"), TODAY)
    assert is_stale(Signal(evidence="launched", date="2025-06"), TODAY)


def test_failed_criterion_caps_score_and_fit():
    # the Supabase case: score 9 against a Seed-Series A profile despite a late-stage raise
    r = CompanyResearch(fits_icp=True, score=9, criteria=[
        {"name": "industry", "status": "met"},
        {"name": "stage", "status": "Not met", "evidence": "raised $1B, Series E"},
        {"name": "size", "status": "unknown"},
    ])
    out = finalize(r, PAGES, TODAY)
    assert out.score == 4 and out.fits_icp is False
    assert "stage not met" in out.score_reasons[-1]


def test_evidence_check_is_word_based():
    assert evidence_on_page("Head of Growth hiring", PAGES[1].text)
    assert not evidence_on_page("Series B funding round", PAGES[1].text)


def test_research_model_coerces_messy_model_output():
    r = CompanyResearch.model_validate({"score": "7.6", "fits_icp": "yes", "signals": ["hiring"], "score_reasons": "fit"})
    assert r.score == 8 and r.fits_icp is True
    assert r.signals[0].evidence == "hiring" and r.score_reasons == ["fit"]
    assert CompanyResearch.model_validate({"score": 42}).score == 10


def test_writer_problems():
    cfg = WriterConfig(max_words=13)
    assert problems("Your new Head of Growth role suggests outbound is next on the list.", cfg) == []
    assert "uses banned phrase 'i noticed'" in problems("I noticed you are hiring.", cfg)
    assert any("limit is 13" in p for p in problems("you " * 14, cfg))
    assert "contains an exclamation mark" in problems("Congrats to your team on the raise!", cfg)
    assert "contains template brackets" in problems("Hi {first_name}, saw your new role opening.", cfg)


def test_writer_rejects_placeholders_and_third_person_lines():
    cfg = WriterConfig()
    # the Supabase/Linear case: the model returned "..." and it was accepted
    assert "not a sentence" in problems("...", cfg)
    assert "contains an ellipsis placeholder" in problems("Your team is growing fast...", cfg)
    # the Resend case: written about the company instead of to it
    assert 'does not speak to the reader ("you"/"your")' in problems(
        "Resend raised an $18M Series A and is a timely partner for growth teams.", cfg)
    assert problems("Saw you're hiring an Account Executive and a Product Marketing Manager.", cfg) == []


def test_load_spec_and_fingerprint(tmp_path):
    p = tmp_path / "icp.toml"
    p.write_text('[icp]\nname = "x"\ndescription = "y"\ntarget_titles = ["CEO"]\n[writer]\nmax_words = 20\nbanned_phrases = ["foo"]\n')
    icp, w = load_spec(p)
    assert icp.target_titles == ["CEO"] and w.max_words == 20
    assert "foo" in w.banned_phrases and "i noticed" in w.banned_phrases
    fp = icp.fingerprint()
    icp.min_score = 9
    assert icp.fingerprint() != fp


def test_companies_and_contacts_from_csv(tmp_path):
    comp = tmp_path / "c.csv"
    comp.write_text("Company,Website\nAcme,https://www.acme.com\nAcme again,acme.com\nBeta,beta.io\n,\n")
    assert load_companies(comp) == [("acme.com", "Acme"), ("beta.io", "Beta")]
    assert load_contacts(comp) == {}

    people = tmp_path / "p.csv"
    people.write_text("First Name,Last Name,Title,Email\nAna,Lee,VP Marketing,ana@acme.com\n"
                      "Bo,Ng,Head of Growth,bo@acme.com\nCy,Oz,Engineer,cy@acme.com\n")
    contacts = load_contacts(people)["acme.com"]
    picked = pick_contacts(contacts, ["Head of Growth", "VP Marketing", "CEO"], limit=2)
    assert [c.first_name for c in picked] == ["Bo", "Ana"]


def test_syntax_verifier():
    v = SyntaxOnlyVerifier()
    assert asyncio.run(v.verify("a@b.com")) == "unverified"
    assert asyncio.run(v.verify("not-an-email")) == "invalid_syntax"
    assert asyncio.run(v.verify("")) == "missing"


def test_contact_full_name():
    assert Contact(domain="a.com", first_name="Ana", last_name="Lee").full_name == "Ana Lee"


def test_load_dotenv_never_overrides_existing_env(tmp_path, monkeypatch):
    from leadagent.config import Settings, load_dotenv

    env = tmp_path / ".env"
    env.write_text('# comment\nexport OPENROUTER_API_KEY="sk-or-file"\nLEADAGENT_MODEL=paid/model  # note\n'
                   "LEADAGENT_RPM=\nnot a line\n")
    for name in ("OPENROUTER_API_KEY", "LEADAGENT_RPM"):  # set-then-delete so pytest restores them afterwards
        monkeypatch.setenv(name, "placeholder")
        monkeypatch.delenv(name)
    monkeypatch.setenv("LEADAGENT_MODEL", "from/shell")
    assert load_dotenv(env) == ["OPENROUTER_API_KEY"]
    s = Settings.from_env()
    assert s.api_key == "sk-or-file" and s.model == "from/shell" and s.rpm == 18.0
    assert "sk-or-file" not in repr(s)
    assert load_dotenv(tmp_path / "missing.env") == []


def test_verdict_and_score_cannot_disagree():
    # the Linear case: "not_fit" but 9/10
    out = finalize(CompanyResearch(fits_icp=False, score=9), PAGES, TODAY)
    assert out.score == 4 and out.score_reasons[-1] == "capped at 4: judged not a fit"


def test_why_now_prefers_buying_signals_over_changelog_trivia():
    # the PostHog/Resend case: a changelog entry chosen over hiring
    pages = [Page(url="https://x.com/", text="BigQuery service account imports shipped. We are hiring a Head of Growth.")]
    r = CompanyResearch(why_now_index=0, signals=[
        Signal(type="product_update", evidence="BigQuery service account imports", source_url="https://x.com/", date="2026-09"),
        Signal(type="hiring", evidence="hiring a Head of Growth", source_url="https://x.com/"),
    ])
    assert finalize(r, pages, TODAY).why_now == "hiring a Head of Growth"
    # among equal-priority signals the model's own choice wins
    r2 = CompanyResearch(why_now_index=1, signals=[
        Signal(type="hiring", evidence="BigQuery service account imports", source_url="https://x.com/"),
        Signal(type="hiring", evidence="hiring a Head of Growth", source_url="https://x.com/"),
    ])
    assert finalize(r2, pages, TODAY).why_now == "hiring a Head of Growth"
