"""Errors with stable codes, an actionable message and an optional hint.

Every surface (agent tools, slash command, REST) renders these the same way, so a caller can
branch on ``code`` instead of parsing prose.
"""

from __future__ import annotations

from typing import Any


class DeskError(Exception):
    code = "error"
    http_status = 400

    def __init__(self, message: str, *, hint: str | None = None, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.message = message
        self.hint = hint
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"error": self.code, "message": self.message}
        if self.hint:
            out["hint"] = self.hint
        if self.details:
            out["details"] = self.details
        return out


class NotFound(DeskError):
    code = "not_found"
    http_status = 404


class Invalid(DeskError):
    code = "invalid"
    http_status = 422


class HumanOnly(DeskError):
    """An agent (or machine) actor tried an action reserved for the applicant."""

    code = "human_only"
    http_status = 403


class StaleRevision(DeskError):
    """The caller acted on a view that no longer matches the stored state."""

    code = "stale_revision"
    http_status = 409


class FolderUnconfirmed(DeskError):
    """The agent chose the material folder and the applicant has not confirmed it yet."""

    code = "folder_unconfirmed"
    http_status = 409


class ConfirmationRequired(DeskError):
    """A destructive action needs an explicit, matching confirmation token."""

    code = "confirmation_required"
    http_status = 428
