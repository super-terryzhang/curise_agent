"""Settings page — agent tools / skills surface.

`GET /tools` reads the live general-agent REGISTRY after v3 tools are
imported, so the admin UI shows exactly what the chat agent has access
to. Skill management is deferred to Phase 6b (`v3_agent_skills` table)
— v3's chat factory doesn't currently use general-agent's skill system,
so the GET stub returns an empty list.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, status
from general_agent import REGISTRY

# Side-effect import: registers all v3 business tools on the global
# REGISTRY. Without this the admin UI would only see general-agent
# bundled tools.
from agent.runtime import tools as _v3_tools  # noqa: F401
from apps.http._deps import Admin

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("/tools")
def list_tools(_admin: Admin) -> list[dict[str, Any]]:
    """Return the general-agent tool catalog (name + description + group + enabled).

    This is the live registry view — same source the chat agent dispatches
    against. Group names map to `toolset` (e.g. `business`, `web`,
    `notes`, `control`, `planning`).
    """
    return [
        {
            "name": t.name,
            "description": t.description,
            "group": t.toolset,
            "is_enabled": True,  # toggling waits for Phase 6b's v3_tool_configs
        }
        for t in REGISTRY.all()
    ]


@router.patch("/tools/{tool_name}")
def update_tool(tool_name: str, _admin: Admin) -> dict[str, str]:  # noqa: ARG001
    raise HTTPException(
        status.HTTP_501_NOT_IMPLEMENTED,
        "Tool enable/disable awaits Phase 6b (v3_tool_configs)",
    )


@router.post("/tools/seed")
def seed_tools(_admin: Admin) -> dict[str, Any]:
    return {"seeded": 0, "note": "tools are code-registered; no seeding needed"}


@router.get("/skills")
def list_skills(_admin: Admin) -> list[dict[str, Any]]:
    """v3 chat doesn't currently load general-agent skills.

    Returns []. Phase 6b will introduce `v3_agent_skills` for DB-backed
    SKILL.md content and re-enable the loader in `create_v3_chat_agent`.
    """
    return []


@router.post("/skills")
def create_skill(_admin: Admin) -> dict[str, str]:
    raise HTTPException(
        status.HTTP_501_NOT_IMPLEMENTED, "Skill CRUD awaits Phase 6b (v3_agent_skills)"
    )


@router.get("/skills/{skill_id}")
def get_skill(skill_id: int, _admin: Admin) -> dict[str, str]:  # noqa: ARG001
    raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, "Skill detail awaits Phase 6b")


@router.patch("/skills/{skill_id}")
def update_skill(skill_id: int, _admin: Admin) -> dict[str, str]:  # noqa: ARG001
    raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, "Skill edit awaits Phase 6b")


@router.delete("/skills/{skill_id}")
def delete_skill(skill_id: int, _admin: Admin) -> dict[str, str]:  # noqa: ARG001
    raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, "Skill delete awaits Phase 6b")


@router.post("/skills/seed")
def seed_skills(_admin: Admin) -> dict[str, Any]:
    return {"seeded": 0, "note": "Skills will be DB-backed in Phase 6b; no seeding needed"}
