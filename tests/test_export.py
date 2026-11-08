"""Hand-back export: identifies the exact revisions checked, labels snapshots honestly, never
leaves partial output, and renders hostile text inertly."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

import pytest

from helpers import REPO, item
from pdfs import text_pdf

HOSTILE = "[click](javascript:alert(1)) <img src=x onerror=alert(1)>.pdf"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _prepare(host, material):
    text_pdf(material / HOSTILE, ["Statement"])
    text_pdf(material / "cv.pdf", ["CV"])
    ws = host.api("POST", "/workspaces", {"title": "Export <b>test</b> | pipes", "material_root": str(material)})["body"]["workspace"]["id"]
    out = host.run([
        {"op": "api", "method": "POST", "path": f"/workspaces/{ws}/requirements",
         "json": {"statement": "Statement **bold** <script>x</script> as PDF", "kind": "document", "rules": {"formats": ["pdf"], "max_pages": 1}}},
        {"op": "api", "method": "POST", "path": f"/workspaces/{ws}/requirements",
         "json": {"statement": "CV as PDF", "kind": "document", "rules": {"formats": ["pdf"]}}},
        {"op": "api", "method": "POST", "path": f"/workspaces/{ws}/fields", "json": {"label": "Statement | upload"}},
        {"op": "api", "method": "POST", "path": f"/workspaces/{ws}/fields", "json": {"label": "CV"}},
        {"op": "api", "method": "POST", "path": f"/workspaces/{ws}/mappings", "json": {"requirement": "R1", "field": "F1", "document": HOSTILE}},
        {"op": "api", "method": "POST", "path": f"/workspaces/{ws}/mappings", "json": {"requirement": "R2", "field": "F2", "document": "cv.pdf"}},
        {"op": "api", "method": "POST", "path": f"/workspaces/{ws}/actions", "json": {"description": "Sign the declaration form"}},
    ])
    assert all(o["status"] == 200 for o in out), out
    return ws


def test_export_identifies_checked_revisions_and_renders_hostile_text_inertly(host, material):
    ws = _prepare(host, material)
    out = host.tool("submission_desk_export", {"workspace": ws})
    assert out["ok"] and out["revalidated"] is True and out["changed_during_export"] == []
    export_dir = Path(out["directory"])
    assert export_dir.parent == host.home / "plugin-data" / "submission-desk" / "exports"
    manifest = json.loads(Path(out["manifest"]).read_text(encoding="utf-8"))
    checklist = Path(out["checklist"]).read_text(encoding="utf-8")
    assert manifest["schema"] == "submission-desk/manifest/1"
    listed = {f["rel_path"]: f["sha256"] for f in manifest["files"]}
    assert listed == {HOSTILE: _sha(material / HOSTILE), "cv.pdf": _sha(material / "cv.pdf")}
    assert manifest["freshness"]["revalidated_after_writing"] is True
    r1 = next(r for r in manifest["requirements"] if r["code"] == "R1")
    pages = next(c for c in r1["uploads"][0]["checks"] if c["check"] == "pages")
    assert pages["measurement"] == 1 and pages["units"] == "pages" and pages["sha256"] == listed[HOSTILE]
    assert r1["state"] == "awaiting_review"
    # Nothing a document, filename or statement contains becomes live Markdown or HTML.
    assert "<img" not in checklist.replace("\\<img", "").replace("`" + HOSTILE, "")
    assert "<script>" not in checklist and "\\<script\\>" in checklist
    assert "](javascript:" not in checklist.replace(f"`{HOSTILE}`", "")
    assert "| Statement \\| upload |" in checklist
    assert "Sign the declaration form" in checklist and "[ ]" in checklist
    assert "does not log in, upload, sign or submit" in checklist
    # Exports never contain the applicant's documents.
    assert sorted(p.name for p in export_dir.iterdir()) == ["checklist.md", "manifest.json"]


def _cli_export(monkeypatch, tmp_path, ws_ref):
    sys.path.insert(0, str(REPO))
    from submission_desk import commands

    return commands.run_cli(argparse.Namespace(sd_command="export", workspace=ws_ref))


def test_a_file_changed_during_export_makes_it_snapshot_only(host, material, monkeypatch, tmp_path, capsys):
    ws = _prepare(host, material)
    db = host.home / "plugin-data" / "submission-desk" / "data.db"
    monkeypatch.setenv("SUBMISSION_DESK_DB", str(db))
    checked = _sha(material / "cv.pdf")
    real_fsync = os.fsync
    state = {"calls": 0}

    def fsync_and_edit(fd):  # the first write of the export is where the user saves a new version
        state["calls"] += 1
        if state["calls"] == 1:
            text_pdf(material / "cv.pdf", ["CV, saved again while exporting"])
        return real_fsync(fd)

    monkeypatch.setattr(os, "fsync", fsync_and_edit)
    assert _cli_export(monkeypatch, tmp_path, ws) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["revalidated"] is False and out["changed_during_export"] == ["cv.pdf"]
    manifest = json.loads(Path(out["manifest"]).read_text(encoding="utf-8"))
    assert next(f for f in manifest["files"] if f["rel_path"] == "cv.pdf")["sha256"] == checked  # what was checked
    checklist = Path(out["checklist"]).read_text(encoding="utf-8")
    assert "SNAPSHOT ONLY" in checklist and "`cv.pdf`" in checklist


def test_an_interrupted_export_leaves_no_partial_output(host, material, monkeypatch, tmp_path, capsys):
    ws = _prepare(host, material)
    exports = host.home / "plugin-data" / "submission-desk" / "exports"
    monkeypatch.setenv("SUBMISSION_DESK_DB", str(exports.parent / "data.db"))
    real_replace = os.replace

    def failing_replace(src, dst, *args, **kwargs):
        if str(src).endswith(".partial"):
            raise OSError(28, "No space left on device")
        return real_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(os, "replace", failing_replace)
    with pytest.raises(OSError):
        _cli_export(monkeypatch, tmp_path, ws)
    assert list(exports.iterdir()) == []
    monkeypatch.setattr(os, "replace", real_replace)
    assert _cli_export(monkeypatch, tmp_path, ws) == 0
    assert len(list(exports.iterdir())) == 1


def test_the_cli_says_so_when_there_is_no_application_yet(tmp_path, monkeypatch, capsys):
    """`hermes submission-desk` right after install must not print nothing."""
    monkeypatch.setenv("SUBMISSION_DESK_DB", str(tmp_path / "db" / "data.db"))
    sys.path.insert(0, str(REPO))
    from submission_desk import commands

    assert commands.run_cli(argparse.Namespace(sd_command=None)) == 0
    assert "No application workspace yet" in capsys.readouterr().out
