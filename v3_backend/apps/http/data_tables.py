"""Custom tables HTTP adapter: trusted identity, parsing, and safe errors only."""

import json
import logging
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import ValidationError as PydanticValidationError

from apps.http._deps import Admin, DbDep, Writer
from domains.dynamic_data import service
from domains.dynamic_data.errors import (
    Conflict,
    DataTableError,
    Forbidden,
    NotFound,
    ValidationError,
)
from domains.dynamic_data.schemas import (
    Actor,
    ChangeQuery,
    ChangeResponse,
    FieldCreate,
    FieldReorder,
    FieldResponse,
    FieldUpdate,
    Page,
    RecordAction,
    RecordCreate,
    RecordQuery,
    RecordResponse,
    RecordUpdate,
    SchemaAction,
    SystemRecordUpdate,
    TableCreate,
    TableResponse,
    TableUpdate,
)
from infrastructure.config import settings

logger = logging.getLogger(__name__)


class DataTablesRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def handler(request):
            try:
                if not settings.CUSTOM_DATA_TABLES_ENABLED:
                    raise HTTPException(
                        503,
                        detail="数据表管理暂未启用，请联系管理员",
                        headers={"Cache-Control": "no-store", "Retry-After": "120"},
                    )
                return await original(request)
            except HTTPException as exc:
                if isinstance(exc.detail, str):
                    codes = {401: "AUTH_REQUIRED", 403: "FORBIDDEN", 503: "MODULE_DISABLED"}
                    raise HTTPException(
                        exc.status_code,
                        detail={
                            "code": codes.get(exc.status_code, "HTTP_ERROR"),
                            "message": exc.detail,
                            "issues": [],
                        },
                        headers=exc.headers,
                    ) from exc
                raise
            except DataTableError as exc:
                status = (
                    409
                    if isinstance(exc, Conflict)
                    else 404
                    if isinstance(exc, NotFound)
                    else 403
                    if isinstance(exc, Forbidden)
                    else 422
                )
                raise HTTPException(
                    status,
                    detail={
                        "code": exc.code,
                        "message": exc.message,
                        "issues": [i.model_dump(mode="json") for i in exc.issues],
                    },
                ) from exc
            except (RequestValidationError, PydanticValidationError) as exc:
                issues = [
                    {
                        "code": "INVALID_INPUT",
                        "field_label": ".".join(str(p) for p in error["loc"]),
                        "message": "输入格式不正确、缺少必填项或提交了不允许修改的字段",
                    }
                    for error in exc.errors()
                ]
                raise HTTPException(
                    422,
                    detail={
                        "code": "INVALID_REQUEST",
                        "message": "输入不合法，请检查标记的字段",
                        "issues": issues,
                    },
                ) from exc
            except Exception as exc:
                # Never log SQL parameters, request values, error str or traceback:
                # driver exceptions can contain the full user record.
                logger.error(
                    "custom_data_request_failed route=%s method=%s exception_type=%s",
                    self.path,
                    request.method,
                    type(exc).__name__,
                )
                raise HTTPException(
                    500,
                    detail={
                        "code": "INTERNAL_ERROR",
                        "message": "处理失败，请保留输入并稍后核对保存结果",
                        "issues": [],
                    },
                ) from exc

        return handler


router = APIRouter(prefix="/data-tables", tags=["data-tables"], route_class=DataTablesRoute)


def actor(user) -> Actor:
    return Actor(id=user.id, role=user.role)


@router.get("", response_model=Page[TableResponse])
def list_tables(
    db: DbDep,
    user: Writer,
    status: Literal["active", "archived"] = "active",
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
):
    return service.list_tables(
        db, status=status, page=page, page_size=page_size, actor=actor(user)
    )


@router.post("", response_model=TableResponse)
def create_table(body: TableCreate, db: DbDep, user: Admin):
    return service.create_table(db, body, actor=actor(user))


@router.get("/{table_id}", response_model=TableResponse)
def get_table(table_id: UUID, db: DbDep, user: Writer):
    return service.get_table(db, table_id, actor=actor(user))


@router.patch("/{table_id}", response_model=TableResponse)
def update_table(table_id: UUID, body: TableUpdate, db: DbDep, user: Admin):
    return service.update_table(db, table_id, body, actor=actor(user))


@router.get("/{table_id}/fields", response_model=list[FieldResponse])
def list_fields(table_id: UUID, db: DbDep, user: Writer, include_archived: bool = True):
    return service.list_fields(db, table_id, include_archived=include_archived)


@router.post("/{table_id}/fields", response_model=FieldResponse)
def create_field(table_id: UUID, body: FieldCreate, db: DbDep, user: Admin):
    return service.create_field(db, table_id, body, actor=actor(user))


@router.post("/{table_id}/fields/reorder", response_model=list[FieldResponse])
def reorder_fields(table_id: UUID, body: FieldReorder, db: DbDep, user: Admin):
    return service.reorder_fields(db, table_id, body, actor=actor(user))


@router.patch("/{table_id}/fields/{field_id}", response_model=FieldResponse)
def update_field(table_id: UUID, field_id: UUID, body: FieldUpdate, db: DbDep, user: Admin):
    return service.update_field(db, table_id, field_id, body, actor=actor(user))


