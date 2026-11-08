"""Retries, stale views, two windows, profiles, failed saves, interrupted runs and deletion."""

from __future__ import annotations

import json
import os
import signal
import sqlite3
import stat
import subprocess
import sys
import time

from helpers import DRIVER, HERMES_ROOT, Host, item
from pdfs import text_pdf


def _ws(host, material, title="Concurrency"):
    text_pdf(material / "cv.pdf", ["CV"])
    return host.api("POST", "/workspaces", {"title": title, "material_root": str(material)})["body"]["workspace"]["id"]


def test_retried_requests_with_the_same_key_are_applied_once(host, material):
    ws = _ws(host, material)
    body = {"statement": "CV as PDF", "kind": "document", "idempotency_key": "req-123"}
    out = host.run([{"op": "api", "method": "POST", "path": f"/workspaces/{ws}/requirements", "json": body}] * 3
                   + [{"op": "api", "method": "GET", "path": f"/workspaces/{ws}"}])
    codes = [o["body"]["requirement"]["code"] for o in out[:3]]
    assert codes == ["R1", "R1", "R1"]
    assert [i["code"] for i in out[-1]["body"]["items"]] == ["R1"]


def test_a_stale_window_cannot_overwrite_a_newer_decision(host, material, tmp_path):
    ws = _ws(host, material)
    host.tool("submission_desk_workspace", {"action": "add", "workspace": ws, "documents": ["cv.pdf"]})
    host.api("POST", f"/workspaces/{ws}/requirements", {"statement": "CV", "kind": "document", "rules": {"formats": ["pdf"]}})
    window_a = host.api("GET", f"/workspaces/{ws}")["body"]  # window A loads
    host.api("POST", f"/workspaces/{ws}/mappings", {"requirement": "R1", "document": "D1"})  # window B changes it
    stale = host.api("POST", f"/workspaces/{ws}/requirements/R1/revise",
                     {"statement": "CV (edited in window A)", "expected_revision": window_a["workspace"]["revision"]})
    assert stale["status"] == 409 and stale["body"]["error"] == "stale_revision"
    assert stale["body"]["details"]["current_revision"] > window_a["workspace"]["revision"]
    state = host.api("GET", f"/workspaces/{ws}")["body"]
    assert item(state, "R1")["requirement"]["statement"] == "CV"


def test_mapping_to_a_new_portal_field_is_one_atomic_request(host, material):
    """Desktop sends one request with the revision it loaded; creating the field must not make it stale."""
    ws = _ws(host, material)
    host.api("POST", f"/workspaces/{ws}/requirements", {"statement": "CV as PDF", "kind": "document", "rules": {"formats": ["pdf"]}})
    loaded = host.api("GET", f"/workspaces/{ws}")["body"]["workspace"]["revision"]
    out = host.api("POST", f"/workspaces/{ws}/mappings", {
        "requirement": "R1", "new_field": {"label": "Curriculum vitae (PDF)", "kind": "upload"},
        "document": "cv.pdf", "expected_revision": loaded})
    assert out["status"] == 200, out
    assert out["body"]["mapping"]["field"] == "F1" and out["body"]["mapping"]["document"] == "D1"
    state = host.api("GET", f"/workspaces/{ws}")["body"]
    assert state["workspace"]["revision"] == loaded + 1  # one change, one revision
    mapping = item(state, "R1")["mappings"][0]
    assert mapping["field"]["label"] == "Curriculum vitae (PDF)" and mapping["document"]["rel_path"] == "cv.pdf"

    # A refused mapping leaves no orphan field behind.
    bad = host.api("POST", f"/workspaces/{ws}/mappings", {
        "requirement": "R404", "new_field": {"label": "Budget (PDF)"}, "expected_revision": loaded + 1})
    assert bad["status"] == 404, bad
    labels = [f["label"] for f in host.api("GET", f"/workspaces/{ws}")["body"]["fields"]]
    assert labels == ["Curriculum vitae (PDF)"]


def test_profiles_keep_separate_ledgers(host, material, tmp_path):
    ws = _ws(host, material)
    other_home = tmp_path / "other-profile"
    (other_home / "plugins").mkdir(parents=True)
    os.symlink(os.path.realpath(host.home / "plugins" / "submission-desk"), other_home / "plugins" / "submission-desk")
    (other_home / "config.yaml").write_text("plugins:\n  enabled:\n    - submission-desk\n", encoding="utf-8")
    other = Host(other_home)
    assert other.tool("submission_desk_workspace", {"action": "list"}) == {"workspaces": []}
    assert host.tool("submission_desk_workspace", {"action": "list"})["workspaces"][0]["id"] == ws
    assert (other_home / "plugin-data" / "submission-desk" / "data.db").exists()


