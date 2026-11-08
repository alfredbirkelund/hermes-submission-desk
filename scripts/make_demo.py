#!/usr/bin/env python3
"""Build the synthetic demo application used in the README walkthrough and the demo video.

Everything here is invented: the "Nordic Example Fellowship 2027" does not exist. The script is
deterministic, so the demo can be reproduced. Usage:

    .test-artifacts/venv/bin/python scripts/make_demo.py /path/to/empty/folder
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

CALL_HTML = """<!doctype html>
<html><head><title>Nordic Example Fellowship 2027: call for applications</title>
<script>/* tracking would run here; Submission Desk drops scripts */</script></head>
<body>
<h1>Nordic Example Fellowship 2027 (synthetic example)</h1>
<p>This call is a fictional fixture for testing Submission Desk.</p>
<h2>1. Required documents</h2>
<ul>
<li>A research statement of no more than 2 pages, uploaded as a PDF.</li>
<li>A curriculum vitae of at most 2 pages, as a PDF.</li>
<li>A budget of 1 page, as a PDF.</li>
<li>A letter of support signed by your supervisor, as a PDF.</li>
<li>An official transcript of records, as a PDF of at most 5 MB.</li>
</ul>
<h2>2. Project abstract</h2>
<p>Paste a project abstract of at most 200 words into the portal.</p>
<h2>3. Deadline</h2>
<p>Applications must be submitted by 15 March 2027 at 14:00 Copenhagen time (CET).</p>
</body></html>
"""

PORTAL_TEXT = """Portal: Upload documents (copied from the application portal on 2026-11-08)

Research statement (PDF, max 2 pages) *
Curriculum vitae (PDF) *
Budget (PDF) *
Letter of support *
Transcript of records *
Project abstract (max 200 words) *
"""

FAQ_TEXT = """Applicant FAQ (2026 round, older guidance)

Q: How long may the research statement be?
A: The research statement may be up to 3 pages including references.
"""

ABSTRACT = (
    "Coastal wetlands store large amounts of carbon, yet their response to repeated storm surges is poorly "
    "measured. This project combines ten years of satellite imagery with field cores from three Danish "
    "estuaries to estimate how quickly buried carbon is released after flooding. We will build an open "
    "dataset of core measurements, calibrate a simple release model against it, and test the model on two "
    "estuaries that were not used for calibration. The work runs for two years. In the first year we collect "
    "and date the cores and align them with the imagery. In the second year we fit the model, publish the "
    "dataset with documentation, and write two papers. The results will help regional planners decide where "
    "restoration protects the most carbon per krone spent, and they will give other researchers a tested "
    "method they can apply to wetlands elsewhere in the Baltic region. All code and data will be released "
    "under open licences at the end of the project, together with a short guide for practitioners."
)


def pdf(path: Path, pages: list[str]) -> None:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    buffer = io.BytesIO()
    doc = canvas.Canvas(buffer, pagesize=A4, pageCompression=0, invariant=1)
    for body in pages:
        y = 790
        for line in body.split("\n"):
            doc.setFont("Helvetica", 12)
            doc.drawString(72, y, line)
            y -= 18
        doc.showPage()
    doc.save()
    path.write_bytes(buffer.getvalue())


def main(target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    (target / "instructions").mkdir(exist_ok=True)
    (target / "instructions" / "call-for-applications.html").write_text(CALL_HTML, encoding="utf-8")
    (target / "instructions" / "portal-upload-page.txt").write_text(PORTAL_TEXT, encoding="utf-8")
    (target / "instructions" / "faq-2026.txt").write_text(FAQ_TEXT, encoding="utf-8")
    pdf(target / "research-statement.pdf", ["Research statement (synthetic)", "Version A. Page two."])
    pdf(target / "cv.pdf", ["Curriculum vitae (synthetic)", "Experience", "Publications"])
    pdf(target / "budget.pdf", ["Budget (synthetic)"])
    pdf(target / "letter-of-support.pdf", ["Letter of support (draft, not yet signed)"])
    (target / "abstract.txt").write_text(ABSTRACT + "\n", encoding="utf-8")
    print(f"Synthetic demo application written to {target}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    main(Path(sys.argv[1]).expanduser().resolve())
