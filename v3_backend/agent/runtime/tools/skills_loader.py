"""`load_skill` tool — the agent-driven half of v3's progressive-disclosure
skill activation (Phase 2, 2026-05-10).

Flow:
  1. Agent boots with the skill catalog (name + description) injected
     into its system prompt by `general_agent.core._build_system_prompt`.
  2. Agent reads the user's task, scans the catalog, picks the relevant
     skill, then calls `load_skill(name)`.
  3. This tool returns the full SKILL.md body. The agent now has the
     playbook in context and follows it.
  4. We also fire `on_skills_activated` on the host's StreamCallbacks
     so the chat UI can render a "skill activated" chip
     (`MessageView.tsx:SkillChipRow`) at the moment the agent commits
     to a playbook.

Why a tool rather than auto-injection:
- Token-efficient — bodies only enter context when relevant.
- Auditable — every activation is a tool call in the trace.
- Anthropic-aligned — matches the official Skills design pattern
  (cf. platform.claude.com/docs → Agent Skills).
"""

from __future__ import annotations

import json

from general_agent import ToolContext, tool

# Limit a single load_skill call from returning more than this many
# characters, so a misconfigured skill can't blow up the context window.
_MAX_BODY_CHARS = 32_000


@tool(toolset="business", emoji="📚")
def load_skill(name: str, *, ctx: ToolContext) -> str:
    """Load the full body of a skill by name.

    Use this AFTER reading the catalog in your system prompt and deciding
    a specific skill applies to the user's task. Pass the skill's exact
    `name` field (the catalog lists every available one). The returned
    body is procedural knowledge — follow it as your playbook for the
    rest of the turn.

    Args:
        name: Exact skill name from the catalog (e.g. "master-data-upload").
    """
    extras = getattr(ctx, "extras", None)
    if not isinstance(extras, dict):
        return "Error: agent context missing extras dict — runtime misconfigured"

    loader = extras.get("skill_loader")
    if loader is None:
        return "Error: skill loader not available in this agent"

    skill = loader.get(name)
    if skill is None:
        available = [s.name for s in loader.skills]
        return (
            f"Error: no skill named {name!r}. "
            f"Available: {', '.join(available) if available else '(none)'}"
        )

    # Notify the host (SSE → UI) that this skill is now active for this turn.
    cb = extras.get("stream_callbacks")
    if cb is not None and getattr(cb, "on_skills_activated", None) is not None:
        try:
            cb.on_skills_activated(
                [{"name": skill.name, "description": skill.description or ""}]
            )
        except Exception:
            # UI notification must never break tool execution.
            pass

    body = skill.body or ""
    if len(body) > _MAX_BODY_CHARS:
        truncated_note = (
            f"\n\n…[truncated — body was {len(body)} chars, "
            f"capped to {_MAX_BODY_CHARS}; ask the user if you need more]"
        )
        body = body[:_MAX_BODY_CHARS] + truncated_note

    # Wrap in a structured envelope so the agent's tool-result parser sees
    # a JSON shape and can route it correctly. The body itself is markdown.
    return json.dumps(
        {
            "name": skill.name,
            "description": skill.description or "",
            "body": body,
        },
        ensure_ascii=False,
    )