@router.get("/{table_id}/records", response_model=Page[RecordResponse])
def list_records(
    table_id: UUID,
    db: DbDep,
    user: Writer,
    status: Literal["active", "archived"] = "active",
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    sort_field_id: UUID | None = None,
    sort_direction: Literal["asc", "desc"] = "asc",
    filters: str = Query("[]", max_length=65536),
    q: str | None = Query(None, max_length=200),
):
    try:
        parsed = json.loads(filters)
    except (ValueError, TypeError) as exc:
        raise HTTPException(
            422, detail={"code": "INVALID_FILTERS", "message": "筛选条件格式不合法", "issues": []}
        ) from exc
    query = RecordQuery(
        status=status,
        page=page,
        page_size=page_size,
        sort_field_id=sort_field_id,
        sort_direction=sort_direction,
        filters=parsed,
        q=q,
    )
    return service.list_records(db, table_id, query, actor=actor(user))


@router.post("/{table_id}/records", response_model=RecordResponse)
def create_record(table_id: UUID, body: RecordCreate, db: DbDep, user: Writer):
    return service.create_record(db, table_id, body, actor=actor(user))


@router.get("/{table_id}/records/{record_id}", response_model=RecordResponse)
def get_record(table_id: UUID, record_id: str, db: DbDep, user: Writer):
    return service.get_record(db, table_id, record_id, actor=actor(user))


@router.patch("/{table_id}/records/{record_id}", response_model=RecordResponse)
def update_record(
    table_id: UUID,
    record_id: str,
    body: RecordUpdate | SystemRecordUpdate,
    db: DbDep,
    user: Writer,
):
    current_actor = actor(user)
    table = service.get_table(db, table_id, actor=current_actor)
    if table.table_kind == "system":
        if not isinstance(body, SystemRecordUpdate):
            raise ValidationError("INVALID_SYSTEM_UPDATE", "请提交系统记录扩展信息")
        if body.source_record_id != record_id:
            raise ValidationError("SOURCE_ID_MISMATCH", "路径记录编号与提交内容不一致")
        return service.save_system_record(db, table_id, body, actor=current_actor)
    if not isinstance(body, RecordUpdate):
        raise ValidationError("INVALID_RECORD_UPDATE", "普通数据表更新内容不合法")
    try:
        user_record_id = UUID(record_id)
    except ValueError as exc:
        raise NotFound("RECORD_NOT_FOUND", "记录不存在于此数据表") from exc
    return service.update_record(db, table_id, user_record_id, body, actor=current_actor)


@router.get("/{table_id}/fields/{field_id}/targets", response_model=Page[RecordResponse])
def targets(
    table_id: UUID,
    field_id: UUID,
    db: DbDep,
    user: Writer,
    q: str = Query("", max_length=200),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
):
    return service.search_link_targets(db, table_id, field_id, q=q, page=page, page_size=page_size)


@router.get("/{table_id}/changes", response_model=Page[ChangeResponse])
def changes(
    table_id: UUID,
    db: DbDep,
    user: Writer,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    entity_type: Literal["table", "field", "record"] | None = None,
    record_id: str | None = None,
):
    return service.list_changes(
        db,
        table_id,
        ChangeQuery(page=page, page_size=page_size, entity_type=entity_type, record_id=record_id),
        actor=actor(user),
    )


@router.post("/{table_id}/archive", response_model=TableResponse)
def archive_table(table_id: UUID, body: SchemaAction, db: DbDep, user: Admin):
    return service.set_table_status(db, table_id, body, active=False, actor=actor(user))


@router.post("/{table_id}/restore", response_model=TableResponse)
def restore_table(table_id: UUID, body: SchemaAction, db: DbDep, user: Admin):
    return service.set_table_status(db, table_id, body, active=True, actor=actor(user))


@router.post("/{table_id}/fields/{field_id}/archive", response_model=FieldResponse)
def archive_field(table_id: UUID, field_id: UUID, body: SchemaAction, db: DbDep, user: Admin):
    return service.set_field_status(db, table_id, field_id, body, active=False, actor=actor(user))


@router.post("/{table_id}/fields/{field_id}/restore", response_model=FieldResponse)
def restore_field(table_id: UUID, field_id: UUID, body: SchemaAction, db: DbDep, user: Admin):
    return service.set_field_status(db, table_id, field_id, body, active=True, actor=actor(user))


@router.post("/{table_id}/records/{record_id}/archive", response_model=RecordResponse)
def archive_record(table_id: UUID, record_id: str, body: RecordAction, db: DbDep, user: Writer):
    try:
        parsed = UUID(record_id)
    except ValueError as exc:
        raise NotFound("RECORD_NOT_FOUND", "记录不存在于此数据表") from exc
    return service.set_record_status(db, table_id, parsed, body, active=False, actor=actor(user))


@router.post("/{table_id}/records/{record_id}/restore", response_model=RecordResponse)
def restore_record(table_id: UUID, record_id: str, body: RecordAction, db: DbDep, user: Writer):
    try:
        parsed = UUID(record_id)
    except ValueError as exc:
        raise NotFound("RECORD_NOT_FOUND", "记录不存在于此数据表") from exc
    return service.set_record_status(db, table_id, parsed, body, active=True, actor=actor(user))
