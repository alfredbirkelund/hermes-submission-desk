"""Human surfaces in chat and terminal.

``/submission-desk`` runs in the session the applicant typed it in. Every decision (confirm,
reject, review, done, verify, resolve, delete) shows the exact version or file revision first and
then asks through the host's own choice picker (``ctx.ask_choice``). When no person can answer
(cron, a hook, a non-interactive run), the picker returns nothing and the decision is refused.

``hermes submission-desk`` is read-only plus export; it never records a decision.
"""

from __future__ import annotations

import json
import shlex

from . import actors, export as export_mod, textfmt
from .errors import ConfirmationRequired, DeskError
from .readiness import short
from .service import Desk

COMMAND = "submission-desk"
LOCAL_PLATFORMS = {"cli", "tui", "desktop", "local", ""}

HELP = """/submission-desk: application readiness (local checks only; you submit in the portal)

  /submission-desk [status]          recheck files and show what is missing, failed, unknown, stale or yours to do
  /submission-desk details           the same with every measurement and quoted instruction
  /submission-desk list              workspaces
  /submission-desk folder [confirm]  show the application folder; confirm one the agent chose
  /submission-desk pending           proposals waiting for your confirmation
  /submission-desk confirm R3        confirm a proposed requirement (or action A2) after reading it
  /submission-desk reject R3         reject a proposal
  /submission-desk review M2         mark the mapped file revision as the right one for its portal field
  /submission-desk done A1 [note]    record that you did an action (or met requirement R5)
  /submission-desk verify M2 words <what you verified>   vouch for a check the plugin could not establish
  /submission-desk resolve C1 [R2] <how it is resolved>  settle a conflict between instructions
  /submission-desk export            write the hand-back checklist and manifest
  /submission-desk delete            delete this application's records (files are not touched)

Start a command with @<workspace id or title> to pick another workspace."""


def _human(session_id: str, platform: str, source) -> actors.Actor | None:
    user_id = getattr(source, "user_id", "") if source is not None else ""
    if platform not in LOCAL_PLATFORMS and not user_id:
        return None
    return actors.human("slash", session_id=session_id, platform=platform or "local", user_id=str(user_id or ""))


def make_handler(ctx, desk_factory=Desk):
    def handle(raw_args: str = "", *, session_id: str = "", platform: str = "", source=None) -> str:
        try:
            tokens = shlex.split(raw_args or "")
        except ValueError:
            tokens = (raw_args or "").split()
        ws_ref = None
        if tokens and tokens[0].startswith("@"):
            ws_ref = tokens.pop(0)[1:]
        sub = tokens.pop(0).lower() if tokens else "status"
        desk = desk_factory()
        try:
            return _dispatch(ctx, desk, sub, tokens, ws_ref, session_id, platform, source)
        except DeskError as exc:
            hint = f"\n{exc.hint}" if exc.hint else ""
            return f"Submission Desk: {exc.message}{hint}"

    return handle


def _ask(ctx, question: str, choices: list) -> str | None:
    try:
        return ctx.ask_choice(question, choices)
    except Exception:
        return None


_NO_HUMAN = ("Nothing recorded: this decision needs your answer in the choice picker, and no one could answer here "
             "(non-interactive run, cron or hook). Run the command in your own session or use the Desktop page.")


def _dispatch(ctx, desk: Desk, sub: str, args: list[str], ws_ref, session_id, platform, source) -> str:
    if sub in ("help", "-h", "--help"):
        return HELP
    if sub == "list":
        rows = desk.list_workspaces()
        if not rows:
            return "No application workspace yet. Ask the agent to create one, or use the Desktop page."
        return "\n".join(f"{r['title']}  ({r['id'][:8]}, folder {r['material_root']}, last check {r['last_check_at'] or 'never'})" for r in rows)
    machine = actors.machine("slash recheck")
    if sub in ("status", "details"):
        return textfmt.text(desk.recheck(machine, ws_ref), verbose=sub == "details")
    if sub == "pending":
        r = desk.readiness(ws_ref)
        if not r["proposals"]:
            return "No proposals are waiting for you."
        return "Waiting for your confirmation:\n" + "\n".join(
            f"  {p['code']} v{p['version']}: {p['statement'][:200]}" for p in r["proposals"]) + \
            "\nUse /submission-desk confirm <code> to read and decide each one."
    if sub == "export":
        out = export_mod.export(desk, actors.machine("slash export"), ws_ref)
        return f"{out['headline']}\nChecklist: {out['checklist']}\nManifest: {out['manifest']}\n{out['note']}"
    if sub == "folder" and not args:
        return _folder_text(desk.readiness(ws_ref)["workspace"])
    human = _human(session_id, platform, source)
    if human is None:
        return "Decisions are accepted only from your own session (or an identified chat user)."
    if sub in ("confirm", "reject"):
        return _decide(ctx, desk, human, sub, args, ws_ref)
    if sub == "review":
        return _review(ctx, desk, human, args, ws_ref)
    if sub == "done":
        return _done(ctx, desk, human, args, ws_ref)
    if sub == "verify":
        return _verify(ctx, desk, human, args, ws_ref)
    if sub == "resolve":
        return _resolve(ctx, desk, human, args, ws_ref)
    if sub == "delete":
        return _delete(ctx, desk, human, ws_ref)
    if sub == "folder" and args and args[0].lower() == "confirm":
        return _folder_confirm(ctx, desk, human, ws_ref)
    return f"Unknown subcommand {sub!r}.\n\n{HELP}"


