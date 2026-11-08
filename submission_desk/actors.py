"""Who asserted something.

The actor is decided by the surface that received the request, never by a field the caller
supplies: agent tools always build an ``agent`` actor, the Desktop backend and the slash
command build ``human`` actors, and the check engine is ``machine``. This is an attribution
boundary against the model, not a sandbox (see README, "Threat model").
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

HUMAN = "human"
AGENT = "agent"
MACHINE = "machine"


@dataclass(frozen=True)
class Actor:
    kind: str
    surface: str
    session_id: str = ""
    platform: str = ""
    user_id: str = ""
    detail: str = ""

    @property
    def is_human(self) -> bool:
        return self.kind == HUMAN

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v}

    def label(self) -> str:
        if self.kind == HUMAN:
            where = {"desktop": "Desktop", "slash": "/submission-desk", "cli": "CLI"}.get(self.surface, self.surface)
            return f"you ({where})"
        if self.kind == AGENT:
            return "agent proposal"
        return "machine check"


def human(surface: str, *, session_id: str = "", platform: str = "", user_id: str = "") -> Actor:
    return Actor(HUMAN, surface, session_id=session_id or "", platform=platform or "", user_id=user_id or "")


def agent(*, session_id: str = "", tool: str = "") -> Actor:
    return Actor(AGENT, "tool", session_id=session_id or "", detail=tool)


def machine(detail: str = "") -> Actor:
    return Actor(MACHINE, "engine", detail=detail)


def from_dict(data: dict[str, Any] | None) -> Actor:
    data = data or {}
    return Actor(
        kind=str(data.get("kind", MACHINE)),
        surface=str(data.get("surface", "")),
        session_id=str(data.get("session_id", "")),
        platform=str(data.get("platform", "")),
        user_id=str(data.get("user_id", "")),
        detail=str(data.get("detail", "")),
    )
