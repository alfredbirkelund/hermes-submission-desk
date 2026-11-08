"""Real PDF fixtures written with reportlab (a real PDF producer) and pypdf (encryption).

Streams are left uncompressed so a test can change visible text in place without changing the
file size, which proves that revision tracking does not depend on size or modification time.
"""

from __future__ import annotations

import io
from pathlib import Path


def text_pdf(path: Path, pages: list[str], *, compress: bool = False) -> Path:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4, pageCompression=1 if compress else 0, invariant=1)
    for body in pages:
        y = 800
        for line in body.split("\n"):
            pdf.drawString(72, y, line)
            y -= 16
        pdf.showPage()
    pdf.save()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(buffer.getvalue())
    return path


def encrypted_pdf(path: Path, source: Path, *, user_password: str = "secret", owner_password: str = "owner") -> Path:
    from pypdf import PdfReader, PdfWriter

    writer = PdfWriter(clone_from=PdfReader(str(source)))
    writer.encrypt(user_password=user_password, owner_password=owner_password, algorithm="AES-128")
    with open(path, "wb") as handle:
        writer.write(handle)
    return path


def empty_tree_pdf(path: Path) -> Path:
    """A structurally valid PDF with an empty page tree (/Count 0)."""
    from pypdf import PdfWriter

    with open(path, "wb") as handle:
        PdfWriter().write(handle)
    return path


def image_only_pdf(path: Path, text: str = "Scanned page") -> Path:
    """A 'scanned' PDF: the page is a raster image of text, with no text layer."""
    from PIL import Image, ImageDraw
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    image = Image.new("RGB", (800, 200), "white")
    ImageDraw.Draw(image).text((20, 80), text, fill="black")
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4, invariant=1)
    pdf.drawImage(ImageReader(image), 72, 600, width=400, height=100)
    pdf.showPage()
    pdf.save()
    path.write_bytes(buffer.getvalue())
    return path


def replace_same_size(path: Path, old: bytes, new: bytes) -> None:
    """Edit bytes in place keeping size and modification time identical."""
    assert len(old) == len(new)
    st = path.stat()
    data = path.read_bytes()
    assert data.count(old) >= 1, f"{old!r} not in {path.name}"
    path.write_bytes(data.replace(old, new, 1))
    import os

    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))
    after = path.stat()
    assert (after.st_size, after.st_mtime_ns) == (st.st_size, st.st_mtime_ns)
