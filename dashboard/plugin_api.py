"""Desktop backend for Submission Desk, mounted by Hermes at ``/api/plugins/submission-desk/``.

Requests here come from the Submission Desk page in Hermes Desktop, so decisions are recorded
as the applicant's (surface ``desktop``). Agent tools never reach these routes. Any program
running as the same OS user could also call them with a Desktop grant: this is an attribution
boundary, not a sandbox (see README, "Threat model").
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, Body, Depends, Query, Request
from fastapi.responses import JSONResponse

_CORE = "hermes_submission_desk_core"


def _core():
    """Load ../submission_desk under a private name (this file is imported standalone by path)."""
    mod = sys.modules.get(_CORE)
    if mod is not None:
        return mod
    root = Path(__file__).resolve().parent.parent / "submission_desk"
    spec = importlib.util.spec_from_file_location(_CORE, root / "__init__.py", submodule_search_locations=[str(root)])
    mod = importlib.util.module_from_spec(spec)
    sys.modules[_CORE] = mod  # our own namespace, so the package's relative imports resolve
    spec.loader.exec_module(mod)
    return mod


def _mods():
    core = _core()
    import importlib

    return (core, importlib.import_module(f"{_CORE}.service"), importlib.import_module(f"{_CORE}.actors"),
            importlib.import_module(f"{_CORE}.errors"), importlib.import_module(f"{_CORE}.export"))


# The route a request came in on, so a request key only ever deduplicates the same request.
_ROUTE: ContextVar[str] = ContextVar("submission_desk_route", default="")


async def _remember_route(request: Request) -> None:
    _ROUTE.set(f"{request.method} {request.url.path}")


router = APIRouter(dependencies=[Depends(_remember_route)])


def _call(fn: Callable[..., Any]):
    core, service, actors, errors, export = _mods()
    try:
        desk = service.Desk()
        human = actors.human("desktop", platform="desktop")
        return fn(desk, human, export)
    except errors.DeskError as exc:
        # 401/403 make Desktop's transport start a sign-in flow; a refusal here is never an auth failure.
        status = 422 if exc.http_status in (401, 403) else exc.http_status
        return JSONResponse(status_code=status, content=exc.to_dict())
    except Exception as exc:  # keep the page alive and the error visible
        return JSONResponse(status_code=500, content={"error": "internal", "message": f"{type(exc).__name__}: {exc}",
                                                      "hint": "Nothing was changed by this failed request."})


def _int(payload: dict, key: str, default: int | None = 0) -> int | None:
    """A whole number from the request body; anything else is the caller's mistake (422), not ours (500)."""
    value = payload.get(key)
    if value is None or value == "":
        return default
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value.strip())
    raise _mods()[3].Invalid(f"{key} must be a whole number, not {json.dumps(value)[:40]}.")


def _rev(payload: dict) -> int | None:
    return _int(payload, "expected_revision", None)


def _idem(payload: dict) -> str | None:
    """The caller's request key, scoped to this route and this exact body.

    A retry of the same request returns the first result. The same key on another route is another
    request; the same key with a different body on this route is refused rather than answered from
    the first result (see store.idempotent_result).
    """
    key = payload.get("idempotency_key")
    if not key:
        return None
    body = {k: v for k, v in payload.items() if k != "idempotency_key"}
    fingerprint = hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:16]
    return f"{_ROUTE.get()}\x1f{str(key)[:100]}\x1f{fingerprint}"


@router.get("/health")
def health():
    core = _core()
    return {"ok": True, "plugin": core.PLUGIN_ID, "version": core.__version__, "engine": core.ENGINE_VERSION}


@router.get("/workspaces")
def list_workspaces():
    return _call(lambda d, h, e: {"workspaces": d.list_workspaces()})


@router.post("/workspaces")
def create_workspace(payload: dict = Body(default={})):
    return _call(lambda d, h, e: d.create_workspace(h, payload.get("title"), payload.get("material_root"),
                                                    timezone_name=payload.get("timezone") or None, idem=_idem(payload)))


@router.post("/workspaces/{ws}/folder/confirm")
def confirm_folder(ws: str, payload: dict = Body(default={})):
    return _call(lambda d, h, e: d.confirm_folder(h, ws, payload.get("material_root"), expected_revision=_rev(payload),
                                                  idem=_idem(payload)))


@router.get("/workspaces/{ws}")
def get_workspace(ws: str, refresh: int = Query(0)):
    return _call(lambda d, h, e: d.recheck(h, ws) if refresh else d.readiness(ws))


@router.post("/workspaces/{ws}/check")
def check(ws: str):
    return _call(lambda d, h, e: d.recheck(h, ws))


@router.get("/workspaces/{ws}/files")
def list_files(ws: str):
    return _call(lambda d, h, e: d.list_files(ws))


@router.post("/workspaces/{ws}/sources")
def add_source(ws: str, payload: dict = Body(default={})):
    def run(d, h, e):
        if payload.get("text"):
            return d.add_source_text(h, ws, payload.get("label") or "Pasted text", payload["text"],
                                     origin_note=payload.get("origin_note"), idem=_idem(payload))
        return d.add_source_file(h, ws, payload.get("path"), label=payload.get("label"),
                                 origin_note=payload.get("origin_note"), idem=_idem(payload))
    return _call(run)


@router.get("/workspaces/{ws}/sources/{code}")
def read_source(ws: str, code: str, offset: int = Query(0)):
    return _call(lambda d, h, e: d.read_source(ws, code, offset=offset, limit=20000))


@router.post("/workspaces/{ws}/documents")
def add_document(ws: str, payload: dict = Body(default={})):
    return _call(lambda d, h, e: d.add_document(h, ws, payload.get("path"), label=payload.get("label"), idem=_idem(payload)))


