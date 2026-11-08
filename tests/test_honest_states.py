"""No state may look better than its evidence (independent review, round 1).

Each test reproduces a reviewer's finding through the real host: agent tools, the Desktop
backend router, the slash command and separate processes.
"""

from __future__ import annotations

import json
from pathlib import Path

from helpers import agent_workspace, item
from pdfs import text_pdf

CALL = """Example Research Grant (synthetic test fixture)
The research statement must be a PDF of no more than 2 pages.
Upload it in the portal field "Research statement (PDF)".
The file may not exceed 100 bytes.
Applications close on 1 April 2027 at 12:00.
Your supervisor must sign the letter of support.
"""


def _reviewed(host, material, *, rules=None, excerpt="The research statement must be a PDF of no more than 2 pages."):
    """One confirmed requirement mapped to a file and a field, with the applicant's review recorded."""
    (material / "call.txt").write_text(CALL, encoding="utf-8")
    text_pdf(material / "statement.pdf", ["Research statement.", "Second page."])
    ws = agent_workspace(host, "Honest", material, source_files=["call.txt"], documents=["statement.pdf"])
    proposed = host.tool("submission_desk_propose", {"workspace": ws,
        "requirements": [{"ref": "r", "statement": "Research statement", "kind": "document", "source": "S1",
                          "excerpt": excerpt, "rules": rules if rules is not None else {"formats": ["pdf"], "max_pages": 2}}],
        "fields": [{"ref": "f", "label": "Research statement (PDF)", "kind": "upload", "source": "S1",
                    "excerpt": 'Upload it in the portal field "Research statement (PDF)".'}],
        "mappings": [{"requirement": "#r", "field": "#f", "document": "D1"}]})
    assert proposed["stored"] == 3, proposed
    out = host.run([
        {"op": "api", "method": "POST", "path": f"/workspaces/{ws}/requirements/R1/decide", "json": {"version": 1, "decision": "confirm"}},
        {"op": "api", "method": "POST", "path": f"/workspaces/{ws}/check"},
    ])
    mapping = item(out[1]["body"], "R1")["mappings"][0]
    reviewed = host.run([
        {"op": "api", "method": "POST", "path": f"/workspaces/{ws}/mappings/M1/review", "json": {
            "decision": "accept", "mapping_version": mapping["version"], "requirement_version": 1,
            "sha256": mapping["document"]["sha256"]}},
        {"op": "api", "method": "POST", "path": f"/workspaces/{ws}/check"},
    ])
    assert reviewed[0]["status"] == 200, reviewed[0]
    return ws, reviewed[1]["body"]


def test_an_instruction_file_replaced_by_unreadable_content_makes_its_requirements_stale(host, material):
    ws, ready = _reviewed(host, material)
    assert item(ready, "R1")["state"] == "ready", item(ready, "R1")["reasons"]
    (material / "call.txt").write_bytes(bytes(range(256)) * 8)  # binary: no text can be extracted
    out = host.run([
        {"op": "api", "method": "POST", "path": f"/workspaces/{ws}/check"},
        {"op": "api", "method": "GET", "path": f"/workspaces/{ws}"},  # a later snapshot read
    ])
    for state in (out[0]["body"], out[1]["body"]):
        row = item(state, "R1")
        assert row["state"] == "stale", row["reasons"]
        reason = next(r for r in row["reasons"] if r["code"] == "source_unreadable")
        assert "cannot be read" in reason["message"] and reason["next"]
        assert state["summary"]["verdict"] == "blocked"
        source = next(s for s in state["sources"] if s["code"] == "S1")
        assert source["unreadable_revision"]["revision"] == 2, source  # the newest copy is shown, not hidden


