"""Render readiness for agents (compact JSON) and for people (plain text).

Agent output never contains document text, only metadata, measurements and the quoted
instruction excerpts the applicant selected as sources.
"""

from __future__ import annotations

from .readiness import STATE_LABELS, short

_MAX_ROWS = 40


def _doc_line(doc: dict | None) -> str:
    if not doc:
        return "no file"
    rev = f"rev {short(doc.get('sha256'))}" if doc.get("sha256") else doc.get("status", "")
    size = f", {doc['size']:,} B" if doc.get("size") is not None else ""
    return f"{doc['code']} {doc['rel_path']} ({rev}{size})"


def compact(r: dict, *, full: bool = False) -> dict:
    rows = []
    for item in r["items"]:
        if item["state"] == "excluded":
            continue
        if not full and item["state"] == "ready":
            continue
        req = item["requirement"] or {}
        row = {
            "code": item["code"], "state": item["state"], "kind": req.get("kind"),
            "statement": (req.get("statement") or "")[:240], "version": req.get("version"),
            "status": req.get("status"),
            "reasons": [f"{x['message']} -> {x['next']}" if x.get("next") else x["message"] for x in item["reasons"]][:6],
            "mappings": [],
        }
        for m in item["mappings"]:
            mrow = {"code": m["code"], "state": m["state"], "field": (m.get("field") or {}).get("label"),
                    "file": _doc_line(m.get("document")), "review": (m.get("review") or {}).get("state")}
            if full:
                mrow["checks"] = [{k: c.get(k) for k in ("check", "status", "measurement", "limit", "units", "method", "estimate")}
                                  for c in m["checks"]]
            row["mappings"].append(mrow)
        rows.append(row)
    omitted = max(0, len(rows) - _MAX_ROWS)
    return {
        "workspace": {k: r["workspace"].get(k) for k in ("id", "title", "material_root", "revision", "folder")},
        "fresh": r["fresh"], "freshness": r["freshness"], "checked_at": r["checked_at"],
        "headline": r["summary"]["headline"], "verdict": r["summary"]["verdict"], "counts": r["summary"]["counts"],
        "items": rows[:_MAX_ROWS], "omitted_items": omitted,
        "proposals_awaiting_applicant": [f"{p['code']} v{p['version']}: {p['statement'][:140]}" for p in r["proposals"]],
        "actions": [{"code": a["code"], "state": a["state"], "description": a["description"][:200]}
                    for a in r["actions"] if a["state"] != "excluded"],
        "open_conflicts": [{"code": c["code"], "requirements": c["requirements"], "description": c["description"][:240]}
                           for c in r["conflicts"] if c["status"] == "open"],
        "notes": [n["message"] for n in r["notes"]],
        "responsibility": r["responsibility"],
        "applicant_decides": "Only the applicant confirms requirements, reviews files and attests actions "
                             "(Desktop page or /submission-desk). Proposals never count as confirmed.",
    }


def text(r: dict, *, verbose: bool = False) -> str:
    ws = r["workspace"]
    lines = [f"Submission Desk: {ws.get('title')}", f"Folder: {ws.get('material_root')}", r["freshness"],
             r["summary"]["headline"], ""]
    for item in r["items"]:
        req = item["requirement"] or {}
        if item["state"] == "excluded" and not verbose:
            continue
        label = STATE_LABELS.get(item["state"], item["state"])
        lines.append(f"[{label}] {item['code']} v{req.get('version')} ({req.get('kind')}): {req.get('statement', '')}")
        for rule in req.get("rules_text") or []:
            lines.append(f"    rule: {rule}")
        if req.get("excerpt") and verbose:
            src = req.get("source") or {}
            lines.append(f"    source {src.get('code')} \u201c{req['excerpt'][:200]}\u201d (captured {src.get('captured_at', '?')})")
        for m in item["mappings"]:
            field = (m.get("field") or {}).get("label") or "no portal field"
            lines.append(f"    {m['code']}: \u201c{field}\u201d <- {_doc_line(m.get('document'))}; review: {(m.get('review') or {}).get('state')}")
            if verbose:
                for c in m["checks"]:
                    est = " (estimate)" if c.get("estimate") else ""
                    att = " [verified by you]" if c.get("attested") else ""
                    lines.append(f"      {c['check']}: {c['status']}{est}{att} - {c.get('detail', '')}")
        for reason in item["reasons"]:
            nxt = f" Next: {reason['next']}" if reason.get("next") else ""
            lines.append(f"    - {reason['message']}{nxt}")
    actions = [a for a in r["actions"] if a["state"] != "excluded"]
    if actions:
        lines += ["", "Your actions:"]
        for a in actions:
            lines.append(f"  [{STATE_LABELS.get(a['state'], a['state'])}] {a['code']} v{a['version']}: {a['description']}")
    conflicts = [c for c in r["conflicts"] if c["status"] == "open"]
    if conflicts:
        lines += ["", "Open conflicts:"]
        for c in conflicts:
            lines.append(f"  {c['code']} ({', '.join(c['requirements'])}): {c['description']}")
            for ex in c["excerpts"]:
                lines.append(f"     {ex['source']}: \u201c{ex['excerpt'][:160]}\u201d")
    if r["proposals"]:
        lines += ["", "Awaiting your confirmation:"]
        for p in r["proposals"]:
            lines.append(f"  {p['code']} v{p['version']}: {p['statement'][:160]}")
    if r["notes"]:
        lines += ["", "Notes:"] + [f"  - {n['message']}" for n in r["notes"]]
    lines += ["", r["responsibility"]]
    return "\n".join(lines)
