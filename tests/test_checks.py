"""Document-quality gates against an independent expected-results fixture.

Requirements are entered by hand through the Desktop backend (manual entry must work without a
model), mapped to real files, checked through the agent tool and read back through the REST
router. Page counts are cross-checked with poppler's pdfinfo when it is installed.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from helpers import item
from pdfs import empty_tree_pdf, encrypted_pdf, image_only_pdf, text_pdf

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "expected_results.json").read_text(encoding="utf-8"))
WORD_TEXT = FIXTURE["word_fixture"]["text_template"].format(
    EM_DASH="\N{EM DASH}", BULLET="\N{BULLET}", NBSP="\N{NO-BREAK SPACE}", ZWSP="\N{ZERO WIDTH SPACE}")


def _material(root: Path) -> None:
    text_pdf(root / "two-pages.pdf", ["Research statement. Version A.", "Second page of the statement."])
    text_pdf(root / "three pages ü.pdf", ["One", "Two", "Three"])
    text_pdf(root / "plain.pdf", ["Single page"])
    encrypted_pdf(root / "encrypted.pdf", root / "plain.pdf")
    encrypted_pdf(root / "owner-only.pdf", root / "plain.pdf", user_password="")
    good = (root / "two-pages.pdf").read_bytes()
    (root / "corrupt.pdf").write_bytes(good[:400])
    image_only_pdf(root / "scan.pdf")
    empty_tree_pdf(root / "no-pages.pdf")
    png = bytes.fromhex("89504e470d0a1a0a") + bytes(64)  # PNG signature, then padding
    (root / "photo.pdf").write_bytes(png)
    (root / "empty.pdf").write_bytes(b"")
    (root / "essay.txt").write_text(WORD_TEXT, encoding="utf-8")
    (root / "essay-zh.txt").write_text("我们的研究计划包括三个部分。" * 20, encoding="utf-8")
    (root / "exact-1000.txt").write_bytes(b"a" * 1000)
    (root / "exact-1001.txt").write_bytes(b"a" * 1001)
    (root / "between.bin").write_bytes(b"\x00" * 1048000)
    (root / "blob.bin").write_bytes(os.urandom(4096).replace(b"%PDF", b"%PDX"))


def test_checks_match_the_independent_fixture(host, material):
    _material(material)
    created = host.api("POST", "/workspaces", {"title": "Quality gates", "material_root": str(material)})
    ws = created["body"]["workspace"]["id"]
    ops = []
    for case in FIXTURE["cases"]:
        ops.append({"op": "api", "method": "POST", "path": f"/workspaces/{ws}/requirements",
                    "json": {"statement": f"Case {case['code']}", "kind": "document", "rules": case["rules"]}})
        ops.append({"op": "api", "method": "POST", "path": f"/workspaces/{ws}/fields", "json": {"label": f"Field for {case['code']}"}})
    for index, case in enumerate(FIXTURE["cases"], start=1):
        ops.append({"op": "api", "method": "POST", "path": f"/workspaces/{ws}/mappings",
                    "json": {"requirement": case["code"], "field": f"F{index}", "document": case["file"]}})
    ops.append({"op": "tool", "name": "submission_desk_check", "args": {"workspace": ws}})
    ops.append({"op": "api", "method": "GET", "path": f"/workspaces/{ws}"})
    results = host.run(ops)
    for res in results[:-2]:
        assert res["status"] == 200, res
    state = results[-1]["body"]
    assert state["fresh"] is False  # a plain read after the check is a labelled snapshot
    mismatches = []
    for case in FIXTURE["cases"]:
        row = item(state, case["code"])
        assert row["requirement"]["status"] == "confirmed" and row.get("no_source_evidence") is True
        mapping = row["mappings"][0]
        checks = {c["check"]: c for c in mapping["checks"]}
        for check, expected in case["expect"].items():
            got = checks.get(check, {}).get("status")
            if got != expected:
                mismatches.append(f"{case['code']} {case['file']} {check}: expected {expected}, got {got} ({checks.get(check, {}).get('detail')})")
        if "pages" in case and "pages" in checks:
            assert checks["pages"]["measurement"] == case["pages"], case
        if "bytes" in case:
            assert checks["size"]["measurement"] == case["bytes"] == (material / case["file"]).stat().st_size
        if "words" in case and checks.get("words", {}).get("status") in ("pass", "fail") and not case.get("estimate"):
            assert list(checks["words"]["measurement"].values()) == [case["words"]], checks["words"]
        if case.get("estimate"):
            assert checks["words"]["estimate"] is True, checks["words"]
        for result in mapping["checks"]:  # every quantitative result explains itself
            assert result["method"] or result["status"] == "unknown", result
            if result["status"] in ("pass", "fail") and result["check"] in ("size", "pages", "words"):
                assert result["measurement"] is not None and result["limit"] is not None and result["units"]
        # Never green with an unknown or failed check hidden underneath.
        statuses = {c["status"] for c in mapping["checks"]}
        if statuses & {"unknown", "fail"}:
            assert row["state"] != "ready"
    assert not mismatches, "\n".join(mismatches)
    r13 = {c["check"]: c for c in item(state, "R13")["mappings"][0]["checks"]}["words"]
    assert r13["measurement"] == {"whitespace_tokens": 19, "letters_digits_tokens": 15}
    r21 = item(state, "R21")
    assert r21["state"] == "missing" and any(r["code"] == "file_missing" for r in r21["reasons"])
    r8 = {c["check"]: c for c in item(state, "R8")["mappings"][0]["checks"]}["format"]
    assert "content is PNG image" in r8["detail"]
    r6 = {c["check"]: c for c in item(state, "R6")["mappings"][0]["checks"]}["pages"]
    assert "not the same as 0" in r6["detail"] and r6["measurement"] is None


@pytest.mark.skipif(shutil.which("pdfinfo") is None, reason="poppler pdfinfo not installed")
def test_page_counts_agree_with_pdfinfo(tmp_path):
    _material(tmp_path)
    for case in FIXTURE["cases"]:
        if "pages" not in case:
            continue
        out = subprocess.run(["pdfinfo", str(tmp_path / case["file"])], capture_output=True, text=True, check=True).stdout
        pages = int(next(line.split(":", 1)[1] for line in out.splitlines() if line.startswith("Pages:")))
        assert pages == case["pages"], case["file"]


def test_a_missing_pdf_engine_is_unknown_with_a_concrete_fix(tmp_path, monkeypatch, material):
    """Without pypdf the page check stays unknown and tells the applicant how to fix it or verify by hand."""
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from submission_desk import actors, service, store

    monkeypatch.setenv("SUBMISSION_DESK_DB", str(tmp_path / "db" / "data.db"))
    text_pdf(material / "statement.pdf", ["One"])
    missing = {"status": "engine_unavailable", "detail": "pypdf is not installed (ModuleNotFoundError)."}
    desk = service.Desk(store.Store(), probe=lambda data, *, want_text: missing)
    me = actors.human("desktop")
    ws = desk.create_workspace(me, "No engine", str(material))["workspace"]["id"]
    desk.create_requirement(me, ws, {"statement": "Statement, max 2 pages", "kind": "document", "rules": {"formats": ["pdf"], "max_pages": 2}})
    desk.set_mapping(me, ws, requirement="R1", field=None, document="statement.pdf")
    row = item(desk.recheck(me, ws), "R1")
    pages = next(c for c in row["mappings"][0]["checks"] if c["check"] == "pages")
    assert pages["status"] == "unknown" and pages["cause"] == "engine_unavailable", pages
    assert pages["detail"] == "not counted, because the PDF engine (pypdf) is missing from this Hermes environment."
    reason = next(r for r in row["reasons"] if r["code"] == "check_pages")
    assert "hermes pm repair" in reason["next"] and "Verify pages by hand" in reason["next"], reason


def test_an_unknown_caused_by_the_environment_is_rechecked_once_the_engine_works(tmp_path, monkeypatch, material):
    """A missing engine says nothing about the file, so its result must not be cached for that revision."""
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from submission_desk import actors, pdfprobe, service, store

    monkeypatch.setenv("SUBMISSION_DESK_DB", str(tmp_path / "db" / "data.db"))
    text_pdf(material / "statement.pdf", ["One", "Two", "Three"])
    engine = {"ok": False}

    def probe(data, *, want_text):
        if not engine["ok"]:
            return {"status": "engine_unavailable", "detail": "pypdf is not installed (ModuleNotFoundError)."}
        return pdfprobe.run_probe(data, want_text=want_text)

    desk = service.Desk(store.Store(), probe=probe)
    me = actors.human("desktop")
    ws = desk.create_workspace(me, "Engine", str(material))["workspace"]["id"]
    desk.create_requirement(me, ws, {"statement": "Statement, max 2 pages", "kind": "document", "rules": {"formats": ["pdf"], "max_pages": 2}})
    desk.set_mapping(me, ws, requirement="R1", field=None, document="statement.pdf")

    def pages():
        state = desk.recheck(me, ws)
        return next(c for c in item(state, "R1")["mappings"][0]["checks"] if c["check"] == "pages")

    assert pages()["status"] == "unknown"
    engine["ok"] = True  # e.g. after `hermes pm repair` and a restart
    after = pages()
    assert after["status"] == "fail" and after["measurement"] == 3, after


def test_the_cause_of_an_unknown_survives_storage_and_old_ledgers_upgrade(tmp_path, monkeypatch, material):
    """Readiness reads stored results; the fix it suggests must not depend on the result still being in memory."""
    import sqlite3
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from submission_desk import actors, service, store

    db = tmp_path / "db" / "data.db"
    db.parent.mkdir()
    old = sqlite3.connect(db)  # a ledger written by schema 1, before results carried a cause
    old.executescript("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);")
    old.executescript((Path(__file__).resolve().parent / "fixtures" / "ledger-schema-1.sql").read_text(encoding="utf-8"))
    old.execute("INSERT INTO meta(key, value) VALUES('schema_version', '1')")
    old.commit()
    old.close()

    monkeypatch.setenv("SUBMISSION_DESK_DB", str(db))
    text_pdf(material / "statement.pdf", ["One"])
    desk = service.Desk(store.Store(), probe=lambda data, *, want_text: {"status": "engine_unavailable", "detail": "x"})
    me = actors.human("desktop")
    ws = desk.create_workspace(me, "Cause", str(material))["workspace"]["id"]
    desk.create_requirement(me, ws, {"statement": "Statement, max 2 pages", "kind": "document", "rules": {"formats": ["pdf"], "max_pages": 2}})
    desk.set_mapping(me, ws, requirement="R1", field=None, document="statement.pdf")
    desk.recheck(me, ws)
    state = desk.readiness(ws)  # read back from storage, not the in-memory result
    reason = next(r for r in item(state, "R1")["reasons"] if r.get("check") == "pages" or "Pages" in r["message"])
    assert "hermes pm repair" in reason["next"], reason
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()[0] == str(store.SCHEMA_VERSION)


def test_a_recheck_after_the_engine_returns_keeps_one_result_per_check(tmp_path, monkeypatch, material):
    """Replacing an environment-dependent result must not write the final results of that revision again."""
    import sqlite3
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from submission_desk import actors, pdfprobe, service, store

    db = tmp_path / "db" / "data.db"
    monkeypatch.setenv("SUBMISSION_DESK_DB", str(db))
    text_pdf(material / "statement.pdf", ["One", "Two", "Three"])
    engine = {"ok": False}

    def probe(data, *, want_text):
        if not engine["ok"]:
            return {"status": "engine_unavailable", "detail": "pypdf is not installed (ModuleNotFoundError)."}
        return pdfprobe.run_probe(data, want_text=want_text)

    desk = service.Desk(store.Store(), probe=probe)
    me = actors.human("desktop")
    ws = desk.create_workspace(me, "Engine", str(material))["workspace"]["id"]
    desk.create_requirement(me, ws, {"statement": "Statement, max 2 pages", "kind": "document", "rules": {"formats": ["pdf"], "max_pages": 2}})
    desk.set_mapping(me, ws, requirement="R1", field=None, document="statement.pdf")
    desk.recheck(me, ws)
    engine["ok"] = True
    desk.recheck(me, ws)
    desk.recheck(me, ws)
    with sqlite3.connect(db) as conn:
        counts = dict(conn.execute("SELECT check_name, COUNT(*) FROM check_results GROUP BY check_name").fetchall())
    assert counts == {"presence": 1, "format": 1, "pages": 1}, counts

