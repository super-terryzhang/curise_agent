"""Append history in the caller's transaction; never commit independently."""

from uuid import UUID

from sqlalchemy.orm import Session

from .models import DataChange
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
    snapshot = {
        str(f.id): FieldResponse.model_validate(f).model_dump(mode="json")
        for f in table_fields(db, table_id)
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