def test_a_failed_save_is_reported_and_leaves_no_partial_state(host, material):
    ws = _ws(host, material)
    host.api("POST", f"/workspaces/{ws}/requirements", {"statement": "CV", "kind": "document"})
    data_dir = host.home / "plugin-data" / "submission-desk"
    before = host.api("GET", f"/workspaces/{ws}")["body"]
    modes = {p: p.stat().st_mode for p in [data_dir, *data_dir.iterdir()]}
    try:
        for path in modes:
            path.chmod(stat.S_IRUSR | (stat.S_IXUSR if path.is_dir() else 0))
        failed = host.api("POST", f"/workspaces/{ws}/requirements/R1/revise", {"statement": "CV, revised"})
        assert failed["status"] >= 500 or failed["status"] == 409, failed
        assert failed["body"].get("hint")  # the error says nothing was changed
    finally:
        for path, mode in modes.items():
            path.chmod(mode)
    after = host.api("GET", f"/workspaces/{ws}")["body"]
    assert item(after, "R1")["requirement"]["statement"] == "CV"
    assert after["workspace"]["revision"] == before["workspace"]["revision"]


def test_a_killed_recheck_leaves_a_consistent_ledger(host, material):
    ws = _ws(host, material)
    for index in range(60):
        (material / f"big-{index:02d}.bin").write_bytes(os.urandom(2 * 1024 * 1024))
    host.run([{"op": "api", "method": "POST", "path": f"/workspaces/{ws}/requirements", "json": {"statement": "Files", "kind": "document"}}]
             + [{"op": "api", "method": "POST", "path": f"/workspaces/{ws}/mappings",
                 "json": {"requirement": "R1", "document": f"big-{i:02d}.bin"}} for i in range(60)])
    env = {k: v for k, v in os.environ.items() if not k.startswith(("HERMES_", "SUBMISSION_DESK"))}
    env.update(HERMES_HOME=str(host.home), PYTHONPATH=str(HERMES_ROOT))
    ops = json.dumps([{"op": "api", "method": "POST", "path": f"/workspaces/{ws}/check"}] * 20)
    proc = subprocess.Popen([sys.executable, str(DRIVER)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, env=env)
    proc.stdin.write(ops.encode())
    proc.stdin.close()
    time.sleep(4.0)
    proc.send_signal(signal.SIGKILL)
    proc.wait(timeout=30)
    db = host.home / "plugin-data" / "submission-desk" / "data.db"
    with sqlite3.connect(db) as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conn.execute("SELECT COUNT(*) FROM check_runs WHERE status != 'completed'").fetchone()[0] == 0
    state = host.api("POST", f"/workspaces/{ws}/check")["body"]  # restart recovers and completes
    assert state["fresh"] is True and len(item(state, "R1")["mappings"]) == 60


def test_deleting_an_application_needs_confirmation_and_touches_only_its_records(host, material, tmp_path):
    ws_a = _ws(host, material, "Application A")
    other = tmp_path / "material-b"
    other.mkdir()
    text_pdf(other / "cv.pdf", ["B"])
    ws_b = host.api("POST", "/workspaces", {"title": "Application B", "material_root": str(other)})["body"]["workspace"]["id"]
    host.tool("submission_desk_export", {"workspace": ws_a})
    agent_try = host.tool("submission_desk_workspace", {"action": "delete", "workspace": ws_a})
    assert agent_try["error"] == "invalid"  # no agent tool can delete
    first = host.api("POST", f"/workspaces/{ws_a}/delete")
    assert first["status"] == 428 and "cannot be undone" in first["body"]["message"]
    wrong = host.api("POST", f"/workspaces/{ws_a}/delete", {"confirm_token": "nope"})
    assert wrong["status"] == 428
    token = first["body"]["details"]["confirm_token"]
    done = host.api("POST", f"/workspaces/{ws_a}/delete", {"confirm_token": token})
    assert done["status"] == 200 and done["body"]["exports_removed"] == 1
    remaining = host.tool("submission_desk_workspace", {"action": "list"})["workspaces"]
    assert [w["id"] for w in remaining] == [ws_b]
    assert (material / "cv.pdf").exists()  # files on disk are never touched
