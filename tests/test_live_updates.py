"""The Desktop page hears about every committed change, whichever surface made it."""

from __future__ import annotations

from pdfs import text_pdf

EVENT = "plugin.submission-desk.ledger.changed"


def test_each_committed_change_is_announced_once_after_commit_and_refusals_are_silent(host, material):
    text_pdf(material / "cv.pdf", ["CV"])
    ws = host.api("POST", "/workspaces", {"title": "Live", "material_root": str(material)})["body"]["workspace"]["id"]
    out = host.run([
        {"op": "capture_events"},
        # Desktop (human) writes
        {"op": "api", "method": "POST", "path": f"/workspaces/{ws}/requirements",
         "json": {"statement": "CV as PDF", "kind": "document", "rules": {"formats": ["pdf"]}}},
        {"op": "api", "method": "POST", "path": f"/workspaces/{ws}/mappings", "json": {"requirement": "R1", "document": "cv.pdf"}},
        # the agent rechecks (no decision, but the page must show the new results)
        {"op": "tool", "name": "submission_desk_check", "args": {"workspace": ws}},
        # refused writes change nothing and must announce nothing
        {"op": "api", "method": "POST", "path": f"/workspaces/{ws}/requirements/R1/revise",
         "json": {"statement": "CV (from a stale window)", "expected_revision": 1}},
        {"op": "tool", "name": "submission_desk_propose", "args": {"workspace": ws, "human_confirmed": True}},
        {"op": "events"},
    ])
    assert out[4]["status"] == 409, out[4]
    events = out[-1]["events"]
    assert [e["name"] for e in events] == [EVENT] * 3, events
    for e in events:
        assert set(e["payload"]) == {"workspace", "revision"}, e  # ids only: no requirement text or file names
        assert e["payload"]["workspace"] == ws
        # emitted after the transaction committed: a separate connection already sees that revision
        assert e["committed_revision"] == e["payload"]["revision"], e
    revisions = [e["payload"]["revision"] for e in events]
    assert revisions[0] < revisions[1] == revisions[2], revisions  # a recheck records results, not a decision


def test_a_process_without_a_gateway_announces_nothing_and_keeps_its_stdout(host, material):
    # hermes chat, cron and the CLI run tools with no Desktop client attached. Loading the gateway
    # there just to announce would rebind sys.stdout for the whole process.
    ws = host.api("POST", "/workspaces", {"title": "Quiet", "material_root": str(material)})["body"]["workspace"]["id"]
    out = host.run([
        {"op": "api", "method": "POST", "path": f"/workspaces/{ws}/requirements",
         "json": {"statement": "CV as PDF", "kind": "document", "rules": {"formats": ["pdf"]}}},
        {"op": "tool", "name": "submission_desk_check", "args": {"workspace": ws}},
        {"op": "slash", "line": "status"},
        {"op": "process"},
    ])
    assert out[0]["status"] == 200, out[0]
    assert out[-1] == {"gateway_loaded": False, "stdout_intact": True}, out[-1]
