"""Reusable and audited PO-to-supplier unit conversion rules."""

from __future__ import annotations

import json
import unicodedata
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from domains.masterdata.errors import BadRequest, Conflict, NotFound
from domains.masterdata.models import Product, UnitConversionRule
from domains.masterdata.schemas import (
    UnitConversionRuleCreate,
    UnitConversionRuleRetire,
    UnitConversionRuleVerify,
)

_STANDARD_UNITS: dict[str, tuple[str, Decimal]] = {
    "MG": ("mass", Decimal("0.000001")),
    "G": ("mass", Decimal("0.001")),
    "KG": ("mass", Decimal("1")),
    "LB": ("mass", Decimal("0.45359237")),
    "OZ": ("mass", Decimal("0.028349523125")),
    "ML": ("volume", Decimal("0.001")),
    "L": ("volume", Decimal("1")),
    "FL OZ": ("volume", Decimal("0.0295735295625")),
    "GAL": ("volume", Decimal("3.785411784")),
}


def normalize_unit(value: str) -> str:
    """Normalize exact unit text for lookup without mutating source records."""

    normalized = unicodedata.normalize("NFKC", str(value or ""))
    return " ".join(normalized.strip().split()).upper()


def _normalize_pack_part(value: str | None) -> str:
    return normalize_unit(value or "")


