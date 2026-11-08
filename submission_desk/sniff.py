"""Identify file formats from their bytes, never from the file name.

Only signatures we can check reliably are recognised. Anything else is ``unknown``; a rule that
requires a format we did not detect fails or stays unknown, it never passes.
"""

from __future__ import annotations

import io
import zipfile

# Rule spelling -> detected format
FORMAT_ALIASES = {
    "pdf": "pdf",
    "png": "png",
    "jpg": "jpeg",
    "jpeg": "jpeg",
    "gif": "gif",
    "docx": "docx",
    "xlsx": "xlsx",
    "pptx": "pptx",
    "odt": "odt",
    "zip": "zip",
    "txt": "text",
    "text": "text",
    "plain text": "text",
    "md": "text",
    "markdown": "text",
}

LABELS = {
    "pdf": "PDF",
    "png": "PNG image",
    "jpeg": "JPEG image",
    "gif": "GIF image",
    "docx": "Word document (.docx)",
    "xlsx": "Excel workbook (.xlsx)",
    "pptx": "PowerPoint (.pptx)",
    "odt": "OpenDocument text (.odt)",
    "zip": "ZIP archive",
    "text": "UTF-8 plain text",
    "empty": "empty file",
    "unknown": "unrecognised format",
}

_ZIP_NAME_LIMIT = 5000


def sniff(data: bytes | None, *, size: int | None = None) -> dict:
    if data is None:
        return {"format": "unknown", "detail": "Content not read (file too large)."}
    if len(data) == 0:
        return {"format": "empty", "detail": "The file is empty (0 bytes)."}
    head = data[:1024]
    if b"%PDF-" in head:
        offset = head.index(b"%PDF-")
        version = head[offset + 5:offset + 8].decode("ascii", "replace")
        return {"format": "pdf", "detail": f"PDF header at byte {offset} (version {version})."}
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return {"format": "png", "detail": "PNG signature."}
    if data.startswith(b"\xff\xd8\xff"):
        return {"format": "jpeg", "detail": "JPEG signature."}
    if data.startswith((b"GIF87a", b"GIF89a")):
        return {"format": "gif", "detail": "GIF signature."}
    if data.startswith(b"PK\x03\x04"):
        return _sniff_zip(data)
    return _sniff_text(data)


def _sniff_zip(data: bytes) -> dict:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            infos = archive.infolist()[:_ZIP_NAME_LIMIT]
            names = {info.filename for info in infos}
            mimetype = ""
            if "mimetype" in names:
                info = archive.getinfo("mimetype")
                if info.file_size <= 200:
                    mimetype = archive.read("mimetype").decode("ascii", "replace").strip()
    except (zipfile.BadZipFile, OSError, KeyError, RuntimeError, NotImplementedError) as exc:
        return {"format": "unknown", "detail": f"ZIP signature but unreadable archive ({type(exc).__name__})."}
    if "[Content_Types].xml" in names:
        if any(n.startswith("word/") for n in names):
            return {"format": "docx", "detail": "Office Open XML package with word/ part."}
        if any(n.startswith("xl/") for n in names):
            return {"format": "xlsx", "detail": "Office Open XML package with xl/ part."}
        if any(n.startswith("ppt/") for n in names):
            return {"format": "pptx", "detail": "Office Open XML package with ppt/ part."}
    if mimetype == "application/vnd.oasis.opendocument.text":
        return {"format": "odt", "detail": "OpenDocument text package."}
    return {"format": "zip", "detail": "ZIP archive."}


def _sniff_text(data: bytes) -> dict:
    if b"\x00" in data[:65536]:
        return {"format": "unknown", "detail": "Binary content (NUL bytes); not a recognised format."}
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        return {"format": "unknown", "detail": f"Not valid UTF-8 text (invalid byte at offset {exc.start})."}
    controls = sum(1 for ch in text[:65536] if ord(ch) < 32 and ch not in "\t\n\r\f")
    if controls > 0.01 * max(1, min(len(text), 65536)):
        return {"format": "unknown", "detail": "Mostly control characters; not treated as text."}
    bom = " with BOM" if data.startswith(b"\xef\xbb\xbf") else ""
    return {"format": "text", "detail": f"Valid UTF-8 text{bom}."}


def decode_text(data: bytes) -> str:
    """Decode bytes already sniffed as text. Newlines normalised to \\n."""
    text = data.decode("utf-8-sig")
    return text.replace("\r\n", "\n").replace("\r", "\n")


def normalize_formats(values: list[str]) -> tuple[list[str], list[str]]:
    """Map rule spellings to detected formats. Returns (formats, unrecognised spellings)."""
    out: list[str] = []
    bad: list[str] = []
    for value in values:
        key = str(value).strip().lower().lstrip(".")
        fmt = FORMAT_ALIASES.get(key)
        if fmt is None:
            bad.append(str(value))
        elif fmt not in out:
            out.append(fmt)
    return out, bad
