"""Instruction sources: extract their text once, keep it as a dated snapshot, and locate quoted
excerpts in it.

A saved snapshot is evidence of what the source said when it was captured, not of what the live
portal says now. HTML is reduced to text with the standard-library parser: scripts, styles and
templates are dropped and nothing is executed or fetched.
"""

from __future__ import annotations

import unicodedata
from html.parser import HTMLParser

from . import pdfprobe, sniff

_QUOTES = {
    "\N{LEFT SINGLE QUOTATION MARK}": "'",
    "\N{RIGHT SINGLE QUOTATION MARK}": "'",
    "\N{SINGLE LOW-9 QUOTATION MARK}": "'",
    "\N{SINGLE HIGH-REVERSED-9 QUOTATION MARK}": "'",
    "\N{PRIME}": "'",
    "\N{LEFT DOUBLE QUOTATION MARK}": '"',
    "\N{RIGHT DOUBLE QUOTATION MARK}": '"',
    "\N{DOUBLE LOW-9 QUOTATION MARK}": '"',
    "\N{DOUBLE HIGH-REVERSED-9 QUOTATION MARK}": '"',
    "\N{DOUBLE PRIME}": '"',
    "\N{LEFT-POINTING DOUBLE ANGLE QUOTATION MARK}": '"',
    "\N{RIGHT-POINTING DOUBLE ANGLE QUOTATION MARK}": '"',
}
_DASHES = {
    "\N{HYPHEN}",
    "\N{NON-BREAKING HYPHEN}",
    "\N{FIGURE DASH}",
    "\N{EN DASH}",
    "\N{EM DASH}",
    "\N{HORIZONTAL BAR}",
    "\N{MINUS SIGN}",
    "\N{SMALL EM DASH}",
    "\N{SMALL HYPHEN-MINUS}",
    "\N{FULLWIDTH HYPHEN-MINUS}",
}
# Characters that change nothing visible; dropped before matching.
_INVISIBLE = {
    "\N{SOFT HYPHEN}",
    "\N{ZERO WIDTH SPACE}",
    "\N{ZERO WIDTH NON-JOINER}",
    "\N{ZERO WIDTH JOINER}",
    "\N{WORD JOINER}",
    "\N{ZERO WIDTH NO-BREAK SPACE}",
}

_LINE_BREAK_HYPHENS = {"-", "\N{HYPHEN}", "\N{SOFT HYPHEN}"}

MAX_SOURCE_CHARS = 2_000_000


class _TextExtractor(HTMLParser):
    _SKIP = {"script", "style", "noscript", "template", "head", "svg"}
    _BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section",
              "article", "header", "footer", "table", "ul", "ol", "dt", "dd", "label", "legend", "fieldset"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):  # noqa: ANN001
        tag = tag.lower()
        if tag in self._SKIP:
            self._skip_depth += 1
        elif tag in self._BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):  # noqa: ANN001
        tag = tag.lower()
        if tag in self._SKIP:
            self._skip_depth = max(0, self._skip_depth - 1)
        elif tag in self._BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):  # noqa: ANN001
        if not self._skip_depth:
            self.parts.append(data)


def _looks_like_html(name: str, text: str) -> bool:
    lowered = name.lower()
    if lowered.endswith((".html", ".htm", ".xhtml")):
        return True
    head = text.lstrip()[:200].lower()
    return head.startswith("<!doctype html") or head.startswith("<html")


def html_to_text(text: str) -> str:
    parser = _TextExtractor()
    parser.feed(text)
    parser.close()
    raw = "".join(parser.parts)
    lines = [" ".join(line.split()) for line in raw.split("\n")]
    out: list[str] = []
    for line in lines:
        if line or (out and out[-1]):
            out.append(line)
    return "\n".join(out).strip()


