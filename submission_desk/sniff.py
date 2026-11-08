"""Identify file formats from their bytes, never from the file name.

Only signatures we can check reliably are recognised. Anything else is ``unknown``; a rule that
requires a format we did not detect fails or stays unknown, it never passes.
"""

from __future__ import annotations

import codecs
import io
import struct
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

# Magic numbers, written as hex so they read like the format specifications.
PNG_SIGNATURE = bytes.fromhex("89504e470d0a1a0a")
JPEG_SIGNATURE = bytes.fromhex("ffd8ff")
ZIP_SIGNATURE = bytes.fromhex("504b0304")
UTF8_BOM = bytes.fromhex("efbbbf")


def sniff(data: bytes | None, *, size: int | None = None) -> dict:
    if data is None:
        return {"format": "unknown", "detail": "Content not read (file too large)."}
    if len(data) == 0:
        return {"format": "empty", "detail": "The file is empty (0 bytes)."}
    head = data[:1024]
    if head.startswith(b"%PDF-"):
        version = head[5:8].decode("ascii", "replace")
        return {"format": "pdf", "detail": f"PDF header at byte 0 (version {version})."}
    if b"%PDF-" in head and b"%%EOF" in data[-1024:]:
        # Readers tolerate a PDF header anywhere in the first 1024 bytes, but only a file that also ends
        # like a PDF is one: text that merely mentions "%PDF-" is not.
        offset = head.index(b"%PDF-")
        version = head[offset + 5:offset + 8].decode("ascii", "replace")
        return {"format": "pdf", "detail": f"PDF header at byte {offset} (version {version}), after {offset} bytes of other "
                                          "data; some portals reject files that do not start with the header."}
    if data.startswith(PNG_SIGNATURE):
        return {"format": "png", "detail": "PNG signature."}
    if data.startswith(JPEG_SIGNATURE):
        return {"format": "jpeg", "detail": "JPEG signature."}
    if data.startswith((b"GIF87a", b"GIF89a")):
        return {"format": "gif", "detail": "GIF signature."}
    if data.startswith(ZIP_SIGNATURE):
        return _sniff_zip(data)
    return _sniff_text(data)


def _zip_entry_count(data: bytes) -> int | None:
    """Entry count from the archive's end-of-central-directory record, read without parsing entries."""
    tail_start = max(0, len(data) - (22 + 65535))
    at = data.rfind(b"PK\x05\x06", tail_start)
    if at < 0 or at + 22 > len(data):
        return None
    count = struct.unpack_from("<H", data, at + 10)[0]
    if count == 0xFFFF and at >= 20 and data[at - 20:at - 16] == b"PK\x06\x07":  # ZIP64 locator
        record = struct.unpack_from("<Q", data, at - 12)[0]
        if data[record:record + 4] == b"PK\x06\x06" and record + 40 <= len(data):
            count = struct.unpack_from("<Q", data, record + 32)[0]
    return count


def _sniff_zip(data: bytes) -> dict:
    # zipfile builds an object for every entry as soon as the archive is opened, so an archive with
    # very many entries is reported as a plain ZIP without opening it.
    count = _zip_entry_count(data)
    if count is not None and count > _ZIP_NAME_LIMIT:
        return {"format": "zip", "detail": f"ZIP archive with {count} entries; too many to inspect, so it is not "
                                           "recognised as an Office or OpenDocument file."}
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


_TEXT_CHUNK = 1 << 20


def _sniff_text(data: bytes) -> dict:
    if b"\x00" in data[:65536]:
        return {"format": "unknown", "detail": "Binary content (NUL bytes); not a recognised format."}
    # Validate in chunks: decoding a large file whole would hold up to four times its size in memory.
    decoder = codecs.getincrementaldecoder("utf-8-sig")()
    text = ""
    view = memoryview(data)
    for start in range(0, len(data), _TEXT_CHUNK):
        try:
            piece = decoder.decode(view[start:start + _TEXT_CHUNK], final=start + _TEXT_CHUNK >= len(data))
        except UnicodeDecodeError as exc:
            return {"format": "unknown", "detail": f"Not valid UTF-8 text (invalid byte at offset {start + exc.start})."}
        if len(text) < 65536:
            text += piece[:65536 - len(text)]
    controls = sum(1 for ch in text[:65536] if ord(ch) < 32 and ch not in "\t\n\r\f")
    if controls > 0.01 * max(1, min(len(text), 65536)):
        return {"format": "unknown", "detail": "Mostly control characters; not treated as text."}
    bom = " with BOM" if data.startswith(UTF8_BOM) else ""
    return {"format": "text", "detail": f"Valid UTF-8 text{bom}."}


def decode_text(data: bytes, *, max_chars: int | None = None) -> str:
    """Decode bytes already sniffed as text. Newlines normalised to \\n.

    With ``max_chars``, only enough of the start is decoded to yield more than that many characters,
    so a caller that keeps a prefix never holds the whole file as text.
    """
    if max_chars is not None and len(data) > 4 * (max_chars + 1):
        data = data[:4 * (max_chars + 1)]
        text = codecs.getincrementaldecoder("utf-8-sig")(errors="ignore").decode(data, final=False)
    else:
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
