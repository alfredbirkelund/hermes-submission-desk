"""What the Desktop backend promises its callers (independent review, round 1)."""

from __future__ import annotations

from helpers import item

CALL = "The statement must be a PDF.\nThe supervisor must sign the letter of support.\n"


def _workspace(host, material) -> str:
    (material / "call.txt").write_text(CALL, encoding="utf-8")
    ws = host.api("POST", "/workspaces", {"title": "Contract", "material_root": str(material)})["body"]["workspace"]["id"]
    assert host.api("POST", f"/workspaces/{ws}/sources", {"path": "call.txt"})["status"] == 200
    return ws


def test_malformed_numbers_are_refused_as_client_errors_without_internals(host, material):
    ws = _workspace(host, material)
    host.api("POST", f"/workspaces/{ws}/requirements", {"statement": "Statement as PDF", "kind": "document"})
    out = host.run([
        {"op": "api", "method": "POST", "path": f"/workspaces/{ws}/requirements/R1/decide",
         "json": {"version": "abc", "decision": "confirm"}},
        {"op": "api", "method": "POST", "path": f"/workspaces/{ws}/requirements/R1/revise",
         "json": {"statement": "x", "expected_revision": "abc"}},
        {"op": "api", "method": "POST", "path": f"/workspaces/{ws}/mappings/M1/review",
         "json": {"decision": "accept", "mapping_version": [1], "requirement_version": 1, "sha256": "x"}},
    ])
    for response in out:
        assert response["status"] == 422, response
        assert "ValueError" not in response["body"]["message"] and "must be a whole number" in response["body"]["message"]


def test_a_request_key_only_deduplicates_the_same_request(host, material):
    ws = _workspace(host, material)
    first = {"statement": "Statement as PDF", "kind": "document", "idempotency_key": "K1"}
    out = host.run([
        {"op": "api", "method": "POST", "path": f"/workspaces/{ws}/requirements", "json": first},
        {"op": "api", "method": "POST", "path": f"/workspaces/{ws}/requirements", "json": first},  # a retry
        {"op": "api", "method": "POST", "path": f"/workspaces/{ws}/fields", "json": {"label": "Statement", "idempotency_key": "K1"}},
        {"op": "api", "method": "POST", "path": f"/workspaces/{ws}/requirements",
         "json": {"statement": "Something else", "kind": "document", "idempotency_key": "K1"}},
        {"op": "api", "method": "GET", "path": f"/workspaces/{ws}"},
    ])
    assert out[0]["status"] == 200 and out[1]["body"] == out[0]["body"], (out[0], out[1])
    assert out[2]["status"] == 200 and out[2]["body"]["field"]["code"] == "F1", out[2]  # another endpoint: its own request
    assert out[3]["status"] == 422 and "different request" in out[3]["body"]["message"], out[3]
    state = out[4]["body"]
    assert [i["code"] for i in state["items"]] == ["R1"] and [f["code"] for f in state["fields"]] == ["F1"]


def test_sources_and_the_folder_say_who_added_them(host, material):
    (material / "call.txt").write_text(CALL, encoding="utf-8")
    created = host.tool("submission_desk_workspace", {"action": "create", "title": "Provenance", "material_root": str(material)})
    ws = created["workspace"]["id"]
    host.api("POST", f"/workspaces/{ws}/folder/confirm", {"material_root": str(material)})
    host.tool("submission_desk_workspace", {"action": "add", "workspace": ws, "source_texts": [
        {"label": "Official rules (from the institution)", "text": "The essay must be 500 words.", "origin": "portal"}]})
    host.api("POST", f"/workspaces/{ws}/sources", {"path": "call.txt"})
    host.tool("submission_desk_propose", {"workspace": ws, "requirements": [
        {"statement": "Essay of 500 words", "kind": "other", "source": "S1", "excerpt": "The essay must be 500 words."}]})
    out = host.run([
        {"op": "api", "method": "POST", "path": f"/workspaces/{ws}/requirements/R1/decide", "json": {"version": 1, "decision": "confirm"}},
        {"op": "api", "method": "POST", "path": f"/workspaces/{ws}/export"},
    ])
    state = host.api("GET", f"/workspaces/{ws}")["body"]
    added = {s["code"]: s["added_by"] for s in state["sources"]}
    assert added["S1"] == "agent proposal" and added["S2"] != "agent proposal", added
    assert item(state, "R1")["requirement"]["source"]["added_by"] == "agent proposal"
    folder = state["workspace"]["folder"]
    assert folder["chosen_by"] == "agent proposal" and folder["confirmed"] and folder["confirmed_by"] != "agent proposal", folder
    checklist = out[1]["body"]["checklist_text"]
    assert "Official rules (from the institution) (S1 rev 1, added by the agent" in checklist, checklist


def test_a_withdrawn_requirement_revised_by_the_agent_can_be_confirmed_on_the_page(host, material):
    ws = _workspace(host, material)
    excerpt = "The supervisor must sign the letter of support."
    host.tool("submission_desk_propose", {"workspace": ws, "requirements": [
        {"statement": "Supervisor signs", "kind": "human_action", "source": "S1", "excerpt": excerpt}]})
    host.run([
        {"op": "api", "method": "POST", "path": f"/workspaces/{ws}/requirements/R1/decide", "json": {"version": 1, "decision": "confirm"}},
        {"op": "api", "method": "POST", "path": f"/workspaces/{ws}/requirements/R1/decide", "json": {"version": 1, "decision": "withdraw"}},
    ])
    host.tool("submission_desk_propose", {"workspace": ws, "requirements": [
        {"requirement": "R1", "statement": "Supervisor signs (applies after all)", "kind": "human_action", "source": "S1", "excerpt": excerpt}]})
    out = host.run([
        {"op": "api", "method": "POST", "path": f"/workspaces/{ws}/requirements/R1/decide", "json": {"version": 3, "decision": "confirm"}},
        {"op": "api", "method": "GET", "path": f"/workspaces/{ws}"},
    ])
    assert out[0]["status"] == 200, out[0]
    row = item(out[1]["body"], "R1")
    assert row["requirement"]["version"] == 3 and row["state"] == "human_action", row["state"]
    assert out[1]["body"]["proposals"] == []
