"""Capability catalog — the source of truth for white-list feature keys.

The 4-tier role system (superadmin > admin > finance > employee) gates
coarse surfaces. Capabilities sit alongside roles and gate specific
high-sensitivity features per user — they're additive, not a replacement.

Semantics:
    - superadmin auto-passes every capability check (root bypass).
    - All other roles: must have an explicit row in `v3_user_capabilities`.
    - There is no "deny" — only grant. Missing row = no access.

Adding a new capability:
    1. Add a constant + entry to CAPABILITY_CATALOG.
    2. Apply `require_capability(KEY)` to the relevant endpoint(s).
    3. Update the frontend capability picker labels.
    No schema migration needed — the DB stores it as a string.
"""

from __future__ import annotations

from dataclasses import dataclass

# ─── Capability keys ─────────────────────────────────────────
#
# Use dot-notation (`scope.action`) so future capabilities cluster
# naturally: `financials.view`, `inquiry.send`, `data.export`, …
#
# Keys here are STABLE identifiers stored in the DB. Renaming a key
# requires a data migration — don't do it casually.

CAP_FINANCIALS_VIEW = "financials.view"


@dataclass(frozen=True)
class CapabilityDef:
    key: str
    label: str  # zh-CN display label, shown in the user-management UI
    description: str  # zh-CN one-liner for the picker tooltip


CAPABILITY_CATALOG: dict[str, CapabilityDef] = {
    CAP_FINANCIALS_VIEW: CapabilityDef(
        key=CAP_FINANCIALS_VIEW,
        label="财务分析",
        description="可使用财务分析模块：查看利润、成本，修改成本和税率并导出；仅限本人有权访问的订单",
    ),
}


def is_known_capability(key: str) -> bool:
    """True if the key is in the catalog. Use to reject grants of
    typo'd / stale capability names at the API boundary."""
    return key in CAPABILITY_CATALOG


def all_capability_keys() -> list[str]:
    """For the frontend picker — list of (key, label, description)."""
    return list(CAPABILITY_CATALOG.keys())
