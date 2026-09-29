"""Reads a company's own website: homepage plus the few pages that carry buying signals."""

from __future__ import annotations

import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx

from .models import Page

USER_AGENT = "Mozilla/5.0 (compatible; leadagent/0.1; company research)"

# Page types worth reading, in priority order, with words that identify them in a URL or link text.
PAGE_TYPES: dict[str, tuple[str, ...]] = {
    "careers": ("careers", "career", "jobs", "job", "hiring", "join-us", "joinus", "work-with-us", "open-positions", "vacancies"),
    "news": ("news", "press", "blog", "updates", "announcements", "newsroom", "changelog"),
    "about": ("about", "company", "team", "who-we-are", "our-story"),
    "customers": ("customers", "case-studies", "case-study", "clients", "success-stories"),
    "product": ("product", "products", "platform", "solutions", "services", "pricing"),
}
# Hosted job boards count as the company's careers page.
JOB_BOARD_HOSTS = ("boards.greenhouse.io", "job-boards.greenhouse.io", "jobs.lever.co", "jobs.ashbyhq.com", "apply.workable.com")
SKIP_EXTENSIONS = (".pdf", ".jpg", ".jpeg", ".png", ".gif", ".svg", ".zip", ".mp4", ".webp", ".xml")
SKIP_TAGS = {"script", "style", "noscript", "svg", "template", "iframe"}
BLOCK_TAGS = {"p", "div", "section", "article", "li", "ul", "ol", "br", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "table", "header", "footer", "nav", "main"}


def normalize_domain(value: str) -> str:
    """'https://www.Acme.com/about' -> 'acme.com'."""
    value = value.strip().lower()
    if "://" not in value:
        value = "http://" + value
    host = urlparse(value).hostname or ""
    return host[4:] if host.startswith("www.") else host


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.links: list[tuple[str, str]] = []
        self.title = ""
        self._skip = 0
        self._in_title = False
        self._href: str | None = None
        self._anchor: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in SKIP_TAGS:
            self._skip += 1
        elif tag == "title":
            self._in_title = True
        elif tag == "a":
            self._href = dict(attrs).get("href")
            self._anchor = []
        if tag in BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in SKIP_TAGS and self._skip:
            self._skip -= 1
        elif tag == "title":
            self._in_title = False
        elif tag == "a" and self._href is not None:
            self.links.append((self._href, " ".join(self._anchor).strip()))
            self._href = None
        if tag in BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        if self._in_title:
            self.title += data
            return
        if self._skip:
            return
        self.parts.append(data)
        if self._href is not None:
            self._anchor.append(data.strip())


def extract_text(html: str) -> tuple[str, str, list[tuple[str, str]]]:
    """Return (title, visible text, [(href, anchor text)]) with navigation noise de-duplicated."""
    p = _TextExtractor()
    p.feed(html)
    seen: set[str] = set()
    lines = []
    for raw in "".join(p.parts).split("\n"):
        line = re.sub(r"\s+", " ", raw).strip()
        if line and line not in seen:
            seen.add(line)
            lines.append(line)
    return re.sub(r"\s+", " ", p.title).strip(), "\n".join(lines), p.links


def _same_site(host: str, domain: str) -> bool:
    host = host[4:] if host.startswith("www.") else host
    return host == domain or host.endswith("." + domain)


def pick_subpages(base_url: str, domain: str, links: list[tuple[str, str]], max_pages: int) -> list[str]:
    """Choose at most max_pages links, one per page type, in PAGE_TYPES priority order."""
    candidates: dict[str, str] = {}
    for href, anchor in links:
        if not href or href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        url = urljoin(base_url, href).split("#")[0]
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        path = parsed.path.lower()
        if parsed.scheme not in ("http", "https") or path.endswith(SKIP_EXTENSIONS):
            continue
        on_job_board = host in JOB_BOARD_HOSTS
        if not (_same_site(host, domain) or on_job_board):
            continue
        if path in ("", "/"):
            continue
        haystack = path + " " + anchor.lower()
        for page_type, words in PAGE_TYPES.items():
            if page_type in candidates:
                continue
            if (page_type == "careers" and on_job_board) or any(re.search(rf"\b{re.escape(w)}\b", haystack) for w in words):
                candidates[page_type] = url
                break
    ordered = [candidates[t] for t in PAGE_TYPES if t in candidates]
    return ordered[:max_pages]


class SiteReader:
    """Fetches pages politely: honours robots.txt and reads only a handful of pages per company."""

    def __init__(self, client: httpx.AsyncClient, max_pages: int = 4, max_chars_per_page: int = 5000):
        self.client = client
        self.max_pages = max_pages
        self.max_chars = max_chars_per_page
        self._robots: dict[str, RobotFileParser | None] = {}

    @classmethod
    def default_client(cls) -> httpx.AsyncClient:
        return httpx.AsyncClient(follow_redirects=True, timeout=15.0, headers={"User-Agent": USER_AGENT})

    async def _allowed(self, url: str) -> bool:
        parsed = urlparse(url)
        root = f"{parsed.scheme}://{parsed.netloc}"
        if root not in self._robots:
            rp: RobotFileParser | None = None
            try:
                r = await self.client.get(root + "/robots.txt")
                if r.status_code == 200:
                    rp = RobotFileParser()
                    rp.parse(r.text.splitlines())
            except httpx.HTTPError:
                rp = None
            self._robots[root] = rp
        rp = self._robots[root]
        return True if rp is None else rp.can_fetch(USER_AGENT, url)

    async def _get(self, url: str) -> tuple[str, str] | None:
        if not await self._allowed(url):
            return None
        try:
            r = await self.client.get(url)
        except httpx.HTTPError:
            return None
        if r.status_code != 200 or "html" not in r.headers.get("content-type", "html"):
            return None
        return str(r.url), r.text

    async def read(self, domain: str) -> list[Page]:
        pages: list[Page] = []
        home = await self._get(f"https://{domain}/") or await self._get(f"http://{domain}/")
        if home is None:
            return pages
        home_url, html = home
        title, text, links = extract_text(html)
        pages.append(Page(url=home_url, title=title, text=text[: self.max_chars]))
        for url in pick_subpages(home_url, domain, links, self.max_pages - 1):
            got = await self._get(url)
            if got is None:
                continue
            final_url, sub_html = got
            sub_title, sub_text, _ = extract_text(sub_html)
            if sub_text:
                pages.append(Page(url=final_url, title=sub_title, text=sub_text[: self.max_chars]))
        return pages
