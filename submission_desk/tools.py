"""Agent-facing tools. Every call runs as an ``agent`` actor.

The tools can create a workspace, add sources and documents, propose requirements, fields,
mappings, actions and conflicts, run checks and export. They cannot confirm, review, attest or
delete, and they refuse arguments that try to claim a human decision (for example
``human_confirmed`` or ``status: confirmed``).
"""

from __future__ import annotations

import json
from typing import Any

from . import actors, export as export_mod, textfmt
from .errors import DeskError, HumanOnly, Invalid
from .service import Desk

TOOLSET = "submission_desk"

# Keys that would let a caller claim a decision the applicant did not make.
FORGED_KEYS = {
    "status", "confirmed", "human_confirmed", "user_confirmed", "approved", "approval", "actor", "decided_by",
    "decision", "attested", "attestation", "verified", "reviewed", "confirmation", "by", "on_behalf_of", "human",
}

_REQ_RULES_HELP = (
    "Only rules the excerpt states. Keys: formats [pdf, png, jpeg, docx, txt]; max_size ('5 MB'; use basis only if "
    "stated); min_pages/max_pages; min_words/max_words with word_convention (whitespace_tokens | letters_digits_tokens | "
    "unresolved when not stated) and word_exclusions (e.g. ['references']); forbid_encryption; deadline "
    "{at: '2027-03-15T23:59', timezone: IANA name, omitted if the source does not state it}."
)

WORKSPACE_SCHEMA = {
    "name": "submission_desk_workspace",
    "description": (
        "Submission Desk (application readiness). Set up and inspect the workspace for ONE application (grant, "
        "scholarship, university). Actions: create (title + absolute material_root folder holding the application files; "
        "the applicant must confirm a folder you choose before its files are read), "
        "add (instruction sources as files or pasted portal text, and documents to check), list, read_source (read a "
        "source's saved text so you can quote it verbatim). Use it only when the user is working on an application."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["create", "add", "list", "read_source"]},
            "workspace": {"type": "string", "description": "Workspace id or exact title; optional when only one exists."},
            "title": {"type": "string", "description": "create: application name."},
            "material_root": {"type": "string", "description": "create: absolute folder path on this machine."},
            "timezone": {"type": "string", "description": "create: applicant's IANA time zone, e.g. Europe/Copenhagen."},
            "source_files": {"type": "array", "items": {"type": "string"},
                             "description": "Instruction files inside the folder (.txt, .md, .html, .pdf)."},
            "source_texts": {
                "type": "array",
                "description": "Pasted instruction text, e.g. the portal's upload page.",
                "items": {"type": "object", "properties": {
                    "label": {"type": "string"}, "text": {"type": "string"},
                    "origin": {"type": "string", "description": "Where it came from (URL or description; never fetched)."}},
                    "required": ["label", "text"]},
            },
            "documents": {"type": "array", "items": {"type": "string"}, "description": "Files to check, inside the folder."},
            "source": {"type": "string", "description": "read_source: source code such as S1."},
            "offset": {"type": "integer", "description": "read_source: character offset for the next chunk."},
        },
        "required": ["action"],
    },
}