def test_a_rule_that_the_requirement_kind_never_checks_is_refused(host, material):
    (material / "call.txt").write_text(CALL, encoding="utf-8")
    ws = agent_workspace(host, "Rules", material, source_files=["call.txt"])
    proposed = host.tool("submission_desk_propose", {"workspace": ws, "requirements": [
        {"statement": "Statement", "kind": "document", "source": "S1",
         "excerpt": "The research statement must be a PDF of no more than 2 pages.",
         "rules": {"formats": ["pdf"], "deadline": {"at": "2020-01-01T12:00", "timezone": "UTC"}}}]})
    assert proposed["stored"] == 0 and proposed["rejected"] == 1, proposed
    assert "deadline" in json.dumps(proposed).lower()
    manual = host.api("POST", f"/workspaces/{ws}/requirements",
                      {"statement": "Supervisor signs the letter", "kind": "human_action", "rules": {"formats": ["pdf"]}})
    assert manual["status"] == 422, manual
    assert "not machine-checked" in manual["body"]["message"]


def test_a_failed_check_cannot_be_verified_by_hand(host, material):
    ws, state = _reviewed(host, material, rules={"formats": ["pdf"], "max_bytes": 100},
                          excerpt="The file may not exceed 100 bytes.")
    row = item(state, "R1")
    assert row["state"] == "failed", row["reasons"]
    sha = row["mappings"][0]["document"]["sha256"]
    out = host.run([
        {"op": "api", "method": "POST", "path": f"/workspaces/{ws}/attest", "json": {
            "subject": "R1", "version": 1, "check": "size", "mapping": "M1", "sha256": sha,
            "statement": "The portal accepted a bigger file when I tried."}},
        {"op": "slash", "line": f"@{ws} verify M1 size I checked it myself", "answers": [0]},
        {"op": "api", "method": "GET", "path": f"/workspaces/{ws}"},
    ])
    assert out[0]["status"] == 422 and "could not establish" in out[0]["body"]["message"], out[0]
    assert "could not establish" in out[1]["text"], out[1]
    row = item(out[2]["body"], "R1")
    assert row["state"] == "failed" and not any(c.get("attested") for c in row["mappings"][0]["checks"])


def test_rechecks_and_exports_never_claim_to_have_verified_a_file_they_could_not_read(host, material):
    ws, _ = _reviewed(host, material)
    (material / "statement.pdf").unlink()
    out = host.run([
        {"op": "api", "method": "POST", "path": f"/workspaces/{ws}/check"},
        {"op": "api", "method": "POST", "path": f"/workspaces/{ws}/export"},
    ])
    checked, exported = out[0]["body"], out[1]["body"]
    assert "every mapped file" not in checked["freshness"], checked["freshness"]
    assert "statement.pdf" in checked["freshness"] and checked["unread"] == ["statement.pdf"]
    assert exported["revalidated"] is False, exported
    manifest = json.loads(Path(exported["manifest"]).read_text(encoding="utf-8"))
    assert manifest["freshness"]["revalidated_after_writing"] is False
    assert manifest["freshness"]["unreadable"] == ["statement.pdf"]
    assert "verified again after this file was written" not in exported["checklist_text"]
    assert "could not be read" in exported["checklist_text"]


def test_an_instruction_file_that_cannot_be_read_is_reported_on_every_read_until_it_can(host, material):
    ws, _ = _reviewed(host, material)
    (material / "call.txt").rename(material / "call-moved-away.txt")
    out = host.run([
        {"op": "api", "method": "POST", "path": f"/workspaces/{ws}/check"},
        {"op": "api", "method": "GET", "path": f"/workspaces/{ws}"},
    ])
    for state in (out[0]["body"], out[1]["body"]):
        notes = [n for n in state["notes"] if n["code"] == "source_unreadable"]
        assert len(notes) == 1 and "S1" in notes[0]["message"], state["notes"]
    (material / "call-moved-away.txt").rename(material / "call.txt")
    again = host.api("POST", f"/workspaces/{ws}/check")["body"]
    assert not [n for n in again["notes"] if n["code"] == "source_unreadable"], again["notes"]


