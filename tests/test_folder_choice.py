"""The applicant chooses the material folder; the agent can only propose one (independent review, round 1).

Before this, one unattended agent call could open a workspace over any readable folder (for example
/etc) and read its files back. Now nothing in a folder the agent chose is read until the applicant
confirms that exact folder on a human surface.
"""

from __future__ import annotations

CALL = "The statement must be a PDF.\n"


def _agent_workspace(host, material, **extra):
    (material / "call.txt").write_text(CALL, encoding="utf-8")
    (material / "statement.txt").write_text("My statement.\n", encoding="utf-8")
    return host.tool("submission_desk_workspace", {"action": "create", "title": "Agent chose", "material_root": str(material),
                                                   "source_files": ["call.txt"], "documents": ["statement.txt"], **extra})


def test_nothing_in_a_folder_the_agent_chose_is_read_until_the_applicant_confirms_it(host, material):
    created = _agent_workspace(host, material, source_texts=[{"label": "Portal page", "text": "Upload one PDF."}])
    ws = created["workspace"]["id"]
    refused = {e["item"]: e["error"] for e in created["errors"]}
    assert refused == {"call.txt": "folder_unconfirmed", "statement.txt": "folder_unconfirmed"}, created["errors"]
    assert [s["code"] for s in created["sources"]] == ["S1"]  # pasted text needs no file access
    state = host.tool("submission_desk_check", {"workspace": ws})
    assert state["verdict"] == "blocked" and "confirm the folder" in state["headline"].lower(), state
    page = host.api("GET", f"/workspaces/{ws}")["body"]
    folder = page["workspace"]["folder"]
    assert folder == {**folder, "path": str(material), "confirmed": False, "chosen_by": "agent proposal"}, folder
    assert page["documents"] == []


def test_the_applicant_confirms_the_exact_folder_and_the_agent_can_then_add_files(host, material):
    ws = _agent_workspace(host, material)["workspace"]["id"]
    other = material.parent / "elsewhere"
    out = host.run([
        {"op": "api", "method": "POST", "path": f"/workspaces/{ws}/folder/confirm", "json": {"material_root": str(other)}},
        {"op": "tool", "name": "submission_desk_workspace", "args": {"action": "add", "workspace": ws, "source_files": ["call.txt"]}},
        {"op": "api", "method": "POST", "path": f"/workspaces/{ws}/folder/confirm", "json": {"material_root": str(material)}},
        {"op": "tool", "name": "submission_desk_workspace", "args": {"action": "add", "workspace": ws, "source_files": ["call.txt"],
                                                                     "documents": ["statement.txt"]}},
        {"op": "api", "method": "GET", "path": f"/workspaces/{ws}"},
    ])
    assert out[0]["status"] == 409 and out[0]["body"]["error"] == "stale_revision", out[0]
    assert out[1]["result"]["errors"][0]["error"] == "folder_unconfirmed"
    assert out[2]["status"] == 200, out[2]
    assert "errors" not in out[3]["result"], out[3]["result"]
    folder = out[4]["body"]["workspace"]["folder"]
    assert folder["confirmed"] and folder["confirmed_by"] == "you (Desktop)", folder
    assert [d["rel_path"] for d in out[4]["body"]["documents"]] == ["statement.txt"]


def test_only_a_person_can_confirm_a_folder(host, material):
    ws = _agent_workspace(host, material)["workspace"]["id"]
    out = host.run([
        {"op": "slash", "line": f"@{ws} folder confirm"},                 # nobody answers the picker
        {"op": "slash", "line": f"@{ws} folder confirm", "answers": [1]},  # the applicant cancels
        {"op": "api", "method": "GET", "path": f"/workspaces/{ws}"},
        {"op": "slash", "line": f"@{ws} folder confirm", "answers": [0]},
        {"op": "slash", "line": f"@{ws} folder"},
    ])
    assert "Nothing recorded" in out[0]["text"], out[0]
    assert "Cancelled" in out[1]["text"], out[1]
    assert out[2]["body"]["workspace"]["folder"]["confirmed"] is False
    assert "confirmed by you" in out[3]["text"], out[3]
    assert str(material) in out[4]["text"] and "confirmed by you (/submission-desk)" in out[4]["text"], out[4]


def test_a_folder_the_applicant_picks_on_the_page_is_theirs_from_the_start(host, material):
    created = host.api("POST", "/workspaces", {"title": "Mine", "material_root": str(material)})["body"]
    folder = host.api("GET", f"/workspaces/{created['workspace']['id']}")["body"]["workspace"]["folder"]
    assert folder["confirmed"] and folder["chosen_by"] == "you (Desktop)" and folder["confirmed_by"] == "you (Desktop)", folder
