"""HTML to text extraction using only the standard library.

Adding a parser dependency for this is not worth it: the goal is readable text
plus the links, not a faithful DOM. Script, style and template content is
dropped entirely, which also removes the most common place injection payloads
hide.
"""

from __future__ import annotations

import re
from html import unescape
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

DROP_TAGS = frozenset({"script", "style", "noscript", "template", "svg", "canvas", "iframe"})
BLOCK_TAGS = frozenset(
    {
        "p",
        "div",
        "section",
        "article",
        "header",
        "footer",
        "main",
        "aside",
        "nav",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "li",
        "tr",
        "br",
        "hr",
        "blockquote",
        "pre",
        "table",
        "ul",
        "ol",
        "dl",
        "dt",
        "dd",
        "form",
        "figure",
        "figcaption",
    }
)
#: Attributes that can carry text a human never sees.
HIDDEN_MARKERS = ("display:none", "visibility:hidden", "font-size:0", "opacity:0")


class _TextExtractor(HTMLParser):
    def __init__(self, base_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.parts: list[str] = []
        self.links: list[str] = []
        self.title = ""
        self._drop_depth = 0
        self._hidden_depth = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in DROP_TAGS:
            self._drop_depth += 1
            return
        attributes = {k.lower(): (v or "") for k, v in attrs}
        style = attributes.get("style", "").replace(" ", "").lower()
        if any(marker in style for marker in HIDDEN_MARKERS) or "hidden" in attributes:
            self._hidden_depth += 1
        if tag == "title":
            self._in_title = True
        if tag == "a":
            href = attributes.get("href", "").strip()
            if href and not href.startswith(("javascript:", "data:", "#", "mailto:")):
                absolute = urljoin(self.base_url, href)
                if urlparse(absolute).scheme in ("http", "https") and absolute not in self.links:
                    self.links.append(absolute)
        if tag in BLOCK_TAGS:
            self.parts.append("\n")
        if tag == "img":
            alt = attributes.get("alt", "").strip()
            if alt:
                self.parts.append(f"[image: {alt}] ")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in DROP_TAGS:
            self._drop_depth = max(0, self._drop_depth - 1)
            return
        if tag == "title":
            self._in_title = False
        if self._hidden_depth:
            self._hidden_depth = max(0, self._hidden_depth - 1)
        if tag in BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._drop_depth or self._hidden_depth:
            return
        if self._in_title:
            self.title += data.strip() + " "
            return
        if data.strip():
            self.parts.append(data)

    def error(self, message: str) -> None:  # pragma: no cover - Python 3.10 compatibility
        return


def extract_text(html: str, base_url: str = "") -> tuple[str, str, list[str]]:
    """Return ``(title, text, links)``."""
    parser = _TextExtractor(base_url)
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # noqa: S110
        # Real-world HTML is frequently malformed. Whatever was parsed before the
        # failure is still useful text, and a broken page must not break the tool.
        pass
    text = "".join(parser.parts)
    text = unescape(text)
    text = re.sub(r"[ \t\x0b\f\r]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    lines = [line.strip() for line in text.splitlines()]
    cleaned = "\n".join(line for line in lines if line)
    title = re.sub(r"\s+", " ", parser.title).strip()
    if not title:
        match = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
        title = unescape(re.sub(r"\s+", " ", match.group(1)).strip()) if match else ""
    return title[:300], cleaned, parser.links[:100]


def strip_tags(value: str) -> str:
    return re.sub(r"<[^>]+>", " ", value)