def _folder_text(ws: dict) -> str:
    folder = ws["folder"]
    if folder["confirmed"]:
        return (f"Folder: {folder['path']}\nChosen by {folder['chosen_by']}; confirmed by {folder['confirmed_by']} "
                f"at {folder['confirmed_at']}.")
    return (f"Folder: {folder['path']}\nChosen by {folder['chosen_by']}; not confirmed yet, so nothing in it is read. "
            "Confirm it with /submission-desk folder confirm or on the Desktop page.")


def _folder_confirm(ctx, desk, human, ws_ref) -> str:
    ws = desk.readiness(ws_ref)["workspace"]
    folder = ws["folder"]
    if folder["confirmed"]:
        return _folder_text(ws)
    question = (f"Is {folder['path']} the folder that holds your application files for \u201c{ws['title']}\u201d?\n"
                f"It was chosen by {folder['chosen_by']}. Submission Desk reads only the files added from this folder.")
    answer = _ask(ctx, question, [{"value": "yes", "label": f"Yes, use {folder['path']}"}, {"value": "no", "label": "Cancel"}])
    if answer is None:
        return _NO_HUMAN
    if answer != "yes":
        return "Cancelled; nothing recorded."
    desk.confirm_folder(human, ws_ref, folder["path"], expected_revision=ws["revision"])
    return f"Folder {folder['path']} confirmed by you. Files in it can now be added and checked."


def _find_item(r: dict, code: str):
    code = code.upper()
    for item in r["items"]:
        if item["code"] == code:
            return item
    for action in r["actions"]:
        if action["code"] == code:
            return action
    return None


def _decide(ctx, desk, human, sub, args, ws_ref) -> str:
    if not args:
        return f"Usage: /submission-desk {sub} <R-code or A-code>"
    code = args[0].upper()
    r = desk.readiness(ws_ref)
    item = _find_item(r, code)
    if item is None:
        return f"No requirement or action {code}."
    if code.startswith("A"):
        if item["status"] != "proposed":
            return f"{code} is {item['status']}; nothing to {sub}."
        question = f"{sub.capitalize()} action {code} v{item['version']}?\n\u201c{item['description']}\u201d (proposed by {item['created_by']})"
        answer = _ask(ctx, question, [{"value": "yes", "label": f"{sub.capitalize()} {code} v{item['version']}"},
                                      {"value": "no", "label": "Cancel"}])
        if answer is None:
            return _NO_HUMAN
        if answer != "yes":
            return "Cancelled; nothing recorded."
        desk.decide_action(human, ws_ref, code, item["version"], sub, expected_revision=r["workspace"]["revision"])
        return f"{code} v{item['version']} {sub}ed by you."
    target = item.get("pending") or (item["requirement"] if item["requirement"]["status"] == "proposed" else None)
    if target is None:
        return f"{code} has no proposal waiting (current: v{item['requirement']['version']}, {item['requirement']['status']})."
    src = target.get("source") or {}
    lines = [f"{sub.capitalize()} requirement {code} v{target['version']}?", f"\u201c{target['statement']}\u201d ({target['kind']})"]
    lines += [f"rule: {rule}" for rule in target.get("rules_text") or []]
    if target.get("excerpt"):
        page = f", p. {target['locator'].get('page')}" if target.get("locator") and target["locator"].get("page") else ""
        lines.append(f"Source {src.get('code')} {src.get('label')} (saved {src.get('captured_at')}{page}): \u201c{target['excerpt'][:400]}\u201d")
    answer = _ask(ctx, "\n".join(lines), [{"value": "yes", "label": f"{sub.capitalize()} {code} v{target['version']}"},
                                          {"value": "no", "label": "Cancel"}])
    if answer is None:
        return _NO_HUMAN
    if answer != "yes":
        return "Cancelled; nothing recorded."
    desk.decide_requirement(human, ws_ref, code, target["version"], sub, expected_revision=r["workspace"]["revision"])
    return f"{code} v{target['version']} {sub}ed by you." + (" Recheck to see its file checks." if sub == "confirm" else "")


