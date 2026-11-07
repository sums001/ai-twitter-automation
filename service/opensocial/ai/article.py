"""Fetch the full text of the one story being written about.

Feeds carry a headline and a blurb at best; the article itself has the
numbers, names and quotes that make a post specific. Only the winning story's
article is fetched (one HTTP request per post, no tokens), using the standard
library HTML parser so there is no extra dependency.

Google News links are JavaScript redirect pages rather than articles, so they
are skipped in favour of another outlet's URL for the same story. Paywalls,
consent walls and blocked requests simply return ``None`` and the writer falls
back to the feed summaries.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser

import httpx

FETCH_TIMEOUT = 8.0
MAX_BYTES = 1_500_000
MIN_ARTICLE_CHARS = 300
# A browser-like UA: many news sites refuse obvious bot user agents outright.
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.9",
}
# Redirect pages and platforms whose pages aren't articles (or block fetches).
_UNFETCHABLE_HOSTS = (
    "news.google.com", "reddit.com", "youtube.com", "youtu.be", "x.com",
    "twitter.com", "news.ycombinator.com",
)

# Containers whose paragraphs are page furniture, not the article.
_SKIP_TAGS = {
    "script", "style", "noscript", "nav", "footer", "header", "aside", "form",
    "figure", "figcaption", "button", "svg", "template", "iframe",
}
_BLOCK_TAGS = {"p", "h2", "h3", "li", "blockquote"}
_VOID_TAGS = {"br", "img", "hr", "input", "meta", "link", "source", "wbr"}
_WS_RE = re.compile(r"\s+")
_BOILERPLATE_RE = re.compile(
    r"subscribe|sign up|newsletter|cookies?|all rights reserved|advertisement|"
    r"click here|read more|related:|follow us|share this|terms of (use|service)|"
    r"privacy policy|download the app",
    re.IGNORECASE,
)


class _ParagraphParser(HTMLParser):
    """Collects text blocks, noting which sit inside an <article> element."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[str] = []
        self.skip_depth = 0
        self.article_depth = 0
        self.block: list[str] | None = None
        self.block_in_article = False
        self.blocks: list[tuple[str, bool]] = []

    def handle_starttag(self, tag, attrs):
        if tag in _VOID_TAGS:
            return
        self.stack.append(tag)
        if tag in _SKIP_TAGS:
            self.skip_depth += 1
        if tag == "article":
            self.article_depth += 1
        if tag in _BLOCK_TAGS and self.skip_depth == 0 and self.block is None:
            self.block = []
            self.block_in_article = self.article_depth > 0

    def handle_endtag(self, tag):
        if tag in _VOID_TAGS or tag not in self.stack:
            return
        # Pop up to and including the matching tag (tolerates sloppy HTML).
        while self.stack:
            top = self.stack.pop()
            if top in _SKIP_TAGS:
                self.skip_depth = max(0, self.skip_depth - 1)
            if top == "article":
                self.article_depth = max(0, self.article_depth - 1)
            if top in _BLOCK_TAGS and self.block is not None:
                text = _WS_RE.sub(" ", "".join(self.block)).strip()
                if text:
                    self.blocks.append((text, self.block_in_article))
                self.block = None
            if top == tag:
                break

    def handle_data(self, data):
        if self.block is not None and self.skip_depth == 0:
            self.block.append(data)


def extract_article_text(html: str) -> str | None:
    """Main article text from an HTML page, or ``None`` if there isn't enough.

    Keeps paragraph-like blocks of real prose (40+ chars, 6+ words), preferring
    the ones inside ``<article>`` when the page marks one up.
    """
    parser = _ParagraphParser()
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        return None

    def prose(text: str) -> bool:
        return len(text) >= 40 and len(text.split()) >= 6 and not _BOILERPLATE_RE.search(text[:120])

    in_article = [t for t, inside in parser.blocks if inside and prose(t)]
    blocks = in_article if sum(map(len, in_article)) >= MIN_ARTICLE_CHARS else [
        t for t, _ in parser.blocks if prose(t)
    ]
    text = "\n".join(dict.fromkeys(blocks))  # drop repeated blocks, keep order
    return text if len(text) >= MIN_ARTICLE_CHARS else None


def fetchable(url: str | None) -> bool:
    if not url or not url.startswith(("http://", "https://")):
        return False
    return not any(host in url for host in _UNFETCHABLE_HOSTS)


def fetch_article(url: str, *, timeout: float = FETCH_TIMEOUT) -> str | None:
    """GET ``url`` and extract its article text; ``None`` on any failure."""
    if not fetchable(url):
        return None
    try:
        with httpx.Client(
            timeout=timeout, headers=_HEADERS, follow_redirects=True
        ) as client:
            resp = client.get(url)
            if resp.status_code != 200:
                return None
            if "html" not in resp.headers.get("content-type", "html"):
                return None
            body = resp.text[:MAX_BYTES]
    except Exception:
        return None
    return extract_article_text(body)


def fetch_first_article(urls: list[str], *, max_tries: int = 3) -> str | None:
    """Try the story's outlet URLs in order until one yields article text."""
    tries = 0
    for url in urls:
        if not fetchable(url):
            continue
        tries += 1
        text = fetch_article(url)
        if text:
            return text
        if tries >= max_tries:
            break
    return None