@router.post("/workspaces/{ws}/requirements")
def create_requirement(ws: str, payload: dict = Body(default={})):
    item = {k: payload[k] for k in ("statement", "kind", "rules", "source", "excerpt") if k in payload and payload[k] not in (None, "")}
    return _call(lambda d, h, e: d.create_requirement(h, ws, item, idem=_idem(payload)))


@router.post("/workspaces/{ws}/requirements/{code}/revise")
def revise_requirement(ws: str, code: str, payload: dict = Body(default={})):
    item = {k: payload[k] for k in ("statement", "kind", "rules", "source", "excerpt") if k in payload}
    return _call(lambda d, h, e: d.revise_requirement(h, ws, code, item, expected_revision=_rev(payload), idem=_idem(payload)))


@router.post("/workspaces/{ws}/requirements/{code}/decide")
def decide_requirement(ws: str, code: str, payload: dict = Body(default={})):
    return _call(lambda d, h, e: d.decide_requirement(h, ws, code, _int(payload, "version"), payload.get("decision"),
                                                      note=payload.get("note"), expected_revision=_rev(payload), idem=_idem(payload)))


@router.post("/workspaces/{ws}/fields")
def create_field(ws: str, payload: dict = Body(default={})):
    item = {k: payload[k] for k in ("label", "kind", "source", "excerpt") if payload.get(k)}
    return _call(lambda d, h, e: d.create_field(h, ws, item, idem=_idem(payload)))


@router.post("/workspaces/{ws}/fields/{code}/revise")
def revise_field(ws: str, code: str, payload: dict = Body(default={})):
    return _call(lambda d, h, e: d.revise_field(h, ws, code, payload.get("label"), expected_revision=_rev(payload), idem=_idem(payload)))


@router.post("/workspaces/{ws}/mappings")
def set_mapping(ws: str, payload: dict = Body(default={})):
    new_field = payload.get("new_field") or None
    return _call(lambda d, h, e: d.set_mapping(h, ws, requirement=payload.get("requirement"), field=payload.get("field") or None,
                                               document=payload.get("document") or None, mapping=payload.get("mapping") or None,
                                               note=payload.get("note"), expected_revision=_rev(payload), idem=_idem(payload),
                                               new_field=new_field))


@router.post("/workspaces/{ws}/mappings/{code}/remove")
def remove_mapping(ws: str, code: str, payload: dict = Body(default={})):
    return _call(lambda d, h, e: d.remove_mapping(h, ws, code, expected_revision=_rev(payload), idem=_idem(payload)))


@router.post("/workspaces/{ws}/mappings/{code}/review")
def review_mapping(ws: str, code: str, payload: dict = Body(default={})):
    return _call(lambda d, h, e: d.review_mapping(
        h, ws, code, decision=payload.get("decision"), mapping_version=_int(payload, "mapping_version"),
        requirement_version=_int(payload, "requirement_version"), sha256=str(payload.get("sha256") or ""),
        note=payload.get("note"), expected_revision=_rev(payload), idem=_idem(payload)))


@router.post("/workspaces/{ws}/actions")
def create_action(ws: str, payload: dict = Body(default={})):
    item = {k: payload[k] for k in ("description", "requirement", "document") if payload.get(k)}
    return _call(lambda d, h, e: d.create_action(h, ws, item, idem=_idem(payload)))


@router.post("/workspaces/{ws}/actions/{code}/decide")
def decide_action(ws: str, code: str, payload: dict = Body(default={})):
    return _call(lambda d, h, e: d.decide_action(h, ws, code, _int(payload, "version"), payload.get("decision"),
                                                 expected_revision=_rev(payload), idem=_idem(payload)))


@router.post("/workspaces/{ws}/attest")
def attest(ws: str, payload: dict = Body(default={})):
    return _call(lambda d, h, e: d.attest(h, ws, payload.get("subject"), version=_int(payload, "version"),
                                          statement=payload.get("statement"), check=payload.get("check") or None,
                                          mapping=payload.get("mapping") or None, sha256=payload.get("sha256") or None,
                                          expected_revision=_rev(payload), idem=_idem(payload)))


@router.post("/workspaces/{ws}/attestations/{attestation_id}/revoke")
def revoke(ws: str, attestation_id: str, payload: dict = Body(default={})):
    return _call(lambda d, h, e: d.revoke_attestation(h, ws, attestation_id, idem=_idem(payload)))


@router.post("/workspaces/{ws}/conflicts")
def flag_conflict(ws: str, payload: dict = Body(default={})):
    item = {k: payload[k] for k in ("description", "requirements", "excerpts") if payload.get(k)}
    return _call(lambda d, h, e: d.flag_conflict(h, ws, item, idem=_idem(payload)))


@router.post("/workspaces/{ws}/conflicts/{code}/resolve")
def resolve_conflict(ws: str, code: str, payload: dict = Body(default={})):
    return _call(lambda d, h, e: d.resolve_conflict(h, ws, code, resolution=payload.get("resolution"),
                                                    governing=payload.get("governing") or None,
                                                    expected_revision=_rev(payload), idem=_idem(payload)))


@router.post("/workspaces/{ws}/export")
def export_workspace(ws: str):
    def run(d, h, e):
        out = e.export(d, h, ws)
        out["checklist_text"] = Path(out["checklist"]).read_text(encoding="utf-8")
        return out
    return _call(run)


@router.get("/workspaces/{ws}/history")
def history(ws: str, subject: str | None = Query(None)):
    return _call(lambda d, h, e: d.history(ws, subject))


@router.post("/workspaces/{ws}/delete")
def delete_workspace(ws: str, payload: dict = Body(default={})):
    return _call(lambda d, h, e: d.delete_workspace(h, ws, confirm_token=payload.get("confirm_token")))
