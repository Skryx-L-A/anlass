"""Minimal, dependency-free HTML text extraction.

No parser dependency: a job posting's visible text does not need a DOM, only the
tag boundaries and entities, and the stdlib's :mod:`html.parser` already handles
both. Scripts and styles are dropped; block-level tags become line breaks so
paragraphs do not run together.

Navigation, footers, forms and cookie-consent blocks are dropped too
(Q11): measured on the two postings, they contributed
15.586 and 18.156 characters of boilerplate to ``page_text`` and nothing else -
the posting text itself was already in the lead. A block is a consent banner if
its ``class``/``id`` names one of the common vendors or the German/English words
for it; that is a heuristic on markup, not on the visible words, so it cannot
mistake an actual sentence about data protection in the posting for the banner.
"""

from __future__ import annotations

from html.parser import HTMLParser

__all__ = ["extract_links", "html_to_text", "page_title"]

_SKIP_TAGS = {"script", "style", "noscript", "template", "svg", "nav", "footer", "aside", "form"}
_BLOCK_TAGS = {
    "p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6",
    "section", "article", "header", "ul", "ol", "table", "blockquote",
}
#: Substrings of a ``class``/``id`` that mark a cookie-consent block, lower-cased.
#: "cookie" and "consent" alone already cover most home-grown banners; the rest are
#: the consent-management vendors and German terms seen on the postings.
_CONSENT_MARKERS = (
    "cookie", "consent", "gdpr", "dsgvo", "onetrust", "trackingbanner", "privacy-banner",
)


def _is_consent_block(attrs: list[tuple[str, str | None]]) -> bool:
    marker = " ".join((value or "").lower() for name, value in attrs if name in ("class", "id"))
    return any(token in marker for token in _CONSENT_MARKERS)


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        #: Stack of (tag, skipped) for every still-open element; skipped is inherited
        #: from the parent so nothing inside an already-dropped block leaks out, even
        #: past a mismatched or missing closing tag.
        self._stack: list[tuple[str, bool]] = []
        self._chunks: list[str] = []
        self._in_title = False
        self.title: str | None = None

    @property
    def _skipping(self) -> bool:
        return any(skip for _, skip in self._stack)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        parent_skipping = self._skipping
        skip = parent_skipping or tag in _SKIP_TAGS or _is_consent_block(attrs)
        self._stack.append((tag, skip))
        if not skip and tag in _BLOCK_TAGS:
            self._chunks.append("\n")
        if tag == "title":
            self._in_title = True

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if not self._skipping and tag in _BLOCK_TAGS:
            self._chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:
        index = next(
            (i for i in range(len(self._stack) - 1, -1, -1) if self._stack[i][0] == tag), None
        )
        if index is None:
            if tag == "title":
                self._in_title = False
            return
        _, was_skipped = self._stack[index]
        del self._stack[index:]
        if not was_skipped and tag in _BLOCK_TAGS:
            self._chunks.append("\n")
        if tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._skipping:
            return
        if self._in_title and self.title is None:
            data = data.strip()
            if data:
                self.title = data
        self._chunks.append(data)

    def text(self) -> str:
        joined = "".join(self._chunks)
        lines = (" ".join(line.split()) for line in joined.splitlines())
        return "\n".join(line for line in lines if line).strip()


class _LinkExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._text_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        href = dict(attrs).get("href")
        if href:
            self._href = href
            self._text_parts = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._href is not None:
            self.links.append((self._href, " ".join("".join(self._text_parts).split())))
            self._href = None


def html_to_text(html: str) -> str:
    """Visible text of an HTML document, one block per line, tags stripped."""
    parser = _TextExtractor()
    parser.feed(html)
    parser.close()
    return parser.text()


def page_title(html: str) -> str | None:
    """Content of ``<title>``, or ``None`` if the document has none."""
    parser = _TextExtractor()
    parser.feed(html)
    parser.close()
    return parser.title


def extract_links(html: str) -> list[tuple[str, str]]:
    """Every ``<a href>`` with its visible text, in document order, hrefs untouched."""
    parser = _LinkExtractor()
    parser.feed(html)
    parser.close()
    return parser.links