def _mapping(r: dict, code: str):
    for item in r["items"]:
        for m in item["mappings"]:
            if m["code"] == code.upper():
                return item, m
    return None, None


def _review(ctx, desk, human, args, ws_ref) -> str:
    if not args:
        return "Usage: /submission-desk review <M-code>"
    r = desk.recheck(actors.machine("slash review"), ws_ref)
    item, m = _mapping(r, args[0])
    if m is None:
        return f"No mapping {args[0].upper()}."
    doc, field, req = m.get("document"), m.get("field"), item["requirement"]
    if not doc or not doc.get("sha256") or not field:
        return f"{m['code']} needs a readable file and a portal field before it can be reviewed."
    checks = "; ".join(f"{c['check']} {c['status']}" for c in m["checks"]) or "no checks"
    question = (f"Review {m['code']}: is this the right file for this portal field?\n"
                f"Field: \u201c{field['label']}\u201d\nFile: {doc['rel_path']} (revision {short(doc['sha256'])}, {doc.get('size')} bytes)\n"
                f"Requirement {item['code']} v{req['version']}: {req['statement']}\nChecks: {checks}")
    answer = _ask(ctx, question, [{"value": "accept", "label": f"Yes, revision {short(doc['sha256'])} is right"},
                                  {"value": "reject", "label": "No, wrong file"}, {"value": "cancel", "label": "Cancel"}])
    if answer is None:
        return _NO_HUMAN
    if answer not in ("accept", "reject"):
        return "Cancelled; nothing recorded."
    out = desk.review_mapping(human, ws_ref, m["code"], decision=answer, mapping_version=m["version"],
                              requirement_version=req["version"], sha256=doc["sha256"], expected_revision=r["workspace"]["revision"])
    return f"Review recorded for {m['code']} ({out['review']['decision']}, revision {short(out['review']['sha256'])}). It goes stale if the file, field or requirement changes."


def _done(ctx, desk, human, args, ws_ref) -> str:
    if not args:
        return "Usage: /submission-desk done <A-code or R-code> [note]"
    code = args[0].upper()
    note = " ".join(args[1:]).strip() or "Done."
    r = desk.recheck(actors.machine("slash done"), ws_ref)
    item = _find_item(r, code)
    if item is None:
        return f"No action or requirement {code}."
    if code.startswith("A"):
        version, text = item["version"], item["description"]
        doc_id = item.get("document_id")
    else:
        version, text = item["requirement"]["version"], item["requirement"]["statement"]
        doc_id = None
    doc = next((d for d in r["documents"] if d["id"] == doc_id), None) if doc_id else None
    extra = f"\nThis is tied to {doc['rel_path']} revision {short(doc['sha256'])}; if that file changes, you will be asked again." if doc else ""
    question = f"Record that you did this?\n{code} v{version}: {text}\nYour note: {note}{extra}\n(Saved as your attestation; the plugin does not verify it.)"
    answer = _ask(ctx, question, [{"value": "yes", "label": f"Yes, record {code} as done"}, {"value": "no", "label": "Cancel"}])
    if answer is None:
        return _NO_HUMAN
    if answer != "yes":
        return "Cancelled; nothing recorded."
    desk.attest(human, ws_ref, code, version=version, statement=note, sha256=doc["sha256"] if doc else None,
                expected_revision=r["workspace"]["revision"])
    return f"{code} recorded as done by you (attestation, not verification)."


def _verify(ctx, desk, human, args, ws_ref) -> str:
    if len(args) < 3:
        return "Usage: /submission-desk verify <M-code> <format|size|pages|words|encryption> <what you verified>"
    r = desk.recheck(actors.machine("slash verify"), ws_ref)
    item, m = _mapping(r, args[0])
    if m is None:
        return f"No mapping {args[0].upper()}."
    check = args[1].lower()
    statement = " ".join(args[2:])
    result = next((c for c in m["checks"] if c["check"] == check), None)
    if result is None:
        return f"{m['code']} has no {check} check."
    doc = m["document"]
    question = (f"Vouch for the {check} check of {m['code']} yourself?\nPlugin result: {result['status']}: {result.get('detail')}\n"
                f"File revision {short(doc['sha256'])}. Your statement: {statement}\n(Shown as verified by you, not by the plugin; it goes stale if the file changes.)")
    answer = _ask(ctx, question, [{"value": "yes", "label": "Yes, record my verification"}, {"value": "no", "label": "Cancel"}])
    if answer is None:
        return _NO_HUMAN
    if answer != "yes":
        return "Cancelled; nothing recorded."
    desk.attest(human, ws_ref, item["code"], version=item["requirement"]["version"], statement=statement, check=check,
                mapping=m["code"], sha256=doc["sha256"], expected_revision=r["workspace"]["revision"])
    return f"Your verification of {check} for {m['code']} is recorded."