def product_pack_signature(
    unit: str | None,
    unit_size: str | None,
    pack_size: str | None,
) -> str:
    """Return a readable deterministic fingerprint of supplier packaging."""

    return json.dumps(
        [
            _normalize_pack_part(unit),
            _normalize_pack_part(unit_size),
            _normalize_pack_part(pack_size),
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool):
        return None
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if not result.is_finite() or result <= 0:
        return None
    return result


def _fixed(value: Decimal) -> str:
    return format(value, ".10f")


def _review(code: str, message: str, *, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "status": "review",
        "issue_code": code,
        "message": message,
        "details": details or {},
    }


def _date_allows(rule: UnitConversionRule, business_date: date | None) -> bool:
    if business_date is None:
        return rule.valid_from is None and rule.valid_to is None
    if rule.valid_from is not None and business_date < rule.valid_from:
        return False
    return rule.valid_to is None or business_date <= rule.valid_to


def _eligible_rules(
    db: Session,
    *,
    source_system: str,
    source_unit: str,
    target_unit: str,
    include_drafts: bool,
) -> list[UnitConversionRule]:
    statuses = ["verified", "draft"] if include_drafts else ["verified"]
    return list(
        db.execute(
            select(UnitConversionRule).where(
                UnitConversionRule.source_system == source_system,
                UnitConversionRule.source_unit == source_unit,
                UnitConversionRule.target_unit == target_unit,
                UnitConversionRule.status.in_(statuses),
            )
        ).scalars()
    )


def _rule_evidence(rule: UnitConversionRule) -> dict[str, Any]:
    return {
        "verified": rule.status == "verified",
        "rule_id": rule.id,
        "rule_revision": rule.revision,
        "scope_type": rule.scope_type,
        "source_quantity": _fixed(rule.source_quantity),
        "target_quantity": _fixed(rule.target_quantity),
        "pack_signature": rule.pack_signature,
        "evidence": rule.evidence,
        "verified_by": rule.verified_by,
        "verified_at": rule.verified_at.isoformat() if rule.verified_at else None,
    }


def _apply_rule(
    rule: UnitConversionRule,
    *,
    source_quantity: Decimal,
    raw_source_unit: str,
    raw_target_unit: str,
) -> dict[str, Any]:
    target_quantity = source_quantity * rule.target_quantity / rule.source_quantity
    if rule.target_step is not None and target_quantity % rule.target_step != 0:
        return _review(
            "NON_INTEGER_PACKAGE_QUANTITY",
            "换算结果不符合供应商订购步长，请人工确认",
            details={
                "calculated_quantity": str(target_quantity),
                "target_step": _fixed(rule.target_step),
                "break_pack": rule.break_pack,
                "rule_id": rule.id,
            },
        )
    return {
        "status": "converted",
        "source_quantity": source_quantity,
        "source_unit": raw_source_unit,
        "target_quantity": target_quantity,
        "target_unit": raw_target_unit,
        "conversion_evidence": _rule_evidence(rule),
    }


def evaluate_unit_conversion(
    db: Session,
    *,
    product_id: int | None,
    source_system: str,
    source_quantity: Any,
    source_unit: str,
    target_unit: str,
    business_date: date | datetime | None,
    product_unit: str | None,
    product_unit_size: str | None,
    product_pack_size: str | None,
    include_drafts: bool = False,
) -> dict[str, Any]:
    """Evaluate one row without changing its stored source quantity or unit."""

    quantity = _decimal(source_quantity)
    if quantity is None:
        return _review("QUANTITY_INVALID", "订购数量必须是大于 0 的数字")

    normalized_source = normalize_unit(source_unit)
    normalized_target = normalize_unit(target_unit)
    if not normalized_source or not normalized_target:
        return _review("UNIT_CONVERSION_REQUIRED", "订购单位或供应商单位缺失")

    if normalized_source == normalized_target:
        return {
            "status": "same",
            "source_quantity": quantity,
            "source_unit": source_unit,
            "target_quantity": quantity,
            "target_unit": target_unit,
            "conversion_evidence": {
                "verified": True,
                "scope_type": "identity",
                "evidence": "规范化后的订购单位与供应商单位一致",
            },
        }

    source_standard = _STANDARD_UNITS.get(normalized_source)
    target_standard = _STANDARD_UNITS.get(normalized_target)
    if (
        source_standard is not None
        and target_standard is not None
        and source_standard[0] == target_standard[0]
    ):
        converted = quantity * source_standard[1] / target_standard[1]
        return {
            "status": "converted",
            "source_quantity": quantity,
            "source_unit": source_unit,
            "target_quantity": converted,
            "target_unit": target_unit,
            "conversion_evidence": {
                "verified": True,
                "scope_type": "standard",
                "source_factor": str(source_standard[1]),
                "target_factor": str(target_standard[1]),
                "evidence": "国际标准物理单位换算",
            },
        }

    resolved_date = business_date.date() if isinstance(business_date, datetime) else business_date
    source_system_key = unicodedata.normalize("NFKC", source_system).strip().lower()
    rules = [
        rule
        for rule in _eligible_rules(
            db,
            source_system=source_system_key,
            source_unit=normalized_source,
            target_unit=normalized_target,
            include_drafts=include_drafts,
        )
        if _date_allows(rule, resolved_date)
    ]

    current_signature = product_pack_signature(
        product_unit, product_unit_size, product_pack_size
    )
    if product_id is not None:
        product_rules = [rule for rule in rules if rule.product_id == product_id]
        exact_product_rules = [
            rule for rule in product_rules if rule.pack_signature == current_signature
        ]
        if len(exact_product_rules) > 1:
            return _review(
                "UNIT_CONVERSION_RULE_CONFLICT",
                "同一商品包装存在多条可用换算规则",
                details={"rule_ids": [rule.id for rule in exact_product_rules]},
            )
        if len(exact_product_rules) == 1:
            return _apply_rule(
                exact_product_rules[0],
                source_quantity=quantity,
                raw_source_unit=source_unit,
                raw_target_unit=target_unit,
            )
        if product_rules:
            saved = sorted({rule.pack_signature or "" for rule in product_rules})
            return _review(
                "UNIT_CONVERSION_RULE_STALE",
                "商品包装已变化，原换算规则不能继续自动使用",
                details={
                    "saved_pack_signature": saved[0] if len(saved) == 1 else saved,
                    "current_pack_signature": current_signature,
                    "rule_ids": [rule.id for rule in product_rules],
                },
            )

    source_rules = [rule for rule in rules if rule.product_id is None]
    if len(source_rules) > 1:
        return _review(
            "UNIT_CONVERSION_RULE_CONFLICT",
            "同一来源单位存在多条可用换算规则",
            details={"rule_ids": [rule.id for rule in source_rules]},
        )
    if len(source_rules) == 1:
        return _apply_rule(
            source_rules[0],
            source_quantity=quantity,
            raw_source_unit=source_unit,
            raw_target_unit=target_unit,
        )
    return _review(
        "UNIT_CONVERSION_REQUIRED",
        "订购单位与供应商单位不一致，且没有可用的换算规则",
        details={
            "source_system": source_system_key,
            "source_unit": source_unit,
            "target_unit": target_unit,
            "product_id": product_id,
        },
    )


def _serialize(rule: UnitConversionRule) -> dict[str, Any]:
    return {
        column.name: getattr(rule, column.name)
        for column in UnitConversionRule.__table__.columns
    }


def create_unit_conversion_rule(
    db: Session,
    body: UnitConversionRuleCreate,
    *,
    actor_id: int,
    commit: bool = True,
) -> dict[str, Any]:
    if body.scope_type not in {"source_unit", "product"}:
        raise BadRequest("单位换算规则作用域必须是来源单位或商品包装")
    if not body.source_system.strip() or not body.source_unit.strip() or not body.target_unit.strip():
        raise BadRequest("来源系统、订购单位和供应商单位不能为空")
    if (
        not body.source_quantity.is_finite()
        or not body.target_quantity.is_finite()
        or body.source_quantity <= 0
        or body.target_quantity <= 0
    ):
        raise BadRequest("换算数量必须是大于 0 的数字")
    if body.target_step is not None and (
        not body.target_step.is_finite() or body.target_step <= 0
    ):
        raise BadRequest("供应商订购步长必须大于 0")
    if not body.evidence.strip():
        raise BadRequest("审核依据不能为空")
    if body.valid_from and body.valid_to and body.valid_from > body.valid_to:
        raise BadRequest("有效开始日期不能晚于结束日期")
    if body.scope_type == "source_unit" and (
        body.product_id is not None or body.pack_signature is not None
    ):
        raise BadRequest("来源单位规则不能指定产品或包装指纹")
    if body.scope_type == "product" and (
        body.product_id is None or not (body.pack_signature or "").strip()
    ):
        raise BadRequest("商品包装规则必须指定产品和包装指纹")
    if body.scope_type == "product" and db.get(Product, body.product_id) is None:
        raise BadRequest("商品不存在，不能创建换算规则")
    values = body.model_dump()
    values.update(
        source_system=unicodedata.normalize("NFKC", body.source_system).strip().lower(),
        source_unit=normalize_unit(body.source_unit),
        target_unit=normalize_unit(body.target_unit),
        pack_signature=(body.pack_signature.strip() if body.pack_signature else None),
        evidence=body.evidence.strip(),
        status="draft",
        created_by=actor_id,
        updated_by=actor_id,
    )
    rule = UnitConversionRule(**values)
    db.add(rule)
    try:
        if commit:
            db.commit()
        else:
            db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise Conflict("相同作用域的换算规则发生冲突") from exc
    db.refresh(rule)
    return _serialize(rule)


def _rule_for_update(db: Session, rule_id: int, expected_revision: int) -> UnitConversionRule:
    rule = db.get(UnitConversionRule, rule_id)
    if rule is None:
        raise NotFound("单位换算规则不存在")
    if rule.revision != expected_revision:
        raise Conflict("单位换算规则已被其他操作修改，请刷新后重试")
    return rule


def verify_unit_conversion_rule(
    db: Session,
    rule_id: int,
    body: UnitConversionRuleVerify,
    *,
    actor_id: int,
    commit: bool = True,
) -> dict[str, Any]:
    rule = _rule_for_update(db, rule_id, body.expected_revision)
    if rule.status != "draft":
        raise BadRequest("只有草稿规则可以审核")
    if rule.scope_type == "product":
        product = db.get(Product, rule.product_id)
        if product is None:
            raise Conflict("规则关联商品不存在")
        current_signature = product_pack_signature(
            product.unit, product.unit_size, product.pack_size
        )
        if current_signature != rule.pack_signature:
            raise Conflict("商品包装已变化，请重新创建换算规则")
    rule.status = "verified"
    rule.evidence = body.evidence
    rule.verified_by = actor_id
    rule.verified_at = datetime.now(UTC).replace(tzinfo=None)
    rule.updated_by = actor_id
    try:
        if commit:
            db.commit()
        else:
            db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise Conflict("相同作用域已有已验证规则，请先停用旧规则") from exc
    db.refresh(rule)
    return _serialize(rule)


def retire_unit_conversion_rule(
    db: Session,
    rule_id: int,
    body: UnitConversionRuleRetire,
    *,
    actor_id: int,
    commit: bool = True,
) -> dict[str, Any]:
    rule = _rule_for_update(db, rule_id, body.expected_revision)
    if rule.status == "retired":
        raise BadRequest("单位换算规则已经停用")
    rule.status = "retired"
    if body.evidence is not None:
        rule.evidence = body.evidence
    rule.updated_by = actor_id
    if commit:
        db.commit()
    else:
        db.flush()
    db.refresh(rule)
    return _serialize(rule)


def list_unit_conversion_rules(
    db: Session,
    *,
    status: str | None = None,
    product_id: int | None = None,
) -> list[dict[str, Any]]:
    query = select(UnitConversionRule)
    if status is not None:
        query = query.where(UnitConversionRule.status == status)
    if product_id is not None:
        query = query.where(UnitConversionRule.product_id == product_id)
    query = query.order_by(UnitConversionRule.id.desc())
    return [_serialize(rule) for rule in db.execute(query).scalars()]
