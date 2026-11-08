"""The core invariant from the brief, exercised through the real host: agent tools (registry
dispatch), the Desktop backend router (human decisions), separate processes (restarts) and real
PDF bytes edited without changing size or modification time.
"""

from __future__ import annotations

import hashlib

from helpers import item
from pdfs import replace_same_size, text_pdf

INSTRUCTIONS = """Demo Research Fellowship 2027: how to apply (synthetic fixture)

1. The research statement must be a PDF of no more than 2 pages.
2. Upload it in the portal field "Research statement (PDF)".
3. A letter of support signed by your supervisor is required.
"""


def _setup_reviewed(host, material):
    (material / "call.txt").write_text(INSTRUCTIONS, encoding="utf-8")
    text_pdf(material / "statement.pdf", ["Research statement. Version A.", "Second page."])
    created = host.tool("submission_desk_workspace", {
        "action": "create", "title": "Demo Research Fellowship 2027", "material_root": str(material),
        "source_files": ["call.txt"], "documents": ["statement.pdf"],
    })
    assert "error" not in created, created
    ws = created["workspace"]["id"]
    proposed = host.tool("submission_desk_propose", {
        "workspace": ws,
        "requirements": [{"ref": "r1", "statement": "Research statement as a PDF of at most 2 pages", "kind": "document",
                          "source": "S1", "excerpt": "The research statement must be a PDF of no more than 2 pages.",
                          "rules": {"formats": ["pdf"], "max_pages": 2}}],
        "fields": [{"ref": "f1", "label": "Research statement (PDF)", "kind": "upload", "source": "S1",
                    "excerpt": 'Upload it in the portal field "Research statement (PDF)".'}],
        "mappings": [{"requirement": "#r1", "field": "#f1", "document": "D1"}],
    })
    assert proposed["stored"] == 3 and proposed["rejected"] == 0, proposed
    # A proposal is not authoritative: the requirement waits for the applicant.
    before = host.api("GET", f"/workspaces/{ws}")["body"]
    assert item(before, "R1")["state"] == "awaiting_confirmation"
    decided = host.api("POST", f"/workspaces/{ws}/requirements/R1/decide",
                       {"version": 1, "decision": "confirm", "expected_revision": before["workspace"]["revision"]})
    assert decided["status"] == 200, decided
    checked = host.api("POST", f"/workspaces/{ws}/check")["body"]
    row = item(checked, "R1")
    assert row["state"] == "awaiting_review", row["reasons"]
    mapping = row["mappings"][0]
    sha = mapping["document"]["sha256"]
    assert sha == hashlib.sha256((material / "statement.pdf").read_bytes()).hexdigest()
    pages = next(c for c in mapping["checks"] if c["check"] == "pages")
    assert (pages["status"], pages["measurement"], pages["limit"]["max"], pages["units"]) == ("pass", 2, 2, "pages")
    review = host.api("POST", f"/workspaces/{ws}/mappings/M1/review", {
        "decision": "accept", "mapping_version": mapping["version"], "requirement_version": 1, "sha256": sha,
        "expected_revision": checked["workspace"]["revision"],
    })
    assert review["status"] == 200, review
    ready = host.api("POST", f"/workspaces/{ws}/check")["body"]
    assert item(ready, "R1")["state"] == "ready", item(ready, "R1")["reasons"]
    return ws, sha


def test_review_goes_stale_when_bytes_change_and_survives_restart(host, material):
    ws, sha_a = _setup_reviewed(host, material)
    replace_same_size(material / "statement.pdf", b"Version A", b"Version B")

    # Fresh process (restart) + recheck through the agent tool.
    after = host.tool("submission_desk_check", {"workspace": ws, "detail": "full"})
    row = next(i for i in after["items"] if i["code"] == "R1")
    assert row["state"] == "stale", row
    assert any("the file changed" in reason for reason in row["reasons"]), row["reasons"]

    # Another restart, no recheck: the stored snapshot is labelled and still stale.
    snapshot = host.api("GET", f"/workspaces/{ws}")["body"]
    assert snapshot["fresh"] is False and snapshot["freshness"].startswith("Snapshot from")
    assert item(snapshot, "R1")["state"] == "stale"
    history = host.api("GET", f"/workspaces/{ws}/history?subject=M1")["body"]
    assert [(r["decision"], r["sha256"]) for r in history["reviews"]] == [("accepted", sha_a)]


def test_mapping_or_requirement_change_invalidates_review_without_erasing_history(host, material):
    ws, sha_a = _setup_reviewed(host, material)

    # The applicant corrects the portal field label: the review covered the old label.
    state = host.api("GET", f"/workspaces/{ws}")["body"]
    revised = host.api("POST", f"/workspaces/{ws}/fields/F1/revise",
                       {"label": "Research statement (PDF, max 2 pages)", "expected_revision": state["workspace"]["revision"]})
    assert revised["status"] == 200, revised
    row = item(host.api("POST", f"/workspaces/{ws}/check")["body"], "R1")
    assert row["state"] == "stale"
    assert any("portal field label changed" in r["message"] for r in row["reasons"]), row["reasons"]

    # Re-review, then point the mapping at another file: stale again, for the mapping change.
    mapping = row["mappings"][0]
    host.api("POST", f"/workspaces/{ws}/mappings/M1/review", {
        "decision": "accept", "mapping_version": mapping["version"], "requirement_version": 1, "sha256": sha_a})
    text_pdf(material / "statement-v2.pdf", ["Research statement, second draft."])
    remapped = host.tool("submission_desk_propose", {"workspace": ws, "mappings": [
        {"requirement": "R1", "mapping": "M1", "document": "statement-v2.pdf"}]})
    assert remapped["stored"] == 1, remapped
    row = item(host.api("POST", f"/workspaces/{ws}/check")["body"], "R1")
    assert row["state"] == "stale"
    assert any("mapping changed" in r["message"] for r in row["reasons"]), row["reasons"]

    # The applicant tightens the requirement (new version): reviews of v1 no longer cover it.
    state = host.api("GET", f"/workspaces/{ws}")["body"]
    host.api("POST", f"/workspaces/{ws}/requirements/R1/revise",
             {"rules": {"formats": ["pdf"], "max_pages": 1}, "expected_revision": state["workspace"]["revision"]})
    row = item(host.api("POST", f"/workspaces/{ws}/check")["body"], "R1")
    assert any("requirement changed" in r["message"] for r in row["reasons"]), row["reasons"]

    history = host.api("GET", f"/workspaces/{ws}/history?subject=M1")["body"]
    assert len(history["reviews"]) == 2  # both earlier reviews kept, none rewritten
    versions = host.api("GET", f"/workspaces/{ws}/history?subject=R1")["body"]["versions"]
    assert [(v["version"], v["status"]) for v in versions] == [(1, "confirmed"), (2, "confirmed")]
