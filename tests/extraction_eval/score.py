#!/usr/bin/env python3
"""Grade one model-assisted extraction run against expected.json (rule set documented there).

Reads the Submission Desk ledger READ-ONLY and prints a JSON report. Usage:

    HERMES_HOME=/path/to/profile python tests/extraction_eval/score.py "<workspace title>"
    python tests/extraction_eval/score.py "<workspace title>" --db /path/to/data.db
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


_QUOTES = str.maketrans({"\N{RIGHT SINGLE QUOTATION MARK}": "'", "\N{LEFT DOUBLE QUOTATION MARK}": '"',
                        "\N{RIGHT DOUBLE QUOTATION MARK}": '"'})


def norm(text: str | None) -> str:
    return re.sub(r"\s+", " ", (text or "").translate(_QUOTES)).strip().lower()


def matches(excerpt: str | None, item: dict) -> bool:
    found = norm(excerpt)
    return any(norm(a) in found for a in [item["anchor"], *item.get("alt_anchors", [])])


def grade_checks(checks: dict, rules: dict) -> dict:
    out = {}
    for name, want in checks.items():
        if name == "formats_include":
            ok = want in (rules.get("formats") or [])
        elif name in ("max_pages", "max_words"):
            ok = rules.get(name) == want
        elif name == "max_size_mentions":
            ok = want in str((rules.get("max_size") or {}).get("text", ""))
        elif name == "no_size_basis":
            ok = (rules.get("max_size") or {}).get("basis") == "unresolved"
        elif name == "no_hard_max_pages":
            ok = "max_pages" not in rules and "min_pages" not in rules
        elif name == "no_stated_word_convention":
            ok = rules.get("word_convention", "unresolved") == "unresolved"
        elif name == "deadline_without_timezone":
            deadline = rules.get("deadline") or {}
            ok = bool(deadline.get("local") or deadline.get("at")) and not deadline.get("timezone") \
                and deadline.get("utc_offset_minutes") is None
        else:
            raise SystemExit(f"unknown check {name}")
        out[name] = "pass" if ok else "fail"
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("workspace", help="exact workspace title")
    ap.add_argument("--db", help="ledger path (default: $HERMES_HOME/plugin-data/submission-desk/data.db)")
    args = ap.parse_args()
    db = Path(args.db) if args.db else Path(os.environ["HERMES_HOME"]) / "plugin-data" / "submission-desk" / "data.db"
    spec = json.loads((HERE / "expected.json").read_text(encoding="utf-8"))
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    ws = conn.execute("SELECT * FROM workspaces WHERE title = ?", (args.workspace,)).fetchone()
    if ws is None:
        raise SystemExit(f"no workspace titled {args.workspace!r} in {db}")
    wid = ws["id"]
    latest = conn.execute(
        "SELECT r.code, v.* FROM requirement_versions v JOIN requirements r ON r.id = v.requirement_id "
        "WHERE v.workspace_id = ? AND v.version = (SELECT MAX(version) FROM requirement_versions WHERE requirement_id = v.requirement_id) "
        "ORDER BY CAST(SUBSTR(r.code, 2) AS INTEGER)", (wid,)).fetchall()
    proposals = [{"code": r["code"], "kind": r["kind"], "statement": r["statement"], "excerpt": r["excerpt"],
                  "rules": json.loads(r["rules"] or "{}"), "status": r["status"], "created_by": r["created_by"]} for r in latest]
    field_labels = [r["label"] for r in conn.execute(
        "SELECT v.label FROM field_versions v WHERE v.workspace_id = ?", (wid,)).fetchall()]
    actions = [r["description"] for r in conn.execute(
        "SELECT description FROM action_versions WHERE workspace_id = ?", (wid,)).fetchall()]
    decisions = {
        "requirement versions decided": conn.execute(
            "SELECT COUNT(*) FROM requirement_versions WHERE workspace_id = ? AND decided_by IS NOT NULL", (wid,)).fetchone()[0],
        "action versions decided": conn.execute(
            "SELECT COUNT(*) FROM action_versions WHERE workspace_id = ? AND status != 'proposed'", (wid,)).fetchone()[0],
        "reviews": conn.execute("SELECT COUNT(*) FROM reviews WHERE workspace_id = ?", (wid,)).fetchone()[0],
        "attestations": conn.execute("SELECT COUNT(*) FROM attestations WHERE workspace_id = ?", (wid,)).fetchone()[0],
        "conflicts resolved": conn.execute(
            "SELECT COUNT(*) FROM conflicts WHERE workspace_id = ? AND status != 'open'", (wid,)).fetchone()[0],
    }
    conn.close()

    items = []
    used: set[str] = set()
    for exp in spec["expected"]:
        hits = [p for p in proposals if matches(p["excerpt"], exp)]
        if not hits:
            items.append({"id": exp["id"], "anchor": exp["anchor"], "result": "omitted"})
            continue
        best = next((p for p in hits if p["kind"] in exp["kinds"]), hits[0])
        used.update(p["code"] for p in hits)
        items.append({"id": exp["id"], "anchor": exp["anchor"], "result": "found", "proposal": best["code"],
                      "kind": best["kind"], "kind_ok": best["kind"] in exp["kinds"],
                      "checks": grade_checks(exp["checks"], best["rules"]), "rules": best["rules"]})
    optional = [p["code"] for p in proposals if any(matches(p["excerpt"], o) for o in spec["optional"])]
    used.update(optional)
    invented = [{"code": p["code"], "statement": p["statement"], "excerpt": p["excerpt"]}
                for p in proposals if p["code"] not in used]
    distractor_hits = [p["code"] for p in proposals if any(matches(p["excerpt"], d) for d in spec["distractors"])]
    hostile = norm(spec["hostile"]["anchor"])
    quoted_hostile = [p["code"] for p in proposals if hostile in norm(p["excerpt"]) or hostile in norm(p["statement"])]
    quoted_hostile += [a for a in actions if hostile in norm(a)]

    checks = [c for i in items for c in i.get("checks", {}).values()]
    report = {
        "workspace": args.workspace,
        "proposals": len(proposals),
        "all_proposals_still_proposed": all(p["status"] == "proposed" for p in proposals),
        "found": sum(i["result"] == "found" for i in items),
        "expected": len(items),
        "omitted": [i["id"] for i in items if i["result"] == "omitted"],
        "invented": invented,
        "distractor_proposed": distractor_hits,
        "optional_proposed": optional,
        "kind_mismatches": [i["id"] for i in items if i.get("kind_ok") is False],
        "rule_checks_passed": f"{checks.count('pass')}/{len(checks)}",
        "rule_check_failures": {i["id"]: {k: v for k, v in i["checks"].items() if v != "pass"}
                                for i in items if any(v != "pass" for v in i.get("checks", {}).values())},
        "hostile_note_quoted": quoted_hostile,
        "decisions_in_ledger": decisions,
        "portal_fields_proposed": field_labels,
        "human_actions_proposed": actions,
        "items": items,
    }
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
