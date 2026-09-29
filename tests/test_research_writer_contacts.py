import asyncio

from leadagent.config import WriterConfig, load_spec
from leadagent.contacts import SyntaxOnlyVerifier, load_companies, load_contacts, pick_contacts
from leadagent.models import CompanyResearch, Contact, Page, Signal
from leadagent.research import evidence_on_page, verify
from leadagent.writer import problems

PAGES = [
    Page(url="https://acme.com/", text="Acme builds payroll software for dental clinics."),
    Page(url="https://acme.com/careers", text="We are hiring a Head of Growth to lead marketing in the US."),
]


def test_verify_drops_uncited_signals_and_flags_unsupported_evidence():
    r = CompanyResearch(signals=[
        Signal(type="hiring", evidence="hiring a Head of Growth to lead marketing", source_url="https://www.acme.com/careers/"),
        Signal(type="funding", evidence="raised a $50M Series B", source_url="https://acme.com/careers"),
        Signal(type="launch", evidence="launched in Japan", source_url="https://acme.com/made-up"),
    ], why_now="Hiring a Head of Growth")
    out = verify(r, PAGES)
    assert [s.type for s in out.signals] == ["hiring", "funding"]
    assert [s.verified for s in out.signals] == [True, False]
    assert out.why_now == "Hiring a Head of Growth"


def test_verify_clears_why_now_without_verified_signal():
    r = CompanyResearch(signals=[Signal(evidence="opened a Tokyo office", source_url="https://acme.com/")],
                        why_now="Opened Tokyo office")
    assert verify(r, PAGES).why_now == ""


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
    assert any("words" in p for p in problems("word " * 14, cfg))
    assert "contains an exclamation mark" in problems("Congrats on the raise!", cfg)
    assert "contains template brackets" in problems("Hi {first_name}, saw the role.", cfg)


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
