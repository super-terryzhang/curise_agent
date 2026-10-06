"""Append history in the caller's transaction; never commit independently."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import DataChange, DataRecord, DataTable
from .repository import table_fields
from .schemas import Actor, FieldResponse


def append_change(
    db: Session,
    *,
    table_id: UUID,
    entity_type: str,
    entity_id: UUID,
    action: str,
    before: dict | None,
    after: dict | None,
    actor: Actor,
    schema_version: int,
    revision: int | None = None,
    creation_request: dict | None = None,
) -> None:
    db.flush()
    field_snapshot = {
        str(f.id): FieldResponse.model_validate(f).model_dump(mode="json")
        for f in table_fields(db, table_id)
    }
    linked_ids = set()
    for state in (before, after):
        values = (state or {}).get("values", {})
        for key, field in field_snapshot.items():
            if field["field_type"] == "link" and values.get(key):
                linked_ids.add(UUID(values[key]))
    targets = (
        list(db.scalars(select(DataRecord).where(DataRecord.id.in_(linked_ids))))
        if linked_ids
        else []
    )
    table_ids = {r.table_id for r in targets} | {
        UUID(f["target_table_id"]) for f in field_snapshot.values() if f["target_table_id"]
    }
    tables = (
        {t.id: t for t in db.scalars(select(DataTable).where(DataTable.id.in_(table_ids)))}
        if table_ids
        else {}
    )
    links = {}
    for target in targets:
        target_table = tables[target.table_id]
        label = (
            target.values.get(str(target_table.display_field_id))
            if target_table.display_field_id
            else None
        )
        links[str(target.id)] = {
            "record_id": str(target.id),
            "table_id": str(target.table_id),
            "table_name": target_table.name,
            "display_label": label
            if isinstance(label, str) and label.strip()
            else f"记录 {target.id}",
        }
    snapshot = {
        "fields": field_snapshot,
        "links": links,
        "target_tables": {str(t.id): t.name for t in tables.values()},
    }
    db.add(
        DataChange(
            table_id=table_id,
            entity_type=entity_type,
            entity_id=entity_id,
            field_id=entity_id if entity_type == "field" else None,
            record_id=entity_id if entity_type == "record" else None,
            action=action,
            before=before,
            after=after,
            actor_id=actor.id,
            actor_role=actor.role,
            schema_version=schema_version,
            revision=revision,
            creation_request=creation_request,
            display_snapshot=snapshot,
        )
    )
