"""Human actions and attestations: tracked, attributed, bound to versions and file bytes."""

from __future__ import annotations

import hashlib

from helpers import item
from pdfs import text_pdf

CALL = """Synthetic grant call (test fixture)
A letter of support signed by your supervisor must be uploaded as a PDF.
Applicants must be enrolled at a Danish university.
"""


def _ws(host, material):
    (material / "call.txt").write_text(CALL, encoding="utf-8")
    text_pdf(material / "support-letter.pdf", ["Letter of support (unsigned draft)"])
    out = host.tool("submission_desk_workspace", {"action": "create", "title": "Actions", "material_root": str(material),
                                                   "source_files": ["call.txt"], "documents": ["support-letter.pdf"]})
    return out["workspace"]["id"]


def test_signature_action_is_proposed_confirmed_done_and_goes_stale_when_the_file_changes(host, material):
    ws = _ws(host, material)
    proposed = host.tool("submission_desk_propose", {"workspace": ws, "actions": [
        {"description": "Get the supervisor to sign the letter of support", "document": "D1"}]})
    assert proposed["results"]["actions"][0]["status"] == "proposed"
    state = host.api("GET", f"/workspaces/{ws}")["body"]
    assert state["actions"][0]["state"] == "awaiting_confirmation"
    # The agent has no way to mark it done.
    forged = host.tool("submission_desk_propose", {"workspace": ws, "actions": [{"description": "Signed", "attested": True}]})
    assert forged["error"] == "human_only"
    host.api("POST", f"/workspaces/{ws}/actions/A1/decide", {"version": 1, "decision": "confirm"})
    state = host.api("POST", f"/workspaces/{ws}/check")["body"]
    assert state["actions"][0]["state"] == "human_action" and state["summary"]["verdict"] == "blocked"
    text_pdf(material / "support-letter.pdf", ["Letter of support, signed by Prof. Example"])
    host.api("POST", f"/workspaces/{ws}/check")
    done = host.slash(f"@{ws} done A1 Signed on paper and scanned", answers=[0])
    assert "recorded as done by you" in done["text"]
    assert "attestation" in done["asked"][0]["question"]  # the picker says it is an attestation, not a verification
    state = host.api("POST", f"/workspaces/{ws}/check")["body"]
    action = state["actions"][0]
    assert action["state"] == "ready" and action["attested"]["by"] == "you (/submission-desk)"
    # Changing the signed file after the attestation needs a new confirmation.
    text_pdf(material / "support-letter.pdf", ["Letter of support, edited after signing"])
    action = host.api("POST", f"/workspaces/{ws}/check")["body"]["actions"][0]
    assert action["state"] == "stale" and "needs a new confirmation" in action["reasons"][0]["message"]


def test_eligibility_attestation_is_the_applicants_statement_only(host, material):
    ws = _ws(host, material)
    host.tool("submission_desk_propose", {"workspace": ws, "requirements": [
        {"statement": "Enrolled at a Danish university", "kind": "attestation", "source": "S1",
         "excerpt": "Applicants must be enrolled at a Danish university."}]})
    host.api("POST", f"/workspaces/{ws}/requirements/R1/decide", {"version": 1, "decision": "confirm"})
    row = item(host.api("GET", f"/workspaces/{ws}")["body"], "R1")
    assert row["state"] == "human_action" and row["reasons"][0]["code"] == "needs_attestation"
    att = host.api("POST", f"/workspaces/{ws}/attest", {"subject": "R1", "version": 1, "statement": "Enrolled at SDU, student no. on file"})
    assert att["status"] == 200 and "not as a verification" in att["body"]["attestation"]["note"]
    state = host.api("GET", f"/workspaces/{ws}")["body"]
    assert item(state, "R1")["state"] == "ready" and item(state, "R1")["attested"]["kind"] == "human attestation"
    host.api("POST", f"/workspaces/{ws}/attestations/{att['body']['attestation']['id']}/revoke")
    assert item(host.api("GET", f"/workspaces/{ws}")["body"], "R1")["state"] == "human_action"


def test_a_check_verified_by_hand_is_labelled_and_bound_to_the_bytes(host, material):
    ws = _ws(host, material)
    (material / "essay.txt").write_text("one two three four five", encoding="utf-8")
    out = host.run([
        {"op": "api", "method": "POST", "path": f"/workspaces/{ws}/requirements",
         "json": {"statement": "Essay max 500 words (counting method not stated)", "kind": "document", "rules": {"max_words": 500}}},
        {"op": "api", "method": "POST", "path": f"/workspaces/{ws}/fields", "json": {"label": "Essay"}},
        {"op": "api", "method": "POST", "path": f"/workspaces/{ws}/mappings", "json": {"requirement": "R1", "field": "F1", "document": "essay.txt"}},
        {"op": "api", "method": "POST", "path": f"/workspaces/{ws}/check"}])
    row = item(out[-1]["body"], "R1")
    assert any(r["code"] == "check_words" and r["state"] == "unknown" for r in row["reasons"])
    sha = hashlib.sha256((material / "essay.txt").read_bytes()).hexdigest()
    verify = host.slash(f"@{ws} verify M1 words Counted 5 words with the portal's counter", answers=[0])
    assert "recorded" in verify["text"], verify
    row = item(host.api("POST", f"/workspaces/{ws}/check")["body"], "R1")
    words = next(c for c in row["mappings"][0]["checks"] if c["check"] == "words")
    assert words["status"] == "unknown" and words["attested"]["kind"].startswith("human attestation")
    assert not any(r["code"] == "check_words" for r in row["reasons"])
    assert words["sha256"] == sha
    (material / "essay.txt").write_text("one two three four five six", encoding="utf-8")
    row = item(host.api("POST", f"/workspaces/{ws}/check")["body"], "R1")
    assert any(r["code"] == "check_words" for r in row["reasons"])  # the hand count covered the old bytes only
