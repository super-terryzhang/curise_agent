"""DB access for the settings domain."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from domains.settings.models import (
    CompanyConfig,
    DeliveryLocation,
    FieldDefinition,
    FieldSchema,
    OrderFormatTemplate,
)

# ─── Field Schema ───────────────────────────────────────────


def list_field_schemas(db: Session) -> list[FieldSchema]:
    return list(db.execute(select(FieldSchema).order_by(FieldSchema.id)).scalars())


def get_field_schema(db: Session, schema_id: int) -> FieldSchema | None:
    return db.get(FieldSchema, schema_id)


def get_default_field_schema(db: Session) -> FieldSchema | None:
    return db.execute(
        select(FieldSchema).where(FieldSchema.is_default.is_(True))
    ).scalar_one_or_none()


# ─── Field Definition ───────────────────────────────────────


def get_field_definition(db: Session, schema_id: int, def_id: int) -> FieldDefinition | None:
    return db.execute(
        select(FieldDefinition).where(
            FieldDefinition.id == def_id, FieldDefinition.schema_id == schema_id
        )
    ).scalar_one_or_none()


# ─── Order Format Template ──────────────────────────────────


def list_order_templates(db: Session) -> list[OrderFormatTemplate]:
    return list(
        db.execute(select(OrderFormatTemplate).order_by(OrderFormatTemplate.id.desc())).scalars()
    )


def get_order_template(db: Session, tpl_id: int) -> OrderFormatTemplate | None:
    return db.get(OrderFormatTemplate, tpl_id)


# ─── Delivery Location ──────────────────────────────────────


def list_delivery_locations(db: Session, *, port_id: int | None = None) -> list[DeliveryLocation]:
    stmt = select(DeliveryLocation).order_by(DeliveryLocation.id)
    if port_id is not None:
        stmt = stmt.where(DeliveryLocation.port_id == port_id)
    return list(db.execute(stmt).scalars())


def get_delivery_location(db: Session, loc_id: int) -> DeliveryLocation | None:
    return db.get(DeliveryLocation, loc_id)


# ─── Company Config ─────────────────────────────────────────


def list_company_config(db: Session) -> list[CompanyConfig]:
    return list(db.execute(select(CompanyConfig).order_by(CompanyConfig.sort_order)).scalars())


def get_company_config_item(db: Session, key: str) -> CompanyConfig | None:
    return db.execute(select(CompanyConfig).where(CompanyConfig.key == key)).scalar_one_or_none()


def save(db: Session, obj: object) -> None:
    db.commit()


def add(db: Session, obj: object) -> None:
    db.add(obj)