def test_text_that_merely_mentions_a_pdf_header_is_not_a_pdf(host, material):
    (material / "call.txt").write_text(CALL, encoding="utf-8")
    (material / "statement.txt").write_text("Dear committee,\nThe marker %PDF-1.4 appears in this sentence.\n" + "x" * 900,
                                            encoding="utf-8")
    ws = agent_workspace(host, "Sniff", material, source_files=["call.txt"], documents=["statement.txt"])
    host.tool("submission_desk_propose", {"workspace": ws, "requirements": [
        {"ref": "r", "statement": "Statement as PDF", "kind": "document", "source": "S1",
         "excerpt": "The research statement must be a PDF of no more than 2 pages.", "rules": {"formats": ["pdf"]}}],
        "mappings": [{"requirement": "#r", "document": "D1"}]})
    out = host.run([
        {"op": "api", "method": "POST", "path": f"/workspaces/{ws}/requirements/R1/decide", "json": {"version": 1, "decision": "confirm"}},
        {"op": "api", "method": "POST", "path": f"/workspaces/{ws}/check"},
    ])
    fmt = next(c for c in item(out[1]["body"], "R1")["mappings"][0]["checks"] if c["check"] == "format")
    assert fmt["status"] == "fail" and fmt["measurement"]["detected"] == "text", fmt


def test_a_deadline_cannot_be_settled_by_an_attestation(host, material):
    (material / "call.txt").write_text(CALL, encoding="utf-8")
    ws = agent_workspace(host, "Deadline", material, source_files=["call.txt"])
    host.tool("submission_desk_propose", {"workspace": ws, "requirements": [
        {"statement": "Applications close 1 April 2027 12:00", "kind": "deadline", "source": "S1",
         "excerpt": "Applications close on 1 April 2027 at 12:00.", "rules": {"deadline": {"at": "2027-04-01T12:00"}}}]})
    out = host.run([
        {"op": "api", "method": "POST", "path": f"/workspaces/{ws}/requirements/R1/decide", "json": {"version": 1, "decision": "confirm"}},
        {"op": "api", "method": "POST", "path": f"/workspaces/{ws}/attest", "json": {
            "subject": "R1", "version": 1, "statement": "The portal banner says it is local time, UTC-5."}},
        {"op": "api", "method": "GET", "path": f"/workspaces/{ws}"},
    ])
    assert out[1]["status"] == 422 and "time zone" in out[1]["body"]["message"], out[1]
    assert item(out[2]["body"], "R1")["state"] == "unknown"


def test_an_agent_revision_of_a_withdrawn_requirement_waits_for_the_applicant_on_every_surface(host, material):
    (material / "call.txt").write_text(CALL, encoding="utf-8")
    ws = agent_workspace(host, "Withdrawn", material, source_files=["call.txt"])
    excerpt = "Your supervisor must sign the letter of support."
    host.tool("submission_desk_propose", {"workspace": ws, "requirements": [
        {"statement": "Supervisor signs the letter", "kind": "human_action", "source": "S1", "excerpt": excerpt}]})
    host.run([
        {"op": "api", "method": "POST", "path": f"/workspaces/{ws}/requirements/R1/decide", "json": {"version": 1, "decision": "confirm"}},
        {"op": "api", "method": "POST", "path": f"/workspaces/{ws}/requirements/R1/decide", "json": {"version": 1, "decision": "withdraw"}},
    ])
    revised = host.tool("submission_desk_propose", {"workspace": ws, "requirements": [
        {"requirement": "R1", "statement": "Supervisor signs the letter (it applies after all)", "kind": "human_action",
         "source": "S1", "excerpt": excerpt}]})
    assert revised["stored"] == 1, revised
    out = host.run([
        {"op": "api", "method": "GET", "path": f"/workspaces/{ws}"},
        {"op": "slash", "line": f"@{ws} pending"},
        {"op": "tool", "name": "submission_desk_check", "args": {"workspace": ws}},
    ])
    state = out[0]["body"]
    assert [(p["code"], p["version"]) for p in state["proposals"]] == [("R1", 3)], state["proposals"]
    assert item(state, "R1")["state"] == "awaiting_confirmation"
    assert state["summary"]["verdict"] == "blocked"
    assert "R1" in out[1]["text"] and "No proposals" not in out[1]["text"], out[1]
    assert "R1" in json.dumps(out[2]["result"].get("proposals_awaiting_applicant")), out[2]["result"]
