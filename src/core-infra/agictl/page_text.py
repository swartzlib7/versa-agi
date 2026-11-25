"""Readable text of a web page without a browser (state_web_access.md §1.3).

`agictl search read` uses this. Standard library only: the agictl venv has no
HTML package. `fetch` does the network part; everything else is pure.
"""

from __future__ import annotations

import re
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser

TIMEOUT_SECONDS = 20
MAX_BYTES = 3 * 1024 * 1024
MAX_LINKS = 100
# Below this much text, on a page that loads scripts, the content is probably
# built in the browser (an app shell says "Loading…" or nothing at all).
SCRIPT_PAGE_TEXT_CHARS = 80
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0.0.0 Safari/537.36"
)

TEXT_TYPES = ("text/plain", "text/markdown", "text/csv", "application/json", "application/xml",
              "text/xml", "application/x-yaml", "text/yaml", "application/javascript",
              "text/javascript", "text/css")
HTML_TYPES = ("text/html", "application/xhtml+xml")

# Tags whose content is never page text.
_SKIP = {"script", "style", "noscript", "template", "svg", "canvas", "iframe", "object",
         "select", "datalist"}
# Tags that start a new line.
_BLOCK = {"p", "div", "section", "article", "main", "header", "footer", "nav", "aside", "ul", "ol",
          "li", "table", "tr", "thead", "tbody", "blockquote", "pre", "figure", "figcaption",
          "form", "fieldset", "details", "summary", "dl", "dt", "dd", "address", "hr",
          "h1", "h2", "h3", "h4", "h5", "h6"}
_VOID = {"br", "hr", "img", "input", "meta", "link", "area", "base", "col", "embed", "source",
         "track", "wbr"}


# Stands in for a space inside <pre> while lines are tidied, so indentation survives.
_PRE_SPACE = "\x00"


class FetchError(Exception):
    """A page could not be read; the message is for the agent."""


def url_error(url: str) -> str:
    """Empty when ``url`` may be fetched, else a message."""
    if not isinstance(url, str) or not url.startswith(("http://", "https://")):
        return f"Invalid URL protocol. Only http:// and https:// are allowed. Got: {str(url)[:50]}"
    if not urllib.parse.urlparse(url).netloc:
        return f"Invalid URL: no host in {url[:50]}"
    return ""


def media_type(content_type: str) -> str:
    return (content_type or "").split(";")[0].strip().lower()


def charset_of(content_type: str, body: bytes) -> str:
    """Charset from the header, else from a <meta> in the first 2 KB, else UTF-8."""
    match = re.search(r"charset\s*=\s*[\"']?([A-Za-z0-9._-]+)", content_type or "", re.I)
    if not match:
        head = body[:2048].decode("ascii", "ignore")
        match = re.search(r"<meta[^>]+charset\s*=\s*[\"']?([A-Za-z0-9._-]+)", head, re.I)
    name = match.group(1) if match else "utf-8"
    try:
        "".encode(name)
    except LookupError:
        name = "utf-8"
    return name


def decode(body: bytes, content_type: str) -> str:
    return body.decode(charset_of(content_type, body), "replace")


class _TextParser(HTMLParser):
    def __init__(self, base_url: str = ""):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.parts: list[str] = []
        self.title_parts: list[str] = []
        self.links: list[dict] = []
        self.scripts = 0
        self._skip_depth = 0
        self._pre_depth = 0
        self._in_title = False
        self._in_head = False
        self._link: dict | None = None

    def _newline(self) -> None:
        if self.parts and not self.parts[-1].endswith("\n"):
            self.parts.append("\n")

    def handle_starttag(self, tag, attrs):
        if tag == "script":
            self.scripts += 1
        if tag in _SKIP:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if tag == "head":
            self._in_head = True
        elif tag == "title":
            self._in_title = True
        elif tag == "br":
            self.parts.append("\n")
        elif tag in _BLOCK:
            self._newline()
            if tag == "li":
                self.parts.append("- ")
            elif tag == "pre":
                self._pre_depth += 1
        elif tag in ("td", "th"):
            if self.parts and not self.parts[-1].endswith(("\n", " | ")):
                self.parts.append(" | ")
        elif tag == "a":
            href = dict(attrs).get("href") or ""
            self._link = {"href": href, "text": []}
        elif tag == "img":
            alt = (dict(attrs).get("alt") or "").strip()
            if alt and not self._in_head:
                self.parts.append(f"[image: {alt}]")

    def handle_startendtag(self, tag, attrs):
        if tag not in _SKIP:
            self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if tag in _SKIP:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if self._skip_depth:
            return
        if tag == "head":
            self._in_head = False
        elif tag == "title":
            self._in_title = False
        elif tag in _BLOCK:
            if tag == "pre":
                self._pre_depth = max(0, self._pre_depth - 1)
            self._newline()
        elif tag == "a" and self._link is not None:
            text = " ".join("".join(self._link["text"]).replace(_PRE_SPACE, " ").split())
            href = self._link["href"].strip()
            self._link = None
            if href and not href.startswith(("#", "javascript:", "mailto:", "tel:", "data:")):
                absolute = urllib.parse.urljoin(self.base_url, href)
                if absolute.startswith(("http://", "https://")):
                    self.links.append({"text": text, "url": absolute})

    def handle_data(self, data):
        if self._skip_depth:
            return
        if self._in_title:
            self.title_parts.append(data)
            return
        if self._in_head:
            return
        if self._pre_depth:
            # <pre> keeps its lines and its indentation (code samples).
            data = data.replace("\t", "    ").replace(" ", _PRE_SPACE)
        else:
            # A line break in the page source is only a space.
            data = re.sub(r"\s+", " ", data)
        if self._link is not None:
            self._link["text"].append(data)
        self.parts.append(data)


