"""Requirements and their sources: drift, conflicts, unclear units, deadlines, manual entry."""

from __future__ import annotations

from helpers import item
from pdfs import text_pdf

CALL = """Synthetic scholarship call 2027 (test fixture)
Section 2. The personal statement must not exceed 2 pages.
Section 5. Applications close on 15 March 2027 at 23:59.
"""
FAQ = """Applicant FAQ, updated (test fixture)
Q: How long can the personal statement be? A: The personal statement may be up to 3 pages.
"""


def _ws(host, material):
    (material / "call.txt").write_text(CALL, encoding="utf-8")
    (material / "faq.html").write_text(f"<html><body><script>alert(1)</script><p>{FAQ}</p></body></html>", encoding="utf-8")
    text_pdf(material / "statement.pdf", ["Statement", "Page 2", "Page 3"])
    out = host.tool("submission_desk_workspace", {"action": "create", "title": "Sources", "material_root": str(material),
                                                   "source_files": ["call.txt", "faq.html"], "documents": ["statement.pdf"]})
    return out["workspace"]["id"]


def test_source_change_after_confirmation_makes_the_requirement_stale(host, material):
    ws = _ws(host, material)
    host.tool("submission_desk_propose", {"workspace": ws, "requirements": [
        {"statement": "Personal statement at most 2 pages", "kind": "document", "source": "S1",
         "excerpt": "The personal statement must not exceed 2 pages.", "rules": {"max_pages": 2}}]})
    host.api("POST", f"/workspaces/{ws}/requirements/R1/decide", {"version": 1, "decision": "confirm"})
    row = item(host.api("POST", f"/workspaces/{ws}/check")["body"], "R1")
    assert not any(r["code"] == "source_changed" for r in row["reasons"])
    (material / "call.txt").write_text(CALL.replace("must not exceed 2 pages", "must not exceed 4 pages"), encoding="utf-8")
    state = host.api("POST", f"/workspaces/{ws}/check")["body"]
    row = item(state, "R1")
    assert any(r["code"] == "source_changed" and r["state"] == "stale" for r in row["reasons"]), row["reasons"]
    assert row["requirement"]["excerpt"] == "The personal statement must not exceed 2 pages."  # the old quote is kept
    source = next(s for s in state["sources"] if s["code"] == "S1")
    assert source["revision"] == 2


def test_conflicting_instructions_stay_visible_until_the_applicant_resolves_them(host, material):
    ws = _ws(host, material)
    proposed = host.tool("submission_desk_propose", {"workspace": ws,
        "requirements": [
            {"ref": "a", "statement": "Statement at most 2 pages (call)", "kind": "document", "source": "S1",
             "excerpt": "The personal statement must not exceed 2 pages.", "rules": {"max_pages": 2}},
            {"ref": "b", "statement": "Statement at most 3 pages (FAQ)", "kind": "document", "source": "S2",
             "excerpt": "The personal statement may be up to 3 pages.", "rules": {"max_pages": 3}}],
        "conflicts": [{"description": "The call says 2 pages, the newer FAQ says 3.", "requirements": ["#a", "#b"],
                       "excerpts": [{"source": "S1", "excerpt": "must not exceed 2 pages"},
                                    {"source": "S2", "excerpt": "may be up to 3 pages"}]}]})
    assert proposed["stored"] == 3, proposed
    one_sided = host.tool("submission_desk_propose", {"workspace": ws, "conflicts": [
        {"description": "x", "requirements": ["R1"], "excerpts": [{"source": "S1", "excerpt": "must not exceed 2 pages"}]}]})
    assert one_sided["rejected"] == 1  # the agent must quote both sides
    for code in ("R1", "R2"):
        host.api("POST", f"/workspaces/{ws}/requirements/{code}/decide", {"version": 1, "decision": "confirm"})
    state = host.api("POST", f"/workspaces/{ws}/check")["body"]
    assert item(state, "R1")["state"] == "conflicting" and item(state, "R2")["state"] == "conflicting"
    conflict = state["conflicts"][0]
    assert [e["source"] for e in conflict["excerpts"]] == ["S1", "S2"]
    agent_try = host.tool("submission_desk_propose", {"workspace": ws, "conflicts": [{"resolution": "FAQ wins"}]})
    assert agent_try.get("error") == "invalid" or agent_try.get("rejected") == 1
    done = host.api("POST", f"/workspaces/{ws}/conflicts/C1/resolve",
                    {"resolution": "Emailed the office: the FAQ is current, 3 pages.", "governing": "R2"})
    assert done["status"] == 200
    state = host.api("POST", f"/workspaces/{ws}/check")["body"]
    assert item(state, "R1")["state"] != "conflicting"
    assert state["conflicts"][0]["status"] == "resolved" and state["conflicts"][0]["governing"] == "R2"