PROPOSE_SCHEMA = {
    "name": "submission_desk_propose",
    "description": (
        "Submission Desk: propose requirements, exact portal fields, file mappings, human actions (signatures, official "
        "documents) and conflicts between instructions. Every requirement must quote its instruction VERBATIM from an "
        "added source (excerpt + source code); quotes not found in the saved source are rejected. Proposals wait for the "
        "applicant's confirmation and never count as confirmed. Link items in one call with ref 'r1' and '#r1'."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "workspace": {"type": "string"},
            "requirements": {"type": "array", "items": {"type": "object", "properties": {
                "ref": {"type": "string"},
                "requirement": {"type": "string", "description": "Existing code (e.g. R3) to propose a revised version."},
                "statement": {"type": "string", "description": "The requirement in plain words."},
                "kind": {"type": "string", "enum": ["document", "text", "human_action", "attestation", "deadline", "other"]},
                "source": {"type": "string", "description": "Source code, e.g. S1."},
                "excerpt": {"type": "string", "description": "Exact quote from that source supporting the requirement."},
                "rules": {"type": "object", "description": _REQ_RULES_HELP},
            }, "required": ["statement", "kind", "source", "excerpt"]}},
            "fields": {"type": "array", "items": {"type": "object", "properties": {
                "ref": {"type": "string"},
                "label": {"type": "string", "description": "The portal field's exact label."},
                "kind": {"type": "string", "enum": ["upload", "text", "checkbox", "other"]},
                "source": {"type": "string"}, "excerpt": {"type": "string"},
            }, "required": ["label"]}},
            "mappings": {"type": "array", "items": {"type": "object", "properties": {
                "requirement": {"type": "string", "description": "R-code or #ref."},
                "field": {"type": "string", "description": "F-code or #ref."},
                "document": {"type": "string", "description": "D-code, or a path inside the folder."},
                "mapping": {"type": "string", "description": "Existing M-code to change."},
            }, "required": ["requirement"]}},
            "actions": {"type": "array", "items": {"type": "object", "properties": {
                "ref": {"type": "string"}, "description": {"type": "string"},
                "requirement": {"type": "string"}, "document": {"type": "string"},
            }, "required": ["description"]}},
            "conflicts": {"type": "array", "items": {"type": "object", "properties": {
                "description": {"type": "string"},
                "requirements": {"type": "array", "items": {"type": "string"}},
                "excerpts": {"type": "array", "items": {"type": "object", "properties": {
                    "source": {"type": "string"}, "excerpt": {"type": "string"}}, "required": ["source", "excerpt"]}},
            }, "required": ["description", "requirements", "excerpts"]}},
        },
    },
}

CHECK_SCHEMA = {
    "name": "submission_desk_check",
    "description": (
        "Submission Desk: re-read every mapped file and source now, run the machine checks (presence, real format, exact "
        "byte size, PDF pages, word counts) and return current readiness: missing, failed, unknown, conflicting, stale "
        "and waiting-for-applicant items, each with the next step. It never marks anything confirmed or ready."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "workspace": {"type": "string"},
            "detail": {"type": "string", "enum": ["summary", "full"], "description": "full adds every measurement."},
        },
    },
}

EXPORT_SCHEMA = {
    "name": "submission_desk_export",
    "description": (
        "Submission Desk: recheck, then write the hand-back checklist (Markdown) and a manifest with the SHA-256 of every "
        "checked file revision to the plugin's export folder. Does not copy, upload or submit any file."
    ),
    "parameters": {"type": "object", "properties": {"workspace": {"type": "string"}}},
}

_ALLOWED_TOP = {
    "submission_desk_workspace": set(WORKSPACE_SCHEMA["parameters"]["properties"]),
    "submission_desk_propose": set(PROPOSE_SCHEMA["parameters"]["properties"]),
    "submission_desk_check": set(CHECK_SCHEMA["parameters"]["properties"]),
    "submission_desk_export": set(EXPORT_SCHEMA["parameters"]["properties"]),
}


def _find_forged(value: Any, path: str = "") -> str | None:
    if isinstance(value, dict):
        for key, item in value.items():
            key_l = str(key).strip().lower()
            if key_l in FORGED_KEYS and not path.endswith("rules"):
                return f"{path}.{key}" if path else str(key)
            found = _find_forged(item, f"{path}.{key}" if path else str(key))
            if found:
                return found
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found = _find_forged(item, f"{path}[{index}]")
            if found:
                return found
    return None


def _guard(name: str, args: dict) -> None:
    forged = _find_forged(args)
    if forged:
        raise HumanOnly(
            f"Argument '{forged}' is not accepted: an agent tool cannot carry a confirmation, review, attestation or "
            "actor. Nothing was stored.",
            hint="Submit the proposal without it; the applicant confirms on the Desktop page or with /submission-desk confirm.",
        )
    unknown = sorted(set(args) - _ALLOWED_TOP[name])
    if unknown:
        raise Invalid(f"Unknown argument(s): {', '.join(unknown)}.", hint=f"Allowed: {', '.join(sorted(_ALLOWED_TOP[name]))}.")


def _dump(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, default=str)


def _actor(kwargs: dict, tool: str) -> actors.Actor:
    session = str(kwargs.get("session_id") or kwargs.get("task_id") or "")
    return actors.agent(session_id=session, tool=tool)


