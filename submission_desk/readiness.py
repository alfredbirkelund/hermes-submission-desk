"""Derive readiness from current evidence. Pure: no I/O, no clock reads, no stored flags.

The verdict is recomputed on every read from the ledger snapshot the service loads. A model
cannot set "ready"; it can only add proposals, which count against readiness until the
applicant confirms them.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import rules as rules_mod

READY = "ready"
MISSING = "missing"
FAILED = "failed"
UNKNOWN = "unknown"
CONFLICTING = "conflicting"
STALE = "stale"
HUMAN_ACTION = "human_action"
AWAITING_CONFIRMATION = "awaiting_confirmation"
AWAITING_REVIEW = "awaiting_review"
EXCLUDED = "excluded"

SEVERITY = [CONFLICTING, FAILED, MISSING, STALE, UNKNOWN, AWAITING_CONFIRMATION, AWAITING_REVIEW, HUMAN_ACTION, READY]
STATE_LABELS = {
    READY: "No local blocker",
    MISSING: "Missing",
    FAILED: "Failed",
    UNKNOWN: "Unknown",
    CONFLICTING: "Conflicting",
    STALE: "Stale",
    HUMAN_ACTION: "Your action",
    AWAITING_CONFIRMATION: "Awaiting your confirmation",
    AWAITING_REVIEW: "Awaiting your review",
    EXCLUDED: "Excluded",
}
RESPONSIBILITY = (
    "Local checks only. You still sign, upload and submit in the portal yourself, and passing these "
    "checks does not mean the institution will accept the application."
)


def _reason(state: str, code: str, message: str, next_step: str = "") -> dict:
    return {"state": state, "code": code, "message": message, "next": next_step}


def _primary(states: list[str]) -> str:
    for state in SEVERITY:
        if state in states:
            return state
    return READY


def short(sha: str | None) -> str:
    return (sha or "")[:10]


def freshness_line(*, fresh: bool, checked_at: str | None, unread: list[str] | None) -> str:
    """One sentence on how current the result is. It never claims a file was read when it was not."""
    if not fresh:
        return f"Snapshot from {checked_at or 'never'}: files have not been re-read since. Recheck to update."
    if unread:
        names = ", ".join(unread)
        return (f"Rechecked just now, but {len(unread)} file{'s' if len(unread) != 1 else ''} could not be read ({names}); "
                "results for {} are not current.".format("them" if len(unread) != 1 else "it"))
    return "Current: every mapped file was re-read and hashed just now."


def derive(state: dict, *, now: datetime, fresh: bool, checked_at: str | None, unread: list[str] | None = None) -> dict:
    fields = state["fields"]
    documents = state["documents"]
    results = state["results"]
    reviews = state["reviews"]
    attestations = state["attestations"]
    conflicts = [c for c in state["conflicts"] if c["status"] == "open"]
    req_by_id = {r["id"]: r for r in state["requirements"]}

    items: list[dict] = []
    proposals: list[dict] = []
    for req in state["requirements"]:
        row = _requirement_row(req, state, fields, documents, results, reviews, attestations, conflicts, req_by_id, now)
        items.append(row)
        if req.get("pending"):
            proposals.append({"kind": "requirement", "code": req["code"], "id": req["id"],
                              "version": req["pending"]["version"], "statement": req["pending"]["statement"]})

    actions = [_action_row(a, documents, attestations) for a in state["actions"]]
    for action in actions:
        if action["status"] == "proposed":
            proposals.append({"kind": "action", "code": action["code"], "id": action["id"],
                              "version": action["version"], "statement": action["description"]})

    notes = _notes(state, documents)
    counts: dict[str, int] = {}
    for row in items:
        counts[row["state"]] = counts.get(row["state"], 0) + 1
    open_actions = [a for a in actions if a["state"] not in (READY, EXCLUDED)]
    blockers = sum(n for s, n in counts.items() if s not in (READY, EXCLUDED)) + len(open_actions) + len(conflicts)
    active = [r for r in items if r["state"] != EXCLUDED]
    if not active and not actions:
        verdict, headline = "empty", "No requirements yet. Add the instructions and propose or enter requirements."
    elif blockers:
        parts = [f"{n} {STATE_LABELS[s].lower()}" for s, n in sorted(counts.items(), key=lambda kv: SEVERITY.index(kv[0]) if kv[0] in SEVERITY else 99)
                 if s not in (READY, EXCLUDED)]
        if open_actions:
            parts.append(f"{len(open_actions)} open action{'s' if len(open_actions) != 1 else ''}")
        if conflicts:
            parts.append(f"{len(conflicts)} open conflict{'s' if len(conflicts) != 1 else ''}")
        verdict, headline = "blocked", (f"{blockers} items need attention: " if blockers != 1 else "1 item needs attention: ") + ", ".join(parts) + "."
    else:
        verdict = "no_local_blockers"
        headline = "No blockers found by the checks that ran. Unchecked parts are listed; signing and submitting stay with you."
    ws_row = state.get("workspace") or {}
    if ws_row and not ws_row.get("folder_confirmed_at"):
        folder_first = (f"Confirm the folder first: the agent chose {ws_row.get('material_root')}, and nothing in it is read "
                        "until you confirm it holds your application files.")
        headline = folder_first + (" " + headline if verdict == "blocked" else "")
        verdict = "blocked"
        blockers += 1
    return {
        "workspace": state["workspace"],
        "fresh": fresh,
        "checked_at": checked_at,
        "derived_at": now.isoformat(timespec="seconds"),
        "freshness": freshness_line(fresh=fresh, checked_at=checked_at, unread=unread),
        "summary": {"verdict": verdict, "headline": headline, "counts": counts, "blockers": blockers},
        "items": items,
        "actions": actions,
        "conflicts": [_conflict_row(c, req_by_id) for c in state["conflicts"]],
        "proposals": proposals,
        "notes": notes,
        "responsibility": RESPONSIBILITY,
    }


def _version_view(version: dict | None, sources: dict) -> dict | None:
    if version is None:
        return None
    rules = version.get("rules") or {}
    view = {
        "id": version["id"], "version": version["version"], "statement": version["statement"],
        "kind": version["kind"], "rules": rules, "rules_text": rules_mod.describe(rules),
        "status": version["status"], "origin": version["origin"], "created_at": version["created_at"],
        "created_by": version.get("created_by_label", ""), "decided_by": version.get("decided_by_label", ""),
        "decided_at": version.get("decided_at"), "excerpt": version.get("excerpt"),
        "locator": version.get("locator"), "source": None,
    }
    rev_id = version.get("source_revision_id")
    if rev_id and rev_id in sources:
        src = sources[rev_id]
        view["source"] = {"code": src["code"], "label": src["label"], "revision": src["revision"],
                          "captured_at": src["captured_at"], "origin_note": src.get("origin_note"),
                          "added_by": src.get("added_by", "")}
    return view


def _requirement_row(req, state, fields, documents, results, reviews, attestations, conflicts, req_by_id, now) -> dict:
    effective = req.get("effective")
    pending = req.get("pending")
    latest = req["latest"]
    view = _version_view(effective or latest, state["source_revisions"])
    row = {"id": req["id"], "code": req["code"], "requirement": view,
           "pending": _version_view(pending, state["source_revisions"]) if pending and effective else None,
           "mappings": [], "reasons": [], "states": []}
    reasons: list[dict] = row["reasons"]
    if effective is None:
        if latest["status"] in ("rejected", "withdrawn"):
            row["state"] = EXCLUDED
            row["states"] = [EXCLUDED]
            reasons.append(_reason(EXCLUDED, latest["status"], f"{latest['status'].capitalize()} by you."))
            return row
        reasons.append(_reason(AWAITING_CONFIRMATION, "unconfirmed",
                               "Proposed by the agent; not authoritative until you confirm it.",
                               f"Read the quoted source and confirm, correct or reject {req['code']}."))
        for mapping in [m for m in state["mappings"] if m["requirement_id"] == req["id"]]:
            row["mappings"].append(_mapping_row(mapping, None, fields, documents, results, reviews, attestations, evaluate=False))
        row["states"] = [AWAITING_CONFIRMATION]
        row["state"] = AWAITING_CONFIRMATION
        return row
    if effective["status"] == "withdrawn":
        if pending:
            # An agent revision of a withdrawn requirement is still a proposal the applicant must see and decide.
            row["state"] = AWAITING_CONFIRMATION
            row["states"] = [AWAITING_CONFIRMATION]
            reasons.append(_reason(AWAITING_CONFIRMATION, "revision_pending",
                                   f"You marked {req['code']} not applicable; a revised version (v{pending['version']}) is proposed "
                                   "and does not apply unless you confirm it.",
                                   "Confirm or reject the revision."))
            return row
        row["state"] = EXCLUDED
        row["states"] = [EXCLUDED]
        reasons.append(_reason(EXCLUDED, "withdrawn", "Marked not applicable by you."))
        return row
    if pending:
        reasons.append(_reason(AWAITING_CONFIRMATION, "revision_pending",
                               f"A revised version (v{pending['version']}) is proposed; v{effective['version']} still applies.",
                               "Confirm or reject the revision."))
    if not effective.get("excerpt"):
        row["no_source_evidence"] = True
    drift = state["source_drift"].get(effective["id"])
    if drift and drift.get("unreadable"):
        reasons.append(_reason(STALE, "source_unreadable",
                               f"The instructions ({drift['source']}) changed (revision {drift['revision']}, captured {drift['captured_at']}) "
                               f"and the new version cannot be read as text ({drift['detail']}), so the quote cannot be checked.",
                               "Save a readable copy of the instructions, recheck, and confirm or correct this requirement."))
    elif drift and not drift["found"]:
        reasons.append(_reason(STALE, "source_changed",
                               f"The source changed (revision {drift['revision']}, captured {drift['captured_at']}) and no longer contains the quoted text.",
                               "Re-read the source and confirm or correct this requirement."))
    for conflict in conflicts:
        if req["id"] in conflict["requirement_ids"]:
            others = [req_by_id[r]["code"] for r in conflict["requirement_ids"] if r != req["id"] and r in req_by_id]
            with_text = f" with {', '.join(others)}" if others else ""
            reasons.append(_reason(CONFLICTING, "conflict", f"Conflict {conflict['code']}{with_text}: {conflict['description']}",
                                   "Decide which instruction governs (both stay visible)."))
    kind = effective["kind"]
    if kind in ("document", "text"):
        mappings = [m for m in state["mappings"] if m["requirement_id"] == req["id"] and m["current"]["status"] == "active"]
        if not mappings:
            reasons.append(_reason(MISSING, "unmapped", "Not mapped to a portal field and file.",
                                   "Map it to the exact portal field and choose the file."))
        for mapping in mappings:
            mrow = _mapping_row(mapping, effective, fields, documents, results, reviews, attestations, evaluate=True)
            row["mappings"].append(mrow)
            for reason in mrow["reasons"]:
                reasons.append({**reason, "mapping": mapping["code"]})
    elif kind == "deadline":
        reasons.extend(_deadline_reasons(effective.get("rules") or {}, now))
    else:
        att = _current_requirement_attestation(effective, attestations, documents)
        if att is None:
            reasons.append(_reason(HUMAN_ACTION, "needs_attestation",
                                   "Only you can confirm this; the plugin never infers it from documents.",
                                   f"When it is true or done, mark {req['code']} done."))
        elif att["stale"]:
            reasons.append(_reason(STALE, "attestation_stale", att["stale"], f"Re-confirm {req['code']}."))
        else:
            row["attested"] = att["view"]
    for action in state["actions"]:
        eff = action.get("effective")
        if eff and eff.get("requirement_id") == req["id"] and eff["status"] == "confirmed":
            done = _current_action_attestation(action, attestations, documents)
            if done is None:
                reasons.append(_reason(HUMAN_ACTION, "open_action", f"Open action {action['code']}: {eff['description']}",
                                       f"Do it, then mark {action['code']} done."))
            elif done["stale"]:
                reasons.append(_reason(STALE, "action_stale", f"{action['code']}: {done['stale']}", f"Re-confirm {action['code']}."))
    row["states"] = sorted({r["state"] for r in reasons}, key=SEVERITY.index) or [READY]
    row["state"] = _primary(row["states"])
    return row


def _mapping_row(mapping, effective, fields, documents, results, reviews, attestations, *, evaluate: bool) -> dict:
    current = mapping["current"]
    field = fields.get(current.get("field_id")) if current.get("field_id") else None
    doc = documents.get(current.get("document_id")) if current.get("document_id") else None
    obs = doc["observation"] if doc else None
    mrow = {
        "id": mapping["id"], "code": mapping["code"], "version": current["version"], "version_id": current["id"],
        "field": {"id": field["id"], "code": field["code"], "label": field["label"], "version": field["version"],
                  "kind": field["kind"]} if field else None,
        "document": None, "checks": [], "review": None, "reasons": [], "state": READY,
        "created_by": current.get("created_by_label", ""),
    }
    if doc:
        mrow["document"] = {
            "id": doc["id"], "code": doc["code"], "label": doc["label"], "rel_path": doc["rel_path"],
            "status": obs["status"] if obs else "unchecked", "sha256": obs.get("sha256") if obs else None,
            "size": obs.get("byte_size") if obs else None, "observed_at": obs.get("observed_at") if obs else None,
            "verified_at": obs.get("verified_at") if obs else None, "detail": obs.get("detail") if obs else None,
        }
    reasons: list[dict] = mrow["reasons"]
    if not evaluate:
        return mrow
    if field is None:
        reasons.append(_reason(MISSING, "no_field", "No portal field chosen.", "Pick the exact portal field this goes into."))
    if doc is None:
        reasons.append(_reason(MISSING, "no_document", "No file chosen.", "Choose the file to upload."))
    else:
        if obs is None:
            reasons.append(_reason(UNKNOWN, "unchecked", "Not checked yet.", "Recheck."))
        elif obs["status"] in ("missing", "root_missing", "moved_ambiguous"):
            last = f" Last seen as revision {short(obs.get('last_sha256'))}." if obs.get("last_sha256") else ""
            reasons.append(_reason(MISSING, "file_missing", f"{doc['rel_path']}: {obs.get('detail') or 'not found'}.{last}",
                                   "Restore the file or choose its new location."))
        elif obs["status"] in ("outside_root", "invalid_path", "not_regular"):
            reasons.append(_reason(FAILED, "file_refused", f"{doc['rel_path']}: {obs.get('detail')}",
                                   "Choose a regular file inside the material folder."))
        elif obs["status"] != "ok":
            reasons.append(_reason(UNKNOWN, "file_unreadable", f"{doc['rel_path']}: {obs.get('detail')}",
                                   "Make the file readable, then recheck."))
    sha = obs.get("sha256") if obs and obs.get("status") == "ok" else None
    if doc is not None and obs is not None and obs.get("status") != "ok":
        # No bytes, so no content checks: the presence result comes from the observation itself.
        refused = obs["status"] in ("missing", "root_missing", "moved_ambiguous", "outside_root", "invalid_path", "not_regular")
        mrow["checks"].append({
            "check": "presence", "status": "fail" if refused else "unknown",
            "measurement": {"rel_path": obs.get("rel_path"), "problem": obs["status"]}, "limit": None, "units": None,
            "method": "Open the mapped path inside the material folder.", "detail": obs.get("detail") or "",
            "estimate": False, "transient": obs["status"] == "changed_during_read",
            "checked_at": obs.get("verified_at"), "sha256": None,
        })
    if sha and effective is not None:
        key = (current["id"], effective["id"], sha)
        found = results.get(key)
        if found is None:
            reasons.append(_reason(UNKNOWN, "not_checked_for_revision", "Not checked against this file revision yet.", "Recheck."))
        else:
            for result in found:
                view = dict(result)
                # Only a check the plugin could not establish can be vouched for by hand; a failure stays one.
                override = (_check_override(attestations, current["id"], effective["id"], sha, result["check"])
                            if result["status"] == "unknown" else None)
                if override:
                    view["attested"] = override
                mrow["checks"].append(view)
                if result["check"] == "presence" and result["status"] == "pass":
                    continue
                if result["status"] == "fail" and not override:
                    reasons.append(_reason(FAILED, f"check_{result['check']}", f"{result['check'].capitalize()}: {result['detail']}",
                                           _fix_hint(result)))
                elif result["status"] == "unknown" and not override:
                    reasons.append(_reason(UNKNOWN, f"check_{result['check']}", f"{result['check'].capitalize()}: {result['detail']}",
                                           _unknown_hint(result)))
    review_state = _review_state(mapping, current, effective, field, sha, reviews)
    mrow["review"] = review_state
    if doc is not None and field is not None and effective is not None:
        if review_state["state"] == "missing":
            reasons.append(_reason(AWAITING_REVIEW, "unreviewed", "You have not reviewed this file revision for this field.",
                                   "Open the file, check it is the right one, and mark this revision reviewed."))
        elif review_state["state"] == "stale":
            reasons.append(_reason(STALE, "review_stale", "Your review no longer covers what is mapped: " + "; ".join(review_state["stale_because"]) + ".",
                                   "Review the current revision again."))
        elif review_state["state"] == "rejected":
            reasons.append(_reason(FAILED, "review_rejected", "You marked this file as not right for this field.",
                                   "Choose another file or correct the mapping."))
    mrow["states"] = sorted({r["state"] for r in reasons}, key=SEVERITY.index) or [READY]
    mrow["state"] = _primary(mrow["states"])
    return mrow


def _review_state(mapping, current, effective, field, sha, reviews) -> dict:
    history = reviews.get(mapping["id"], [])
    if not history:
        return {"state": "missing", "latest": None, "stale_because": [], "history_count": 0}
    latest = history[0]
    because: list[str] = []
    if latest["mapping_version_id"] != current["id"]:
        because.append(f"the field or file mapping changed (reviewed mapping v{latest.get('mapping_version', '?')}, now v{current['version']})")
    if effective is not None and latest["requirement_version_id"] != effective["id"]:
        because.append(f"the requirement changed (reviewed v{latest.get('requirement_version', '?')}, now v{effective['version']})")
    if field is not None and latest.get("field_version_id") and latest["field_version_id"] != field["version_id"]:
        because.append(f"the portal field label changed (now \u201c{field['label']}\u201d)")
    if sha is None:
        because.append("the file cannot be read now")
    elif latest.get("sha256") != sha:
        because.append(f"the file changed (reviewed revision {short(latest.get('sha256'))}, now {short(sha)})")
    view = {k: latest.get(k) for k in ("id", "decision", "note", "created_at", "sha256", "actor_label")}
    if because:
        return {"state": "stale", "latest": view, "stale_because": because, "history_count": len(history)}
    return {"state": "rejected" if latest["decision"] == "rejected" else "current", "latest": view,
            "stale_because": [], "history_count": len(history)}


def _check_override(attestations, mapping_version_id, requirement_version_id, sha, check) -> dict | None:
    for att in attestations:
        if (att["subject_type"] == "check" and att["mapping_version_id"] == mapping_version_id
                and att["requirement_version_id"] == requirement_version_id and att["sha256"] == sha
                and att["check_name"] == check):
            return {"statement": att["statement"], "by": att.get("actor_label", ""), "at": att["created_at"],
                    "kind": "human attestation (not machine-verified)"}
    return None


def _fix_hint(result: dict) -> str:
    return {
        "format": "Export or save the file in the required format.",
        "size": "Reduce the file size (compress images, export at lower resolution).",
        "pages": "Shorten or split the document to fit the page limit.",
        "words": "Edit the text to fit the word limit, then recheck.",
        "encryption": "Save an unprotected copy of the PDF.",
        "presence": "Restore the file or choose another one.",
    }.get(result["check"], "Fix the file, then recheck.")


def _unknown_hint(result: dict) -> str:
    check = result["check"]
    by_hand = f"or check it yourself and record it with “Verify {check} by hand”."
    cause = result.get("cause")
    if cause == "engine_unavailable":
        return f"Install the plugin's PDF engine (run `hermes pm repair`, restart Hermes, then recheck), {by_hand}"
    if cause == "encrypted_locked":
        return f"Save a copy without a password and recheck, {by_hand}"
    if cause == "parse_error":
        return f"Export the document again as a standard PDF and recheck, {by_hand}"
    if check == "size":
        return "Ask the institution whether MB means 1,000,000 or 1,048,576 bytes, or keep the file under the decimal limit."
    if check == "words":
        return "Count words with the institution's method, then record your count as an attestation."
    if check == "pages":
        return "Open the file and count pages, or export a standard PDF; record your count if needed."
    if result.get("transient"):
        return "Recheck."
    return "Verify this yourself and record it as an attestation, or fix the file."


def _current_requirement_attestation(effective, attestations, documents) -> dict | None:
    for att in attestations:
        if att["subject_type"] != "requirement" or att["subject_id"] != effective["requirement_id"]:
            continue
        view = {"statement": att["statement"], "by": att.get("actor_label", ""), "at": att["created_at"],
                "kind": "human attestation"}
        if att["subject_version_id"] != effective["id"]:
            return {"view": view, "stale": f"You confirmed v{att.get('subject_version', '?')}; the requirement is now v{effective['version']}."}
        stale = _doc_binding_stale(att, documents)
        return {"view": view, "stale": stale}
    return None


def _current_action_attestation(action, attestations, documents) -> dict | None:
    eff = action["effective"]
    for att in attestations:
        if att["subject_type"] != "action" or att["subject_id"] != action["id"]:
            continue
        view = {"statement": att["statement"], "by": att.get("actor_label", ""), "at": att["created_at"],
                "kind": "human attestation"}
        if att["subject_version_id"] != eff["id"]:
            return {"view": view, "stale": f"You marked v{att.get('subject_version', '?')} done; the action is now v{eff['version']}."}
        return {"view": view, "stale": _doc_binding_stale(att, documents)}
    return None


def _doc_binding_stale(att, documents) -> str | None:
    if not att.get("document_id"):
        return None
    doc = documents.get(att["document_id"])
    obs = doc["observation"] if doc else None
    if not obs or obs.get("status") != "ok":
        return "The file you attested about cannot be read now."
    if obs.get("sha256") != att.get("sha256"):
        return (f"You attested about revision {short(att.get('sha256'))} of {doc['label']}; the file is now "
                f"{short(obs.get('sha256'))}. A changed file needs a new confirmation (for example a new signature).")
    return None


def _action_row(action, documents, attestations) -> dict:
    eff = action.get("effective")
    latest = action["latest"]
    view_version = eff or latest
    row = {"id": action["id"], "code": action["code"], "version": view_version["version"],
           "version_id": view_version["id"], "description": view_version["description"],
           "requirement_id": view_version.get("requirement_id"), "document_id": view_version.get("document_id"),
           "status": view_version["status"], "origin": view_version["origin"],
           "created_by": view_version.get("created_by_label", ""), "reasons": []}
    if eff is None:
        if latest["status"] in ("rejected", "withdrawn"):
            row.update(state=EXCLUDED)
            return row
        row.update(state=AWAITING_CONFIRMATION)
        row["reasons"].append(_reason(AWAITING_CONFIRMATION, "unconfirmed", "Proposed by the agent.", f"Confirm or reject {action['code']}."))
        return row
    if eff["status"] == "withdrawn":
        row.update(state=EXCLUDED)
        return row
    done = _current_action_attestation(action, attestations, documents)
    if done is None:
        row.update(state=HUMAN_ACTION)
        row["reasons"].append(_reason(HUMAN_ACTION, "open", "Not done yet.", f"Do it, then mark {action['code']} done."))
    elif done["stale"]:
        row.update(state=STALE, attested=done["view"])
        row["reasons"].append(_reason(STALE, "stale", done["stale"], f"Re-confirm {action['code']}."))
    else:
        row.update(state=READY, attested=done["view"])
    return row


def _conflict_row(conflict, req_by_id) -> dict:
    return {
        "id": conflict["id"], "code": conflict["code"], "status": conflict["status"],
        "description": conflict["description"],
        "requirements": [req_by_id[r]["code"] for r in conflict["requirement_ids"] if r in req_by_id],
        "excerpts": conflict["excerpts"], "resolution": conflict.get("resolution"),
        "governing": req_by_id[conflict["governing_requirement_id"]]["code"]
        if conflict.get("governing_requirement_id") in req_by_id else None,
        "created_by": conflict.get("created_by_label", ""), "resolved_by": conflict.get("resolved_by_label", ""),
    }


def _deadline_reasons(rules: dict, now: datetime) -> list[dict]:
    deadline = rules.get("deadline")
    if not deadline:
        return [_reason(UNKNOWN, "deadline_missing", "No deadline date recorded.", "Add the deadline with date, time and time zone.")]
    local = datetime.fromisoformat(deadline["local"])
    if deadline.get("timezone"):
        due = local.replace(tzinfo=ZoneInfo(deadline["timezone"]))
    elif deadline.get("utc_offset_minutes") is not None:
        due = local.replace(tzinfo=timezone(timedelta(minutes=deadline["utc_offset_minutes"])))
    else:
        return [_reason(UNKNOWN, "deadline_timezone", f"Deadline {deadline['local'].replace('T', ' ')} has no time zone; it cannot be placed in time.",
                        "Find the time zone in the instructions (or ask) and correct the requirement.")]
    if now >= due:
        return [_reason(FAILED, "deadline_passed", f"The deadline passed at {due.isoformat(timespec='minutes')}.", "Contact the institution.")]
    return []


def deadline_info(rules: dict, now: datetime) -> dict | None:
    deadline = rules.get("deadline")
    if not deadline or not (deadline.get("timezone") or deadline.get("utc_offset_minutes") is not None):
        return None
    local = datetime.fromisoformat(deadline["local"])
    tz = ZoneInfo(deadline["timezone"]) if deadline.get("timezone") else timezone(timedelta(minutes=deadline["utc_offset_minutes"]))
    due = local.replace(tzinfo=tz)
    return {"due_utc": due.astimezone(timezone.utc).isoformat(timespec="minutes"),
            "remaining_hours": round((due - now).total_seconds() / 3600, 1)}


def _notes(state: dict, documents: dict) -> list[dict]:
    notes: list[dict] = []
    by_sha: dict[str, list[str]] = {}
    for doc in documents.values():
        obs = doc["observation"]
        if obs and obs.get("status") == "ok" and obs.get("sha256"):
            by_sha.setdefault(obs["sha256"], []).append(doc["code"])
    for sha, codes in by_sha.items():
        if len(codes) > 1:
            notes.append({"code": "duplicate_content",
                          "message": f"{', '.join(sorted(codes))} have identical content (revision {short(sha)}). Each mapping keeps its own review."})
    seen: dict[tuple, str] = {}
    for mapping in state["mappings"]:
        cur = mapping["current"]
        if cur["status"] != "active" or not cur.get("field_id") or not cur.get("document_id"):
            continue
        key = (cur["field_id"], cur["document_id"])
        if key in seen:
            notes.append({"code": "duplicate_attachment",
                          "message": f"{mapping['code']} and {seen[key]} attach the same file to the same portal field."})
        else:
            seen[key] = mapping["code"]
    for doc in documents.values():
        obs = doc["observation"]
        if obs and obs.get("moved_from"):
            notes.append({"code": "moved", "message": f"{doc['code']} moved from {obs['moved_from']} to {obs['rel_path']} (same content)."})
    for src in state.get("source_list") or []:
        if src.get("last_problem"):
            saved = f"revision {src['revision']} saved {src['captured_at']}" if src.get("revision") else "no readable copy"
            notes.append({"code": "source_unreadable",
                          "message": f"Instructions {src['code']} ({src['rel_path']}) could not be read at the last recheck "
                                     f"({src['last_problem_at']}): {src['last_problem']} The saved copy ({saved}) is still used."})
        elif src.get("unreadable_revision"):
            bad = src["unreadable_revision"]
            notes.append({"code": "source_unextractable",
                          "message": f"Instructions {src['code']} ({src['rel_path']}) changed (revision {bad['revision']}) and no text "
                                     f"can be extracted from the new version: {bad['detail'] or bad['status']}. Requirements quoting it are stale."})
    return notes
