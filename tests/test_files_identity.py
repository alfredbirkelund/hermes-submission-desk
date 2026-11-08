"""File identity and the material-folder boundary, with real files on disk."""

from __future__ import annotations

import hashlib
import os
import shutil
import sys
from pathlib import Path

import pytest

from helpers import REPO, item
from pdfs import text_pdf


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _mapped_and_reviewed(host, material, rel="drafts/essay.pdf", fields=("Essay upload",)):
    text_pdf(material / rel, ["Essay. Final version."])
    ws = host.api("POST", "/workspaces", {"title": "Identity", "material_root": str(material)})["body"]["workspace"]["id"]
    ops = [{"op": "api", "method": "POST", "path": f"/workspaces/{ws}/requirements",
            "json": {"statement": "Essay as PDF", "kind": "document", "rules": {"formats": ["pdf"]}}}]
    for index, label in enumerate(fields, start=1):
        ops.append({"op": "api", "method": "POST", "path": f"/workspaces/{ws}/fields", "json": {"label": label}})
        ops.append({"op": "api", "method": "POST", "path": f"/workspaces/{ws}/mappings",
                    "json": {"requirement": "R1", "field": f"F{index}", "document": rel}})
    ops.append({"op": "api", "method": "POST", "path": f"/workspaces/{ws}/check"})
    ops.append({"op": "api", "method": "POST", "path": f"/workspaces/{ws}/mappings/M1/review",
                "json": {"decision": "accept", "mapping_version": 1, "requirement_version": 1, "sha256": _sha(material / rel)}})
    ops.append({"op": "api", "method": "POST", "path": f"/workspaces/{ws}/check"})
    out = host.run(ops)
    assert all(o["status"] == 200 for o in out), [o for o in out if o["status"] != 200]
    return ws, out[-1]["body"]


def test_a_moved_file_keeps_its_identity_and_review(host, material):
    ws, state = _mapped_and_reviewed(host, material)
    assert item(state, "R1")["state"] == "ready"
    (material / "final").mkdir()
    shutil.move(material / "drafts" / "essay.pdf", material / "final" / "essay (1).pdf")
    state = host.api("POST", f"/workspaces/{ws}/check")["body"]
    row = item(state, "R1")
    assert row["state"] == "ready", row["reasons"]
    assert row["mappings"][0]["document"]["rel_path"] == "final/essay (1).pdf"
    assert any(n["code"] == "moved" for n in state["notes"])

    # Two identical copies elsewhere: the plugin will not guess which one is meant.
    shutil.copy(material / "final" / "essay (1).pdf", material / "copy-a.pdf")
    shutil.move(material / "final" / "essay (1).pdf", material / "copy-b.pdf")
    row = item(host.api("POST", f"/workspaces/{ws}/check")["body"], "R1")
    assert row["state"] == "missing"
    assert "several places" in row["mappings"][0]["checks"][0]["detail"]


def test_same_name_with_different_bytes_is_a_new_revision(host, material):
    ws, state = _mapped_and_reviewed(host, material)
    old = item(state, "R1")["mappings"][0]["document"]["sha256"]
    text_pdf(material / "drafts" / "essay.pdf", ["Essay. A different file under the same name."])
    row = item(host.api("POST", f"/workspaces/{ws}/check")["body"], "R1")
    assert row["mappings"][0]["document"]["sha256"] not in (old, None)
    assert row["state"] == "stale"


def test_same_file_in_two_fields_keeps_separate_reviews(host, material):
    ws, state = _mapped_and_reviewed(host, material, fields=("Essay upload", "Writing sample"))
    row = item(state, "R1")
    by_code = {m["code"]: m for m in row["mappings"]}
    assert by_code["M1"]["review"]["state"] == "current"
    assert by_code["M2"]["review"]["state"] == "missing"  # reviewing one field does not review the other
    assert row["state"] == "awaiting_review"
    # Identical bytes under two names are flagged, never merged.
    shutil.copy(material / "drafts" / "essay.pdf", material / "essay-copy.pdf")
    out = host.run([{"op": "api", "method": "POST", "path": f"/workspaces/{ws}/documents", "json": {"path": "essay-copy.pdf"}},
                    {"op": "api", "method": "POST", "path": f"/workspaces/{ws}/mappings",
                     "json": {"requirement": "R1", "field": "F1", "document": "drafts/essay.pdf"}},
                    {"op": "api", "method": "POST", "path": f"/workspaces/{ws}/check"}])
    codes = {n["code"] for n in out[-1]["body"]["notes"]}
    assert {"duplicate_content", "duplicate_attachment"} <= codes