def _resolve(ctx, desk, human, args, ws_ref) -> str:
    if len(args) < 2:
        return "Usage: /submission-desk resolve <C-code> [governing R-code] <how it is resolved>"
    code = args[0].upper()
    governing = args[1].upper() if len(args) > 2 and args[1].upper().startswith("R") and args[1][1:].isdigit() else None
    resolution = " ".join(args[2:] if governing else args[1:])
    r = desk.readiness(ws_ref)
    conflict = next((c for c in r["conflicts"] if c["code"] == code), None)
    if conflict is None:
        return f"No conflict {code}."
    excerpts = "\n".join(f"  {e['source']}: \u201c{e['excerpt'][:200]}\u201d" for e in conflict["excerpts"])
    question = (f"Resolve {code}: {conflict['description']}\n{excerpts}\n"
                f"{'Governing: ' + governing if governing else 'No single governing requirement'}. Resolution: {resolution}")
    answer = _ask(ctx, question, [{"value": "yes", "label": f"Resolve {code}"}, {"value": "no", "label": "Cancel"}])
    if answer is None:
        return _NO_HUMAN
    if answer != "yes":
        return "Cancelled; nothing recorded."
    desk.resolve_conflict(human, ws_ref, code, resolution=resolution, governing=governing, expected_revision=r["workspace"]["revision"])
    return f"{code} resolved by you. Both quoted instructions stay in the history."


def _delete(ctx, desk, human, ws_ref) -> str:
    try:
        desk.delete_workspace(human, ws_ref, confirm_token=None)
    except ConfirmationRequired as exc:
        token = exc.details["confirm_token"]
        answer = _ask(ctx, exc.message, [{"value": "delete", "label": f"Delete \u201c{exc.details['title']}\u201d records"},
                                         {"value": "keep", "label": "Keep everything"}])
        if answer is None:
            return _NO_HUMAN
        if answer != "delete":
            return "Kept; nothing deleted."
        out = desk.delete_workspace(human, ws_ref, confirm_token=token)
        return f"Deleted the Submission Desk records of \u201c{out['title']}\u201d. Your files on disk were not touched."
    return "Nothing deleted."


# ------------------------------------------------------------------ CLI (read-only + export)


def setup_cli(parser) -> None:
    subs = parser.add_subparsers(dest="sd_command")
    subs.add_parser("list", help="List application workspaces")
    status = subs.add_parser("status", help="Recheck files and print readiness")
    status.add_argument("--workspace", "-w", default=None)
    status.add_argument("--json", action="store_true", help="Print the full readiness as JSON")
    status.add_argument("--no-recheck", action="store_true", help="Print the last snapshot without re-reading files")
    status.add_argument("--details", action="store_true")
    exp = subs.add_parser("export", help="Write the hand-back checklist and manifest")
    exp.add_argument("--workspace", "-w", default=None)
    parser.set_defaults(func=run_cli)


def run_cli(args) -> int:
    desk = Desk()
    command = getattr(args, "sd_command", None) or "list"
    trigger = actors.Actor("machine", "cli", detail="hermes submission-desk")
    try:
        if command == "list":
            rows = desk.list_workspaces()
            if not rows:
                print("No application workspace yet. Ask the agent to create one, or use the Desktop page.")
            for row in rows:
                print(f"{row['id']}  {row['title']}  ({row['material_root']})  last check: {row['last_check_at'] or 'never'}")
            return 0
        if command == "status":
            r = desk.readiness(args.workspace) if args.no_recheck else desk.recheck(trigger, args.workspace)
            print(json.dumps(r, ensure_ascii=False, indent=2, default=str) if args.json else textfmt.text(r, verbose=args.details))
            return 0 if r["summary"]["verdict"] == "no_local_blockers" else 2
        if command == "export":
            out = export_mod.export(desk, trigger, args.workspace)
            print(json.dumps(out, ensure_ascii=False, indent=2))
            return 0
    except DeskError as exc:
        print(f"submission-desk: {exc.message}" + (f"\n{exc.hint}" if exc.hint else ""))
        return 1
    print("usage: hermes submission-desk {list,status,export}")
    return 1


def register(ctx) -> None:
    ctx.register_command(COMMAND, make_handler(ctx), description="Application readiness: what goes where, what is stale, what is yours to do",
                         args_hint="[status|details|list|folder|pending|confirm|reject|review|done|verify|resolve|export|delete|help]")
    ctx.register_cli_command(name=COMMAND, help="Submission Desk: application readiness (read-only and export)",
                             setup_fn=setup_cli, handler_fn=run_cli)


__all__ = ["register", "make_handler", "HELP"]
