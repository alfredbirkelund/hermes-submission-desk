"""Hostile or unusual input stays bounded and is read as what it is (independent review, round 1)."""

from __future__ import annotations

import io
import sys
import tracemalloc
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from submission_desk import sniff, textsrc  # noqa: E402

MB = 1_000_000


def _peak(fn):
    tracemalloc.start()
    try:
        out = fn()
        return out, tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()


def test_an_archive_with_very_many_entries_is_not_expanded_in_memory():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as archive:
        for i in range(60_000):
            archive.writestr(f"f{i}.txt", b"")
    data = buf.getvalue()
    out, peak = _peak(lambda: sniff.sniff(data))
    assert out["format"] == "zip" and "60000 entries" in out["detail"], out
    assert peak < 4 * MB, f"sniffing a 60,000-entry archive allocated {peak / MB:.1f} MB"


def test_an_ordinary_office_file_is_still_recognised():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", "<w:document/>")
    assert sniff.sniff(buf.getvalue())["format"] == "docx"


def test_a_huge_html_page_is_reduced_to_text_within_a_bounded_amount_of_memory():
    page = ("<html><body>" + "<p>word " * 1_500_000 + "</body></html>").encode()  # 12 MB of markup
    out, peak = _peak(lambda: textsrc.extract("page.html", page))
    assert out["status"] == "ok" and out["detail"].startswith("Text truncated"), out["detail"]
    assert textsrc.MAX_SOURCE_CHARS * 0.9 < len(out["text"]) <= textsrc.MAX_SOURCE_CHARS
    assert peak < 60 * MB, f"extracting 12 MB of HTML allocated {peak / MB:.1f} MB"


def test_a_large_text_file_is_validated_without_decoding_it_whole():
    data = ("\u00e9" * 6_000_000).encode()  # 12 MB of two-byte characters
    out, peak = _peak(lambda: sniff.sniff(data))
    assert out["format"] == "text", out
    assert peak < 6 * MB, f"sniffing 12 MB of text allocated {peak / MB:.1f} MB"
    broken = data[:5_000_001] + b"\xff" + data[5_000_001:]
    assert sniff.sniff(broken)["format"] == "unknown"


def test_file_names_with_edge_spaces_or_leading_dots_are_addressed_exactly(host, material):
    (material / " spaced.txt ").write_text("Spaced file.\n", encoding="utf-8")
    (material / "..data").mkdir()
    (material / "..data" / "x.txt").write_text("In a folder whose name starts with two dots.\n", encoding="utf-8")
    ws = host.api("POST", "/workspaces", {"title": "Names", "material_root": str(material)})["body"]["workspace"]["id"]
    out = host.run([
        {"op": "api", "method": "POST", "path": f"/workspaces/{ws}/documents", "json": {"path": " spaced.txt "}},
        {"op": "api", "method": "POST", "path": f"/workspaces/{ws}/documents", "json": {"path": "..data/x.txt"}},
        {"op": "api", "method": "POST", "path": f"/workspaces/{ws}/documents", "json": {"path": "../outside.txt"}},
        {"op": "api", "method": "GET", "path": f"/workspaces/{ws}"},
    ])
    assert out[0]["status"] == 200 and out[1]["status"] == 200, (out[0], out[1])
    assert out[2]["status"] == 422 and "outside the material folder" in out[2]["body"]["message"]
    docs = {d["rel_path"]: d for d in out[3]["body"]["documents"]}
    assert docs[" spaced.txt "]["status"] == "ok" and docs["..data/x.txt"]["status"] == "ok", docs


def test_quotes_are_located_in_a_saved_revision_once_not_on_every_read(tmp_path, monkeypatch, material):
    """Readiness is read on every page poll; locating quotes in a long changed source must not repeat."""
    from submission_desk import actors, service, store

    monkeypatch.setenv("SUBMISSION_DESK_DB", str(tmp_path / "db" / "data.db"))
    quote = "The research statement must be a PDF."
    (material / "call.txt").write_text(quote + "\n", encoding="utf-8")
    desk = service.Desk(store.Store())
    me = actors.human("desktop")
    ws = desk.create_workspace(me, "Cache", str(material))["workspace"]["id"]
    desk.add_source_file(me, ws, "call.txt")
    desk.create_requirement(me, ws, {"statement": "Statement as PDF", "kind": "other", "source": "S1", "excerpt": quote})
    (material / "call.txt").write_text("Updated call.\n" + "filler text. " * 50_000 + quote + "\n", encoding="utf-8")
    desk.recheck(me, ws)  # saves revision 2, where the quote still occurs

    calls = []
    real = textsrc.locate
    monkeypatch.setattr(textsrc, "locate", lambda *a, **k: calls.append(1) or real(*a, **k))
    for _ in range(3):
        state = desk.readiness(ws)
    assert not [r for r in state["items"][0]["reasons"] if r["code"].startswith("source")], state["items"][0]["reasons"]
    assert len(calls) <= 1, f"the quote was located {len(calls)} times for one saved revision"

