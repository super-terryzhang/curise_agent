"""Admin-only temporary surface for preparing the clean product database."""

from __future__ import annotations

import logging
from datetime import date
from io import BytesIO
from typing import Any
from uuid import UUID

from fastapi import APIRouter, File, HTTPException, Query, UploadFile, status
from fastapi.responses import StreamingResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import IntegrityError

from apps.http._deps import Admin, DbDep
from domains.dynamic_data import errors as dynamic_errors
from domains.dynamic_data.schemas import Actor
from domains.masterdata.errors import BadRequest, Conflict, NotFound
from domains.masterdata.schemas import ProductPricePeriodCreate, ProductPricePeriodUpdate
from domains.product_imports import manual, service
from domains.product_imports.commit import ImportConflict
from domains.product_imports.models import ImportBatch
from infrastructure.config import settings

logger = logging.getLogger(__name__)
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class DatabaseSetupRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def handler(request):
            if not settings.TEMP_DATABASE_SETUP_ENABLED:
                raise HTTPException(
                    status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail={
                        "code": "MODULE_DISABLED",
                        "message": "临时数据库整理页面未启用",
                        "issues": [],
                    },
                    headers={"Cache-Control": "no-store"},
                )
            try:
                return await original(request)
            except manual.ManualValidation as exc:
                raise HTTPException(
                    status_code=422,
                    detail={"code": "VALIDATION_FAILED", "message": str(exc), "issues": exc.issues},
                ) from exc
            except (Conflict, dynamic_errors.Conflict, IntegrityError) as exc:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "DATA_CONFLICT",
                        "message": str(exc)
                        if isinstance(exc, (Conflict, dynamic_errors.Conflict))
                        else "数据冲突，请刷新后检查关联记录",
                        "issues": [],
                    },
                ) from exc
            except BadRequest as exc:
                raise HTTPException(
                    status_code=422,
                    detail={"code": "VALIDATION_FAILED", "message": str(exc), "issues": []},
                ) from exc
            except dynamic_errors.ValidationError as exc:
                raise HTTPException(
                    status_code=422,
                    detail={"code": exc.code, "message": str(exc), "issues": []},
                ) from exc
            except NotFound as exc:
                raise HTTPException(
                    status_code=404, detail={"code": "NOT_FOUND", "message": str(exc), "issues": []}
                ) from exc
            except ImportConflict as exc:
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    detail={"code": "IMPORT_CONFLICT", "message": str(exc), "issues": []},
                ) from exc
            except ValueError as exc:
                raise HTTPException(
                    status.HTTP_404_NOT_FOUND,
                    detail={"code": "NOT_FOUND", "message": str(exc), "issues": []},
                ) from exc

        return handler


router = APIRouter(
    prefix="/database-setup",
    tags=["database-setup"],
    route_class=DatabaseSetupRoute,
)


@router.get("/status")
def status_info(db: DbDep, user: Admin):
    return service.setup_status(db, database_name=settings.TEMP_DATABASE_SETUP_EXPECTED_DATABASE)


@router.get("/product-template")
def download_product_template(
    db: DbDep,
    user: Admin,
    include_existing: bool = False,
    product_ids: list[int] | None = Query(None),
):
    blob = service.build_product_workbook(
        db,
        Actor(id=user.id, role=user.role),
        include_existing=include_existing,
        product_ids=product_ids,
    )
    filename = "products-with-prices.xlsx" if include_existing else "product-import-template.xlsx"
    return StreamingResponse(
        BytesIO(blob),
        media_type=XLSX_MIME,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


@router.post("/imports", status_code=status.HTTP_201_CREATED)
async def upload_import(db: DbDep, user: Admin, file: UploadFile = File(...)):
    filename = (file.filename or "").strip()
    if not filename.lower().endswith(".xlsx"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "只接受 .xlsx 文件")
    blob = await file.read()
    if not blob:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "文件为空")
    if len(blob) > min(settings.MAX_UPLOAD_SIZE, 8 * 1024 * 1024):
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "文件超过 8 MB")
    batch = service.parse_workbook(db, blob, filename, user.id)
    return service.batch_summary(batch)


@router.post("/imports/{batch_id}/validate")
def validate_import(batch_id: UUID, db: DbDep, user: Admin):
    service.validate_batch(db, batch_id, user.id)
    batch = db.get(ImportBatch, batch_id)
    if batch is None:
        raise ValueError("导入批次不存在")
    return service.batch_summary(batch)


@router.get("/imports/{batch_id}/rows")
def import_rows(
    batch_id: UUID,
    db: DbDep,
    user: Admin,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    return service.list_batch_rows(db, batch_id, user.id, page=page, page_size=page_size)


@router.post("/imports/{batch_id}/commit")
def commit_import(batch_id: UUID, db: DbDep, user: Admin):
    return service.commit_batch(db, batch_id, user.id).model_dump(mode="json")


@router.post("/imports/{batch_id}/rollback")
def rollback_import(batch_id: UUID, db: DbDep, user: Admin):
    return service.rollback_batch(db, batch_id, user.id).model_dump(mode="json")


@router.get("/products")
def products(
    db: DbDep,
    user: Admin,
    q: str | None = Query(None, max_length=100),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    return service.list_products(db, q=q, page=page, page_size=page_size)


@router.get("/products/{product_id}")
def product_detail(product_id: int, db: DbDep, user: Admin):
    return service.get_product(db, product_id)


class ProductEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)
    schema_version: int = Field(ge=1)
    extension_revision: int | None = Field(default=None, ge=0)
    values: dict[str, Any]


class RevisionAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)


class ProductDelete(RevisionAction):
    confirm_code: str
    expected_extension_revision: int = Field(default=0, ge=0)


class PeriodCreate(ProductPricePeriodCreate):
    model_config = ConfigDict(extra="forbid")
    currency: str = Field(pattern=r"^[A-Z]{3}$")


class PeriodEdit(ProductPricePeriodUpdate, RevisionAction):
    model_config = ConfigDict(extra="forbid")
    # The form submits the complete period; null amounts/dates are not accepted.
    amount: float = Field(ge=0)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    effective_from: date
    effective_to: date


@router.get("/products/{product_id}/edit-config")
def product_edit_config(product_id: int, db: DbDep, user: Admin):
    return manual.edit_config(db, product_id)


@router.patch("/products/{product_id}")
def edit_product(product_id: int, body: ProductEdit, db: DbDep, user: Admin):
    return manual.update_product(db, product_id, body, user.id)


@router.get("/products/{product_id}/deletion")
def preview_product_delete(product_id: int, db: DbDep, user: Admin):
    return manual.deletion_preview(db, product_id)


@router.delete("/products/{product_id}")
def remove_product(product_id: int, body: ProductDelete, db: DbDep, user: Admin):
    return manual.delete_product(db, product_id, body, user.id)


@router.post("/products/{product_id}/periods", status_code=201)
def create_product_period(product_id: int, body: PeriodCreate, db: DbDep, user: Admin):
    return manual.save_period(db, product_id, body, user.id)


@router.patch("/products/{product_id}/periods/{period_id}")
def edit_product_period(product_id: int, period_id: int, body: PeriodEdit, db: DbDep, user: Admin):
    return manual.save_period(db, product_id, body, user.id, period_id)


@router.delete("/products/{product_id}/periods/{period_id}")
def remove_product_period(
    product_id: int, period_id: int, body: RevisionAction, db: DbDep, user: Admin
):
    return manual.delete_period(db, product_id, period_id, body.expected_revision, user.id)