def _run(name: str, args: Any, kwargs: dict, fn) -> str:
    try:
        if not isinstance(args, dict):
            raise Invalid("Arguments must be an object.")
        _guard(name, args)
        return _dump(fn(Desk(), _actor(kwargs, name), args))
    except DeskError as exc:
        return _dump(exc.to_dict())
    except Exception as exc:  # never raise into the agent loop
        return _dump({"error": "internal", "message": f"{type(exc).__name__}: {exc}",
                      "hint": "This is a Submission Desk bug; nothing was confirmed. Try the Desktop page or report it."})


def _workspace(desk: Desk, actor: actors.Actor, args: dict) -> dict:
    action = args.get("action")
    if action == "list":
        return {"workspaces": desk.list_workspaces()}
    if action == "read_source":
        return desk.read_source(args.get("workspace"), args.get("source"), offset=args.get("offset") or 0)
    if action == "create":
        created = desk.create_workspace(actor, args.get("title"), args.get("material_root"), timezone_name=args.get("timezone"))
        ws_ref = created["workspace"]["id"]
        added = _add(desk, actor, ws_ref, args)
        if not created["workspace"].get("folder_confirmed"):
            step = ("Ask the applicant to confirm the folder (Submission Desk page or /submission-desk folder confirm), "
                    "then add the files with action 'add'. Pasted text can be added and quoted now.")
        else:
            step = "Read the sources (read_source) and propose requirements with exact excerpts."
        return {**created, **added, "next": step}
    if action == "add":
        ws = desk.workspace(args.get("workspace"))
        return {"workspace": {"id": ws["id"], "title": ws["title"]}, **_add(desk, actor, ws["id"], args)}
    raise Invalid("action must be create, add, list or read_source.")


def _add(desk: Desk, actor: actors.Actor, ws_ref: str, args: dict) -> dict:
    out: dict[str, list] = {"sources": [], "documents": [], "errors": []}
    for path in args.get("source_files") or []:
        try:
            out["sources"].append(desk.add_source_file(actor, ws_ref, path)["source"])
        except DeskError as exc:
            out["errors"].append({"item": path, **exc.to_dict()})
    for item in args.get("source_texts") or []:
        try:
            out["sources"].append(desk.add_source_text(actor, ws_ref, item.get("label"), item.get("text"),
                                                       origin_note=item.get("origin"))["source"])
        except DeskError as exc:
            out["errors"].append({"item": item.get("label"), **exc.to_dict()})
    for path in args.get("documents") or []:
        try:
            out["documents"].append(desk.add_document(actor, ws_ref, path)["document"])
        except DeskError as exc:
            out["errors"].append({"item": path, **exc.to_dict()})
    return {k: v for k, v in out.items() if v}


def _propose(desk: Desk, actor: actors.Actor, args: dict) -> dict:
    batch = {k: v for k, v in args.items() if k != "workspace" and v}
    if not batch:
        raise Invalid("Nothing to propose.")
    result = desk.propose(actor, args.get("workspace"), batch)
    result["note"] = "Stored as proposals. The applicant must confirm them (Desktop page or /submission-desk confirm <code>)."
    return result


def _check(desk: Desk, actor: actors.Actor, args: dict) -> dict:
    readiness = desk.recheck(actor, args.get("workspace"))
    return textfmt.compact(readiness, full=args.get("detail") == "full")


def _export(desk: Desk, actor: actors.Actor, args: dict) -> dict:
    return export_mod.export(desk, actor, args.get("workspace"))


def workspace_tool(args, **kwargs):
    return _run("submission_desk_workspace", args, kwargs, _workspace)


def propose_tool(args, **kwargs):
    return _run("submission_desk_propose", args, kwargs, _propose)


def check_tool(args, **kwargs):
    return _run("submission_desk_check", args, kwargs, _check)


def export_tool(args, **kwargs):
    return _run("submission_desk_export", args, kwargs, _export)


TOOLS = (
    (WORKSPACE_SCHEMA, workspace_tool, "\U0001f5c2"),
    (PROPOSE_SCHEMA, propose_tool, "\U0001f4dd"),
    (CHECK_SCHEMA, check_tool, "\u2705"),
    (EXPORT_SCHEMA, export_tool, "\U0001f4e6"),
)


def register(ctx) -> None:
    for schema, handler, emoji in TOOLS:
        ctx.register_tool(name=schema["name"], toolset=TOOLSET, schema=schema, handler=handler,
                          description=schema["description"], emoji=emoji)
