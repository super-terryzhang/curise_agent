"""Shared validation helpers used across CRUD services.

Internal module (underscore prefix). Only `service` + sibling submodules use these.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from domains.masterdata import repository as repo
from domains.masterdata.errors import BadRequest, Conflict


def parse_date_str(value: str | None) -> datetime | None:
    """YYYY-MM-DD / YYYY/MM/DD / DD/MM/YYYY → datetime. None stays None."""
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    raise BadRequest(f"日期格式无效: '{value}'，请使用 YYYY-MM-DD")


def check_unique_code(
    db: Session,
    lookup_fn: Any,
    code: str | None,
    *,
    exclude_id: int | None = None,
) -> None:
    if not code:
        return
    existing = lookup_fn(db, code)
    if existing and (exclude_id is None or existing.id != exclude_id):
        raise Conflict(f"code '{code}' 已存在")


def require_country(db: Session, country_id: int | None) -> None:
    if country_id is not None and not repo.country_exists(db, country_id):
        raise BadRequest("国家不存在")


def require_port(db: Session, port_id: int | None) -> None:
    if port_id is not None and not repo.port_exists(db, port_id):
        raise BadRequest("港口不存在")


def require_category(db: Session, category_id: int | None) -> None:
    if category_id is not None and not repo.category_exists(db, category_id):
        raise BadRequest("类别不存在")


def require_supplier(db: Session, supplier_id: int | None) -> None:
    if supplier_id is not None and not repo.supplier_exists(db, supplier_id):
        raise BadRequest("供应商不存在")


def assert_no_references(
    db: Session,
    refs: list[tuple[Any, str, int, str]],
) -> None:
    """Raise Conflict if any row in the listed tables references the given value."""
    for model, column, value, entity_name in refs:
        n = repo.count_rows_referencing(db, model, column, value)
        if n:
            raise Conflict(f"无法删除：有 {n} 条{entity_name}引用此记录")