def test_paths_outside_the_material_folder_are_refused(host, material, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    text_pdf(outside / "secret.pdf", ["Not part of the application"])
    os.symlink(outside / "secret.pdf", material / "link.pdf")
    os.symlink(outside, material / "linked-dir")
    text_pdf(material / "inside.pdf", ["Inside"])
    os.symlink(material / "inside.pdf", material / "alias.pdf")
    ws = host.api("POST", "/workspaces", {"title": "Boundary", "material_root": str(material)})["body"]["workspace"]["id"]
    out = host.tool("submission_desk_workspace", {"action": "add", "workspace": ws, "documents": [
        "../outside/secret.pdf", str(outside / "secret.pdf"), "link.pdf", "linked-dir/secret.pdf", "alias.pdf", "inside.pdf"]})
    refused = {e["item"] for e in out.get("errors", [])}
    assert refused == {"../outside/secret.pdf", str(outside / "secret.pdf"), "link.pdf", "linked-dir/secret.pdf"}, out
    assert {d["rel_path"] for d in out["documents"]} == {"alias.pdf", "inside.pdf"}
    files = host.api("GET", f"/workspaces/{ws}/files")["body"]["files"]
    assert {f["rel_path"] for f in files} == {"inside.pdf"}  # listings never follow symlinks
    # A mapped file later replaced by a symlink to outside content is refused, not followed.
    host.run([{"op": "api", "method": "POST", "path": f"/workspaces/{ws}/requirements", "json": {"statement": "Doc", "kind": "document"}},
              {"op": "api", "method": "POST", "path": f"/workspaces/{ws}/mappings", "json": {"requirement": "R1", "document": "inside.pdf"}}])
    os.remove(material / "inside.pdf")
    os.symlink(outside / "secret.pdf", material / "inside.pdf")
    row = item(host.api("POST", f"/workspaces/{ws}/check")["body"], "R1")
    presence = row["mappings"][0]["checks"][0]
    assert presence["status"] == "fail" and presence["measurement"]["problem"] == "outside_root"


@pytest.mark.parametrize("root", ["~", "~/.hermes", "/"])
def test_unsafe_material_folders_are_refused(host, root):
    res = host.api("POST", "/workspaces", {"title": "x", "material_root": os.path.expanduser(root)})
    assert res["status"] == 422, res


def test_a_file_swapped_while_it_is_opened_is_not_trusted(material, monkeypatch):
    """Simulates an attacker swapping the file between the check and the open (OS boundary)."""
    sys.path.insert(0, str(REPO))
    from submission_desk import files

    text_pdf(material / "a.pdf", ["original"])
    text_pdf(material / "b.pdf", ["replacement"])
    real_open = os.open
    swapped = {"done": False}

    def swapping_open(path, flags, *args, **kwargs):
        if not swapped["done"] and str(path).endswith("a.pdf"):
            swapped["done"] = True
            os.replace(material / "b.pdf", material / "a.pdf")
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", swapping_open)
    result = files.read_snapshot(material, "a.pdf")
    assert isinstance(result, files.Problem) and result.status == "changed_during_read"


def test_a_directory_swapped_for_a_symlink_mid_open_cannot_redirect_the_read(material, tmp_path, monkeypatch):
    """The parent directory is replaced by a symlink to outside content after the path check."""
    sys.path.insert(0, str(REPO))
    from submission_desk import files

    (material / "sub").mkdir()
    text_pdf(material / "sub" / "doc.pdf", ["inside"])
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    text_pdf(outside / "doc.pdf", ["outside secret"])
    real_stat = os.stat
    state = {"swapped": False}

    def swapping_stat(path, *args, **kwargs):  # the window between the realpath check and the open
        if not state["swapped"] and str(path).endswith("sub/doc.pdf"):
            state["swapped"] = True
            os.rename(material / "sub", material / "sub-moved")
            os.symlink(outside, material / "sub")
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(os, "stat", swapping_stat)
    first = files.read_snapshot(material, "sub/doc.pdf")
    assert isinstance(first, files.Problem) and first.status == "changed_during_read", first
    again = files.read_with_retry(material, "sub/doc.pdf")  # the retry sees the symlink and refuses it
    assert isinstance(again, files.Problem) and again.status == "outside_root", again