def extract(name: str, data: bytes | None) -> dict:
    """Return {status, text, pages, method, detail}. status: ok | no_text | unsupported | unreadable."""
    info = sniff.sniff(data)
    fmt = info["format"]
    if fmt == "text":
        text = sniff.decode_text(data or b"")
        if _looks_like_html(name, text):
            text = html_to_text(text)
            method = "HTML reduced to text (stdlib html.parser; scripts/styles dropped)"
        else:
            method = "UTF-8 text"
        return _finish(text, None, method)
    if fmt == "pdf":
        probe = pdfprobe.run_probe(data or b"", want_text=True)
        if probe.get("status") != "ok":
            return {"status": "unreadable", "text": "", "pages": None,
                    "method": probe.get("engine", "pypdf"), "detail": probe.get("detail", probe.get("status"))}
        texts = probe.get("texts") or []
        pages: list[list[int]] = []
        chunks: list[str] = []
        cursor = 0
        for index, page_text in enumerate(texts, start=1):
            page_text = page_text.replace("\r\n", "\n").replace("\r", "\n")
            if chunks:
                chunks.append("\n\n")
                cursor += 2
            pages.append([index, cursor, cursor + len(page_text)])
            chunks.append(page_text)
            cursor += len(page_text)
        method = f"PDF text layer ({probe.get('engine', 'pypdf')} extract_text)"
        return _finish("".join(chunks), pages, method)
    if fmt == "empty":
        return {"status": "no_text", "text": "", "pages": None, "method": None, "detail": "The file is empty."}
    return {"status": "unsupported", "text": "", "pages": None, "method": None,
            "detail": f"{sniff.LABELS.get(fmt, fmt)} sources are not supported; paste the text instead."}


def _finish(text: str, pages, method: str) -> dict:
    truncated = len(text) > MAX_SOURCE_CHARS
    text = text[:MAX_SOURCE_CHARS]
    if not text.strip():
        return {"status": "no_text", "text": "", "pages": pages, "method": method,
                "detail": "No extractable text (a scanned document has no text layer)."}
    detail = "Text truncated to 2,000,000 characters." if truncated else ""
    return {"status": "ok", "text": text, "pages": pages, "method": method, "detail": detail}


def _normalize_with_map(text: str, *, fold_case: bool) -> tuple[str, list[int]]:
    """Normalised text plus, for each normalised character, the index of its source character.

    Normalisation: NFKC per character, curly quotes -> straight, dash variants -> '-',
    invisible characters dropped, line-break hyphenation joined, whitespace runs -> one space.
    """
    out: list[str] = []
    index_map: list[int] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        # "appli-\ncation" -> "application" when a hyphen ends a line between letters.
        if ch in _LINE_BREAK_HYPHENS and i + 1 < n and text[i + 1] == "\n" and out and out[-1].isalpha():
            j = i + 1
            while j < n and text[j].isspace():
                j += 1
            if j < n and text[j].isalpha() and text[j].islower():
                i = j
                continue
        if ch in _INVISIBLE:
            i += 1
            continue
        if ch.isspace():
            if out and out[-1] != " ":
                out.append(" ")
                index_map.append(i)
            i += 1
            continue
        if ch in _QUOTES:
            piece = _QUOTES[ch]
        elif ch in _DASHES:
            piece = "-"
        else:
            piece = unicodedata.normalize("NFKC", ch)
        if fold_case:
            piece = piece.casefold()
        for p in piece:
            out.append(p)
            index_map.append(i)
        i += 1
    while out and out[-1] == " ":
        out.pop()
        index_map.pop()
    start = 0
    while start < len(out) and out[start] == " ":
        start += 1
    return "".join(out[start:]), index_map[start:]


def locate(text: str, excerpt: str, pages=None) -> dict:
    """Find ``excerpt`` in ``text``. Returns {match, start, end, page} with match in
    exact | normalized | normalized_casefold | none. Offsets refer to the original text."""
    excerpt = (excerpt or "").strip()
    if len(excerpt) < 3:
        return {"match": "none", "reason": "Excerpt is too short to locate (need at least 3 characters)."}
    position = text.find(excerpt)
    if position >= 0:
        return _with_page({"match": "exact", "start": position, "end": position + len(excerpt)}, pages)
    for fold, label in ((False, "normalized"), (True, "normalized_casefold")):
        norm_text, index_map = _normalize_with_map(text, fold_case=fold)
        norm_excerpt, _ = _normalize_with_map(excerpt, fold_case=fold)
        if not norm_excerpt:
            break
        found = norm_text.find(norm_excerpt)
        if found >= 0:
            start = index_map[found]
            end = index_map[found + len(norm_excerpt) - 1] + 1
            return _with_page({"match": label, "start": start, "end": end}, pages)
    return {"match": "none", "reason": "The excerpt does not occur in the saved source text."}


def _with_page(result: dict, pages) -> dict:
    if pages:
        for page, start, end in pages:
            if start <= result["start"] <= end:
                result["page"] = page
                break
    return result
