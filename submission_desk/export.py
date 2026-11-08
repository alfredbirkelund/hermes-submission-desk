"""Hand-back export: a readable checklist and a machine-readable manifest.

The export rechecks first, so every listed revision was hashed and checked moments before
writing. After writing, every listed file is hashed again: if any changed, the export is
labelled SNAPSHOT ONLY and names the changed files, instead of presenting stale evidence as
current. Exports contain metadata, measurements and the quoted instruction excerpts, never
the applicant's documents themselves.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from pathlib import Path

from . import ENGINE_VERSION, __version__, files
from .actors import Actor
from .readiness import SEVERITY, STATE_LABELS, short
from .store import dumps, record_event

MANIFEST_SCHEMA = "submission-desk/manifest/1"


def md(text) -> str:
    """Escape user, document and agent supplied text for Markdown; no raw HTML survives."""
    value = " ".join(str(text if text is not None else "").split())
    value = value.replace("\\", "\\\\")
    return re.sub(r"([`*_{}\[\]<>()#+!|~])", r"\\\1", value)


def code(text) -> str:
    value = " ".join(str(text if text is not None else "").split())
    run = max((len(m) for m in re.findall(r"`+", value)), default=0)
    fence = "`" * (run + 1)
    pad = " " if value.startswith("`") or value.endswith("`") else ""
    return f"{fence}{pad}{value}{pad}{fence}"


def build_manifest(r: dict, generated_at: str) -> dict:
    files_index: dict[str, dict] = {}
    requirements = []
    for item in r["items"]:
        req = item["requirement"] or {}
        src = req.get("source") or {}
        uploads = []
        for m in item["mappings"]:
            doc = m.get("document")
            if doc and doc.get("sha256"):
                files_index[doc["sha256"] + doc["rel_path"]] = {"code": doc["code"], "rel_path": doc["rel_path"],
                                                               "sha256": doc["sha256"], "bytes": doc.get("size")}
            review = m.get("review") or {}
            latest = review.get("latest") or {}
            uploads.append({
                "mapping": m["code"], "mapping_version": m["version"], "state": m["state"],
                "field": m.get("field"),
                "file": {k: doc.get(k) for k in ("code", "label", "rel_path", "sha256", "size", "verified_at", "status")} if doc else None,
                "checks": [{k: c.get(k) for k in ("check", "status", "measurement", "limit", "units", "method", "estimate", "detail", "attested", "checked_at", "sha256")}
                           for c in m["checks"]],
                "review": {"state": review.get("state"), "decision": latest.get("decision"), "by": latest.get("actor_label"),
                           "at": latest.get("created_at"), "sha256": latest.get("sha256"), "stale_because": review.get("stale_because")},
                "reasons": m["reasons"],
            })
        requirements.append({
            "code": item["code"], "version": req.get("version"), "version_id": req.get("id"), "statement": req.get("statement"),
            "kind": req.get("kind"), "status": req.get("status"), "origin": req.get("origin"), "rules": req.get("rules"),
            "rules_text": req.get("rules_text"), "created_by": req.get("created_by"), "decided_by": req.get("decided_by"),
            "decided_at": req.get("decided_at"),
            "source": {**src, "excerpt": req.get("excerpt"), "locator": req.get("locator")} if req.get("excerpt") else None,
            "state": item["state"], "states": item["states"], "reasons": item["reasons"], "uploads": uploads,
            "attested": item.get("attested"), "deadline": item.get("deadline"),
        })
    return {
        "schema": MANIFEST_SCHEMA,
        "generated_at": generated_at,
        "generator": {"plugin": "submission-desk", "version": __version__, "engine": ENGINE_VERSION},
        "workspace": r["workspace"],
        "freshness": {"rechecked_at": r["checked_at"], "fresh": r["fresh"], "revalidated_after_writing": None,
                      "changed_during_export": []},
        "verdict": r["summary"]["verdict"], "headline": r["summary"]["headline"], "counts": r["summary"]["counts"],
        "responsibility": r["responsibility"],
        "requirements": requirements,
        "actions": r["actions"], "conflicts": r["conflicts"], "notes": r["notes"],
        "files": sorted(files_index.values(), key=lambda f: (f["code"], f["rel_path"])),
    }


def render_checklist(r: dict, manifest: dict) -> str:
    ws = r["workspace"]
    out: list[str] = [f"# Hand-back checklist: {md(ws.get('title'))}", ""]
    out.append(f"Generated {md(manifest['generated_at'])} (UTC) by Submission Desk {__version__}. Folder: {code(ws.get('material_root'))}.")
    fresh = manifest["freshness"]
    if fresh.get("revalidated_after_writing"):
        out.append(f"Freshness: every listed file was re-read and hashed at {md(fresh['rechecked_at'])} and verified again after this file was written.")
    else:
        changed = ", ".join(code(c) for c in fresh.get("changed_during_export") or []) or "unknown"
        out.append(f"**SNAPSHOT ONLY.** These files changed while the export was being written: {changed}. "
                   "The results below describe the revisions listed, not the current files. Recheck and export again.")
    out += ["", f"**{md(manifest['headline'])}**", "", f"> {md(manifest['responsibility'])}", ""]
    out += ["## What goes where", "", "| Portal field | File to upload | Revision (SHA-256, first 12) | Bytes | State |", "|---|---|---|---|---|"]
    rows = 0
    for req in manifest["requirements"]:
        for up in req["uploads"]:
            field = (up.get("field") or {}).get("label") or "(no field chosen)"
            f = up.get("file") or {}
            out.append(f"| {md(field)} | {code(f.get('rel_path') or 'no file')} | {code((f.get('sha256') or '')[:12] or '-')} | "
                       f"{f.get('size') if f.get('size') is not None else '-'} | {md(STATE_LABELS.get(up['state'], up['state']))} ({md(req['code'])}) |")
            rows += 1
    if not rows:
        out.append("| (nothing mapped yet) | | | | |")
    out += ["", "## Requirements", ""]
    ordered = sorted(manifest["requirements"], key=lambda q: SEVERITY.index(q["state"]) if q["state"] in SEVERITY else 99)
    for req in ordered:
        if req["state"] == "excluded":
            continue
        out.append(f"### {md(req['code'])} v{req.get('version')}: {md(req.get('statement'))}")
        out.append("")
        out.append(f"- State: **{md(STATE_LABELS.get(req['state'], req['state']))}** ({md(req.get('status'))}, "
                   f"proposed by {md(req.get('created_by'))}{'; confirmed by ' + md(req.get('decided_by')) if req.get('decided_by') else ''})")
        src = req.get("source")
        if src:
            page = f", p. {src['locator'].get('page')}" if src.get("locator") and src["locator"].get("page") else ""
            out.append(f"- Instruction: {md(src.get('label'))} ({md(src.get('code'))} rev {src.get('revision')}, saved {md(src.get('captured_at'))}{page}): "
                       f"\u201c{md(src.get('excerpt'))}\u201d (a saved snapshot, not the live portal)")
        else:
            out.append("- Instruction: no source evidence (entered by you)")
        for rule in req.get("rules_text") or []:
            out.append(f"- Rule: {md(rule)}")
        if req.get("deadline"):
            out.append(f"- Due (UTC): {md(req['deadline']['due_utc'])}")
        for up in req["uploads"]:
            f = up.get("file") or {}
            field = (up.get("field") or {}).get("label") or "(no field chosen)"
            out.append(f"- Upload into \u201c{md(field)}\u201d: {code(f.get('rel_path') or 'no file')} "
                       f"rev {code((f.get('sha256') or '')[:12] or '-')}, {f.get('size') if f.get('size') is not None else '?'} bytes ({md(up['mapping'])} v{up['mapping_version']})")
            for c in up["checks"]:
                est = " (estimate)" if c.get("estimate") else ""
                att = f" - verified by you: \u201c{md(c['attested']['statement'])}\u201d (attestation, not a machine check)" if c.get("attested") else ""
                out.append(f"  - {md(c['check'])}: {md(c['status'])}{est}: {md(c.get('detail'))} [method: {md(c.get('method'))}]{att}")
            review = up.get("review") or {}
            if review.get("state") == "current":
                out.append(f"  - Your review: {md(review.get('decision'))} by {md(review.get('by'))} at {md(review.get('at'))}, for this exact revision")
            elif review.get("state") == "stale":
                out.append(f"  - Your review is STALE: {md('; '.join(review.get('stale_because') or []))}")
            elif review.get("state") == "rejected":
                out.append("  - You rejected this file for this field")
            else:
                out.append("  - Not reviewed by you yet")
        if req.get("attested"):
            out.append(f"- Confirmed by you: \u201c{md(req['attested']['statement'])}\u201d ({md(req['attested']['at'])}; attestation, not verification)")
        for reason in req["reasons"]:
            nxt = f" Next: {md(reason['next'])}" if reason.get("next") else ""
            out.append(f"- [ ] {md(reason['message'])}{nxt}")
        out.append("")
    actions = [a for a in manifest["actions"] if a["state"] != "excluded"]
    if actions:
        out += ["## Your actions", ""]
        for a in actions:
            box = "x" if a["state"] == "ready" else " "
            done = f" (done: \u201c{md(a['attested']['statement'])}\u201d, {md(a['attested']['at'])}; your attestation)" if a.get("attested") and a["state"] == "ready" else ""
            stale = f" STALE: {md(a['reasons'][0]['message'])}" if a["state"] == "stale" and a.get("reasons") else ""
            out.append(f"- [{box}] {md(a['code'])}: {md(a['description'])}{done}{stale}")
        out.append("")
    conflicts = [c for c in manifest["conflicts"] if c["status"] == "open"]
    if conflicts:
        out += ["## Open conflicts between instructions", ""]
        for c in conflicts:
            out.append(f"- {md(c['code'])} ({md(', '.join(c['requirements']))}): {md(c['description'])}")
            for ex in c["excerpts"]:
                out.append(f"  - {md(ex['source'])}: \u201c{md(ex['excerpt'])}\u201d")
        out.append("")
    unknowns = [(req["code"], c) for req in manifest["requirements"] for up in req["uploads"] for c in up["checks"]
                if c["status"] == "unknown" and not c.get("attested")]
    if unknowns:
        out += ["## Not established by local checks", ""]
        for req_code, c in unknowns:
            out.append(f"- {md(req_code)} {md(c['check'])}: {md(c.get('detail'))}")
        out.append("")
    if manifest["notes"]:
        out += ["## Notes", ""] + [f"- {md(n['message'])}" for n in manifest["notes"]] + [""]
    out += ["---", "Submission Desk checks local readiness only. It does not log in, upload, sign or submit, and it cannot know whether "
            "the institution will accept the application."]
    return "\n".join(out) + "\n"


def export(desk, actor: Actor, ws_ref: str | None) -> dict:
    r = desk.recheck(actor, ws_ref)
    ws = r["workspace"]
    generated = datetime.now(timezone.utc).isoformat(timespec="seconds")
    manifest = build_manifest(r, generated)
    base = desk.data_dir() / "exports"
    base.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    final = base / f"{stamp}-{ws['id'][:8]}"
    partial = base / f".{final.name}.partial"
    partial.mkdir(mode=0o700)
    try:
        _write(partial / "manifest.json", dumps(manifest))  # provisional, rewritten below
        changed = _revalidate(ws, manifest)
        manifest["freshness"]["revalidated_after_writing"] = not changed
        manifest["freshness"]["changed_during_export"] = changed
        _write(partial / "manifest.json", _pretty(manifest))
        _write(partial / "checklist.md", render_checklist(r, manifest))
        os.replace(partial, final)
    except BaseException:
        for child in partial.glob("*"):
            child.unlink(missing_ok=True)
        partial.rmdir()
        raise
    with desk.store.tx(write=True) as conn:
        record_event(conn, workspace_id=ws["id"], actor=actor.to_dict(), type="exported", subject=final.name,
                     payload={"revalidated": not changed, "changed": changed, "files": len(manifest["files"])}, now=desk.now())
    return {
        "ok": True, "directory": str(final), "checklist": str(final / "checklist.md"), "manifest": str(final / "manifest.json"),
        "revalidated": not changed, "changed_during_export": changed, "files": len(manifest["files"]),
        "headline": manifest["headline"], "verdict": manifest["verdict"],
        "note": "Snapshot only: some files changed during export; recheck and export again." if changed else
                "Every listed file revision was re-verified after writing.",
    }


def _revalidate(ws: dict, manifest: dict) -> list[str]:
    changed: list[str] = []
    try:
        root = files.resolve_root(ws["material_root"])
    except ValueError:
        return [f["rel_path"] for f in manifest["files"]]
    for entry in manifest["files"]:
        read = files.read_with_retry(root, entry["rel_path"])
        if not isinstance(read, files.Snapshot) or read.sha256 != entry["sha256"]:
            changed.append(entry["rel_path"])
    return changed


def _pretty(manifest: dict) -> str:
    import json

    return json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=False, default=str)


def _write(path: Path, content: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


__all__ = ["export", "build_manifest", "render_checklist", "md", "short"]
