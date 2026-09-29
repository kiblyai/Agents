import asyncio

import httpx

from leadagent.fetch import SiteReader, extract_text, normalize_domain, pick_subpages

from .fakes import site_transport


def test_normalize_domain():
    assert normalize_domain("https://www.Acme.com/about?x=1") == "acme.com"
    assert normalize_domain("acme.io") == "acme.io"
    assert normalize_domain("  WWW.acme.co.uk ") == "acme.co.uk"


def test_extract_text_skips_scripts_and_dedupes_nav():
    html = """<html><head><title> Acme | Home </title><style>.x{}</style></head>
    <body><nav><a href="/about">About us</a></nav><script>var secret=1;</script>
    <p>We build   payroll software.</p><footer><a href="/about">About us</a></footer></body></html>"""
    title, text, links = extract_text(html)
    assert title == "Acme | Home"
    assert "payroll software" in text
    assert "secret" not in text
    assert text.count("About us") == 1
    assert ("/about", "About us") in links


def test_pick_subpages_prefers_signal_pages_and_stays_on_site():
    links = [
        ("/pricing", "Pricing"),
        ("https://twitter.com/acme", "Twitter"),
        ("/about-us", "About"),
        ("/blog/series-a", "We raised"),
        ("https://jobs.lever.co/acme", "Open roles"),
        ("/brochure.pdf", "Brochure"),
        ("mailto:hi@acme.com", "Email"),
        ("https://app.acme.com/careers", "Careers"),
    ]
    picked = pick_subpages("https://acme.com/", "acme.com", links, max_pages=3)
    # first careers link wins, including hosted job boards; off-site and file links are ignored
    assert picked == ["https://jobs.lever.co/acme", "https://acme.com/blog/series-a", "https://acme.com/about-us"]


def test_site_reader_honours_robots_and_reads_subpages():
    pages = {
        "https://acme.com/robots.txt": (200, "User-agent: *\nDisallow: /careers"),
        "https://acme.com/": (200, '<title>Acme</title><p>Payroll for clinics.</p><a href="/careers">Careers</a><a href="/news">News</a>'),
        "https://acme.com/careers": (200, "<p>We are hiring a Head of Growth.</p>"),
        "https://acme.com/news": (200, "<p>Acme raised a Series A.</p>"),
    }

    async def go():
        async with httpx.AsyncClient(transport=site_transport(pages)) as client:
            return await SiteReader(client, max_pages=4).read("acme.com")

    got = asyncio.run(go())
    urls = [p.url for p in got]
    assert urls == ["https://acme.com/", "https://acme.com/news"]
    assert "Series A" in got[1].text


def test_site_reader_unreachable_returns_nothing():
    async def go():
        async with httpx.AsyncClient(transport=site_transport({"https://other.com/": (200, "x")})) as client:
            return await SiteReader(client).read("deadco.com")

    assert asyncio.run(go()) == []