def test_unclear_units_and_untimed_deadlines_are_not_guessed(host, material):
    ws = _ws(host, material)
    ambiguous = host.api("POST", f"/workspaces/{ws}/requirements",
                         {"statement": "Max 1,000 KB", "kind": "document", "rules": {"max_size": "1,000 KB"}})
    assert ambiguous["status"] == 422 and "ambiguous" in ambiguous["body"]["message"]
    host.tool("submission_desk_propose", {"workspace": ws, "requirements": [
        {"statement": "Deadline 15 March 2027 23:59", "kind": "deadline", "source": "S1",
         "excerpt": "Applications close on 15 March 2027 at 23:59.", "rules": {"deadline": {"at": "2027-03-15T23:59"}}}]})
    host.api("POST", f"/workspaces/{ws}/requirements/R1/decide", {"version": 1, "decision": "confirm"})
    row = item(host.api("GET", f"/workspaces/{ws}")["body"], "R1")
    assert row["state"] == "unknown" and row["reasons"][0]["code"] == "deadline_timezone"
    host.api("POST", f"/workspaces/{ws}/requirements/R1/revise",
             {"rules": {"deadline": {"at": "2027-03-15T23:59", "timezone": "Europe/Copenhagen"}}})
    row = item(host.api("GET", f"/workspaces/{ws}")["body"], "R1")
    assert row["state"] == "ready" and row["deadline"]["due_utc"] == "2027-03-15T22:59+00:00"
    host.api("POST", f"/workspaces/{ws}/requirements/R1/revise",
             {"rules": {"deadline": {"at": "2020-01-01T12:00", "timezone": "UTC"}}})
    assert item(host.api("GET", f"/workspaces/{ws}")["body"], "R1")["state"] == "failed"


def test_manual_entry_works_without_any_model_and_is_marked_unsourced(host, material):
    ws = _ws(host, material)
    out = host.run([
        {"op": "api", "method": "POST", "path": f"/workspaces/{ws}/requirements",
         "json": {"statement": "Statement max 3 pages", "kind": "document", "rules": {"formats": ["pdf"], "max_pages": 3}}},
        {"op": "api", "method": "POST", "path": f"/workspaces/{ws}/requirements",
         "json": {"statement": "Quoted by hand", "kind": "document", "source": "S1",
                  "excerpt": "the personal statement must NOT exceed 2 pages"}},
        {"op": "api", "method": "POST", "path": f"/workspaces/{ws}/fields", "json": {"label": "Personal statement"}},
        {"op": "api", "method": "POST", "path": f"/workspaces/{ws}/mappings", "json": {"requirement": "R1", "field": "F1", "document": "D1"}},
        {"op": "api", "method": "POST", "path": f"/workspaces/{ws}/check"},
    ])
    assert [o["status"] for o in out] == [200] * 5, out
    r2 = out[1]["body"]["requirement"]
    assert r2["status"] == "confirmed" and r2["evidence"]["match"] == "normalized_casefold"
    state = out[-1]["body"]
    assert item(state, "R1").get("no_source_evidence") is True
    assert item(state, "R1")["state"] == "awaiting_review"  # checks pass, the applicant still reviews