def html_to_text(html: str, base_url: str = "") -> dict:
    """``{"title", "text", "links", "scripts"}`` for one HTML document."""
    parser = _TextParser(base_url)
    try:
        parser.feed(html or "")
        parser.close()
    except Exception:  # a broken page still gives what was read so far
        pass
    lines = []
    for line in "".join(parser.parts).replace("\r", "").split("\n"):
        line = re.sub(r"[ \t ]+", " ", line).strip()
        line = re.sub(r"^(\| )+", "", line)
        lines.append(line)
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip().replace(_PRE_SPACE, " ")
    seen, links = set(), []
    for link in parser.links:
        if link["url"] not in seen:
            seen.add(link["url"])
            links.append(link)
    return {
        "title": " ".join("".join(parser.title_parts).split()),
        "text": text,
        "links": links[:MAX_LINKS],
        "scripts": parser.scripts,
    }


def needs_browser(text: str, scripts: int) -> bool:
    """True when the page is likely built by scripts: almost no text, and it loads some."""
    return scripts > 0 and len(text or "") < SCRIPT_PAGE_TEXT_CHARS


def browser_hint(url: str) -> str:
    return (
        "This page has almost no text without running its scripts. Read it with the browser: "
        f'browser goto "{url}"'
    )


def read_page(body: bytes, content_type: str, url: str) -> dict:
    """Turn a fetched reply into ``{"title", "text", "links", "hint"?}``. Raises FetchError."""
    kind = media_type(content_type)
    looks_html = body[:1024].lstrip().lower().startswith((b"<!doctype html", b"<html"))
    if kind in HTML_TYPES or (not kind and looks_html) or (kind == "text/plain" and looks_html):
        page = html_to_text(decode(body, content_type), url)
        out = {"title": page["title"], "text": page["text"], "links": page["links"]}
        if needs_browser(page["text"], page["scripts"]):
            out["hint"] = browser_hint(url)
        return out
    if kind in TEXT_TYPES or kind.startswith("text/") or kind.endswith(("+json", "+xml")):
        return {"title": "", "text": decode(body, content_type).strip(), "links": []}
    raise FetchError(
        f"This address returns {kind or 'an unknown type'}, not a page of text. "
        "`search read` reads HTML and plain text only."
    )


def fetch(url: str, user_agent: str = "", timeout: int = TIMEOUT_SECONDS,
          max_bytes: int = MAX_BYTES, opener=None) -> tuple[str, str, bytes]:
    """GET ``url``. Returns (final url, content type, body up to ``max_bytes``). Raises FetchError."""
    problem = url_error(url)
    if problem:
        raise FetchError(problem)
    request = urllib.request.Request(url, headers={
        "User-Agent": user_agent or DEFAULT_USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.5",
        "Accept-Language": "en",
    })
    open_url = opener or urllib.request.urlopen
    try:
        with open_url(request, timeout=timeout) as reply:
            final_url = reply.geturl() or url
            content_type = reply.headers.get("Content-Type", "") if reply.headers else ""
            body = reply.read(max_bytes + 1)[:max_bytes]
    except urllib.error.HTTPError as exc:
        raise FetchError(f"The site answered HTTP {exc.code} ({exc.reason}) for {url}") from exc
    except urllib.error.URLError as exc:
        raise FetchError(f"Could not reach {url}: {exc.reason}") from exc
    except (TimeoutError, OSError) as exc:
        raise FetchError(f"Could not read {url}: {exc}") from exc
    if url_error(final_url):
        raise FetchError(f"The page redirected to an address that is not allowed: {final_url[:80]}")
    return final_url, content_type, body
