"""Master-data HTTP endpoints — thin adapters over `domains.masterdata.service`.

URL space `/api/data/*` — 100% compatible with v2's `routes/data.py`.

Role policy (mirrors v2):
- Read: superadmin | admin | employee  (`Writer` alias covers these three)
- Write: superadmin | admin  (`Admin` alias)
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile, status
from pydantic import BaseModel, Field

from apps.http._deps import Admin, DbDep, ProductUploader, Writer
from domains.masterdata import service
from domains.masterdata.schemas import (
    CategoryCreate,
    CategoryUpdate,
    CountryCreate,
    CountryUpdate,
    ExchangeRateCreate,
    ExchangeRateUpdate,
    FetchRatesRequest,
    PortCreate,
    PortUpdate,
    ProductCreate,
    ProductImageReorderBody,
    ProductImageUpdate,
    ProductPricePeriodCreate,
    ProductPricePeriodUpdate,
    ProductUpdate,
    SupplierCreate,
    SupplierUpdate,
)

router = APIRouter(prefix="/data", tags=["data"])


def _translate(exc: service.MasterdataError) -> HTTPException:
    if isinstance(exc, service.NotFound):
        return HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
    if isinstance(exc, service.Conflict):
        return HTTPException(status.HTTP_409_CONFLICT, str(exc))
    if isinstance(exc, service.BadRequest):
        return HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    if isinstance(exc, service.UpstreamUnavailable):
        return HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc))
    return HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(exc))


# ═════════ Countries ═════════════════════════════════════════


@router.get("/countries")
def list_countries(db: DbDep, _reader: Writer) -> list[dict[str, Any]]:
    return service.list_countries(db)


@router.post("/countries", status_code=201)
def create_country(body: CountryCreate, db: DbDep, _admin: Admin) -> dict[str, Any]:
    try:
        return service.create_country(db, body)
    except service.MasterdataError as exc:
        raise _translate(exc) from exc


@router.patch("/countries/{country_id}")
def update_country(
    country_id: int, body: CountryUpdate, db: DbDep, _admin: Admin
) -> dict[str, Any]:
    try:
        return service.update_country(db, country_id, body)
    except service.MasterdataError as exc:
        raise _translate(exc) from exc


@router.delete("/countries/{country_id}", status_code=204)
def delete_country(country_id: int, db: DbDep, _admin: Admin):
    try:
        service.delete_country(db, country_id)
    except service.MasterdataError as exc:
        raise _translate(exc) from exc


# ═════════ Categories ════════════════════════════════════════


@router.get("/categories")
def list_categories(db: DbDep, _reader: Writer) -> list[dict[str, Any]]:
    return service.list_categories(db)


@router.post("/categories", status_code=201)
def create_category(body: CategoryCreate, db: DbDep, _admin: Admin) -> dict[str, Any]:
    try:
        return service.create_category(db, body)
    except service.MasterdataError as exc:
        raise _translate(exc) from exc


@router.patch("/categories/{category_id}")
def update_category(
    category_id: int, body: CategoryUpdate, db: DbDep, _admin: Admin
) -> dict[str, Any]:
    try:
        return service.update_category(db, category_id, body)
    except service.MasterdataError as exc:
        raise _translate(exc) from exc


@router.delete("/categories/{category_id}", status_code=204)
def delete_category(category_id: int, db: DbDep, _admin: Admin):
    try:
        service.delete_category(db, category_id)
    except service.MasterdataError as exc:
        raise _translate(exc) from exc


# ═════════ Ports ═════════════════════════════════════════════


@router.get("/ports")
def list_ports(db: DbDep, _reader: Writer) -> list[dict[str, Any]]:
    return service.list_ports(db)


@router.post("/ports", status_code=201)
def create_port(body: PortCreate, db: DbDep, _admin: Admin) -> dict[str, Any]:
    try:
        return service.create_port(db, body)
    except service.MasterdataError as exc:
        raise _translate(exc) from exc


@router.patch("/ports/{port_id}")
def update_port(port_id: int, body: PortUpdate, db: DbDep, _admin: Admin) -> dict[str, Any]:
    try:
        return service.update_port(db, port_id, body)
    except service.MasterdataError as exc:
        raise _translate(exc) from exc


@router.delete("/ports/{port_id}", status_code=204)
def delete_port(port_id: int, db: DbDep, _admin: Admin):
    try:
        service.delete_port(db, port_id)
    except service.MasterdataError as exc:
        raise _translate(exc) from exc


# ═════════ Suppliers ═════════════════════════════════════════


@router.get("/suppliers")
def list_suppliers(db: DbDep, _reader: Writer) -> list[dict[str, Any]]:
    return service.list_suppliers(db)


@router.post("/suppliers", status_code=201)
def create_supplier(body: SupplierCreate, db: DbDep, _admin: Admin) -> dict[str, Any]:
    try:
        return service.create_supplier(db, body)
    except service.MasterdataError as exc:
        raise _translate(exc) from exc


@router.patch("/suppliers/{supplier_id}")
def update_supplier(
    supplier_id: int, body: SupplierUpdate, db: DbDep, _admin: Admin
) -> dict[str, Any]:
    try:
        return service.update_supplier(db, supplier_id, body)
    except service.MasterdataError as exc:
        raise _translate(exc) from exc


@router.delete("/suppliers/{supplier_id}", status_code=204)
def delete_supplier(supplier_id: int, db: DbDep, _admin: Admin):
    try:
        service.delete_supplier(db, supplier_id)
    except service.MasterdataError as exc:
        raise _translate(exc) from exc


# ═════════ Products ══════════════════════════════════════════


@router.get("/products")
def list_products(
    db: DbDep,
    _reader: Writer,
    search: str | None = Query(None),
    country_id: int | None = Query(None),
    port_id: int | None = Query(None),
    category_id: int | None = Query(None),
    supplier_id: int | None = Query(None),
    is_effective: bool | None = Query(
        None,
        description=(
            "True = only 有效 products (status AND (effective_to IS NULL OR "
            "effective_to >= today)); False = only 无效 (manual disable OR "
            "expired); omit = all."
        ),
    ),
    limit: int = Query(20, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    return service.list_products(
        db,
        search=search,
        country_id=country_id,
        port_id=port_id,
        category_id=category_id,
        supplier_id=supplier_id,
        is_effective=is_effective,
        limit=limit,
        offset=offset,
    )


@router.post("/products", status_code=201)
def create_product(body: ProductCreate, db: DbDep, _admin: Admin) -> dict[str, Any]:
    try:
        return service.create_product(db, body, actor_id=_admin.id, source="http")
    except service.MasterdataError as exc:
        raise _translate(exc) from exc


@router.patch("/products/{product_id}")
def update_product(
    product_id: int, body: ProductUpdate, db: DbDep, _admin: Admin
) -> dict[str, Any]:
    try:
        if body.expected_revision is None:
            raise service.BadRequest("请刷新产品后提交，缺少产品版本")
        return service.update_product(db, product_id, body, actor_id=_admin.id, source="http")
    except service.MasterdataError as exc:
        raise _translate(exc) from exc


@router.delete("/products/{product_id}", status_code=204)
def delete_product(product_id: int, db: DbDep, _admin: Admin, expected_revision: int = Query(..., ge=1)):
    try:
        service.delete_product(db, product_id, actor_id=_admin.id, source="http", expected_revision=expected_revision)
    except service.MasterdataError as exc:
        raise _translate(exc) from exc


@router.get("/products/{product_id}/price-periods")
def list_product_price_periods(
    product_id: int, db: DbDep, _reader: Writer
) -> list[dict[str, Any]]:
    try:
        return service.list_product_price_periods(db, product_id)
    except service.MasterdataError as exc:
        raise _translate(exc) from exc


@router.post("/products/{product_id}/price-periods", status_code=201)
def create_product_price_period(
    product_id: int,
    body: ProductPricePeriodCreate,
    db: DbDep,
    admin: Admin,
) -> dict[str, Any]:
    try:
        return service.create_product_price_period(
            db, product_id, body, actor_id=admin.id
        )
    except service.MasterdataError as exc:
        raise _translate(exc) from exc


@router.patch("/products/{product_id}/price-periods/{period_id}")
def update_product_price_period(
    product_id: int,
    period_id: int,
    body: ProductPricePeriodUpdate,
    db: DbDep,
    admin: Admin,
) -> dict[str, Any]:
    try:
        return service.update_product_price_period(
            db, product_id, period_id, body, actor_id=admin.id
        )
    except service.MasterdataError as exc:
        raise _translate(exc) from exc


@router.delete("/products/{product_id}/price-periods/{period_id}", status_code=204)
def deactivate_product_price_period(
    product_id: int, period_id: int, db: DbDep, admin: Admin
):
    try:
        service.deactivate_product_price_period(
            db, product_id, period_id, actor_id=admin.id
        )
    except service.MasterdataError as exc:
        raise _translate(exc) from exc


# ═════════ Product images (R5 2026-06-22) ═══════════════════════

class RestoreProductPrice(BaseModel):
    event_id: str = Field(min_length=32, max_length=32)
    expected_revision: int = Field(ge=1)


@router.get("/products/{product_id}/price-history")
def product_price_history(
    product_id: int, db: DbDep, _reader: Writer,
    limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0),
    max_version: int | None = Query(None, ge=0), field: str | None = None,
    date_from: datetime | None = None, date_to: datetime | None = None, legacy: bool = False,
) -> dict[str, Any]:
    from domains.masterdata.price_history import list_history
    try:
        return list_history(db, product_id, limit=limit, offset=offset, max_version=max_version,
                            field=field, date_from=date_from, date_to=date_to, legacy=legacy)
    except service.MasterdataError as exc:
        raise _translate(exc) from exc


@router.post("/products/{product_id}/price-history/restore")
def restore_product_price(product_id: int, body: RestoreProductPrice, db: DbDep, _admin: Admin):
    from domains.masterdata.price_history import restore
    try:
        return restore(db, product_id, body.event_id, body.expected_revision, actor_id=_admin.id)
    except service.MasterdataError as exc:
        raise _translate(exc) from exc

#
# Five endpoints, all scoped under /products/{product_id}/images, mirror
# the standard "child resource" REST pattern. We use `Writer` (not
# `Admin`) for read+write because Felix's whole team should be able to
# tag a photo to a product — this isn't superadmin territory.


@router.get("/products/{product_id}/images")
def list_product_images(
    product_id: int, db: DbDep, _r: Writer
) -> list[dict[str, Any]]:
    try:
        return service.list_product_images(db, product_id=product_id)
    except service.MasterdataError as exc:
        raise _translate(exc) from exc


@router.post("/products/{product_id}/images", status_code=201)
async def upload_product_image(
    product_id: int,
    db: DbDep,
    user: Writer,
    file: UploadFile = File(...),
) -> dict[str, Any]:
    # Read in memory: bounded by MAX_IMAGE_BYTES (5 MB) which sits well
    # below Cloud Run's request cap. If we ever support multi-MB raw
    # photos (Phase 2), switch to streaming-via-Pillow.
    content = await file.read()
    try:
        return service.add_product_image(
            db,
            product_id=product_id,
            content=content,
            filename=file.filename or "image.jpg",
            content_type=file.content_type or "application/octet-stream",
            user_id=user.id,
        )
    except service.MasterdataError as exc:
        raise _translate(exc) from exc


@router.patch("/products/{product_id}/images/{image_id}")
def patch_product_image(
    product_id: int,
    image_id: int,
    body: ProductImageUpdate,
    db: DbDep,
    _w: Writer,
) -> dict[str, Any]:
    try:
        return service.update_product_image(
            db,
            product_id=product_id,
            image_id=image_id,
            alt_text=body.alt_text,
        )
    except service.MasterdataError as exc:
        raise _translate(exc) from exc


@router.put("/products/{product_id}/images/reorder")
def reorder_product_images(
    product_id: int,
    body: ProductImageReorderBody,
    db: DbDep,
    _w: Writer,
) -> list[dict[str, Any]]:
    try:
        return service.reorder_product_images(
            db, product_id=product_id, entries=body.items
        )
    except service.MasterdataError as exc:
        raise _translate(exc) from exc


@router.delete("/products/{product_id}/images/{image_id}", status_code=204)
def delete_product_image_endpoint(
    product_id: int, image_id: int, db: DbDep, _w: Writer
):
    try:
        service.delete_product_image(
            db, product_id=product_id, image_id=image_id
        )
    except service.MasterdataError as exc:
        raise _translate(exc) from exc


# ═════════ Exchange rates ════════════════════════════════════


@router.get("/exchange-rates")
def list_exchange_rates(
    db: DbDep,
    _reader: Writer,
    from_currency: str | None = Query(None),
    to_currency: str | None = Query(None),
) -> list[dict[str, Any]]:
    return service.list_exchange_rates(db, from_currency=from_currency, to_currency=to_currency)


@router.post("/exchange-rates", status_code=201)
def create_exchange_rate(body: ExchangeRateCreate, db: DbDep, _admin: Admin) -> dict[str, Any]:
    try:
        return service.create_exchange_rate(db, body)
    except service.MasterdataError as exc:
        raise _translate(exc) from exc


@router.patch("/exchange-rates/{rate_id}")
def update_exchange_rate(
    rate_id: int, body: ExchangeRateUpdate, db: DbDep, _admin: Admin
) -> dict[str, Any]:
    try:
        return service.update_exchange_rate(db, rate_id, body)
    except service.MasterdataError as exc:
        raise _translate(exc) from exc


@router.delete("/exchange-rates/{rate_id}", status_code=204)
def delete_exchange_rate(rate_id: int, db: DbDep, _admin: Admin):
    try:
        service.delete_exchange_rate(db, rate_id)
    except service.MasterdataError as exc:
        raise _translate(exc) from exc


@router.post("/exchange-rates/fetch")
def fetch_exchange_rates(body: FetchRatesRequest, db: DbDep, _admin: Admin) -> dict[str, Any]:
    try:
        result = service.fetch_exchange_rates(
            db, base=body.base_currency, targets=body.target_currencies
        )
    except service.MasterdataError as exc:
        raise _translate(exc) from exc
    return result.model_dump()


# ═════ Bulk Image Upload — ZIP-based product image ingestion ═══════
#
# Three endpoints + one polling read:
#   GET    /bulk-images/template       — download empty ZIP template
#   POST   /bulk-images/preview        — upload filled ZIP, get preview
#   POST   /bulk-images/{id}/commit    — kick off async ingestion
#   DELETE /bulk-images/{id}           — cancel before commit
#   GET    /bulk-images/{id}           — read state (polling)
#
# Auth: Writer (any role) can upload + commit; ownership checked in service.
# See `domains/masterdata/images/bulk_service.py` for the lifecycle.


from fastapi.responses import Response  # noqa: E402

from domains.masterdata.images import bulk_service, bulk_upload, direct_service  # noqa: E402
from infrastructure.jobs.runner import get_job_runner  # noqa: E402


def _translate_bulk(exc: bulk_service.BulkImageError) -> HTTPException:
    if isinstance(exc, bulk_service.NotFound):
        return HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
    if isinstance(exc, bulk_service.BadRequest):
        return HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    if isinstance(exc, bulk_service.StatusConflict):
        return HTTPException(status.HTTP_409_CONFLICT, str(exc))
    return HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(exc))


class DirectImageRowUpdate(BaseModel):
    product_id: int | None = None
    decision: str | None = None


class DirectImagePlanUpdate(BaseModel):
    items: list[str] = Field(min_length=1, max_length=30)
    expected_revision: int = Field(ge=1)


@router.post("/bulk-images/direct", status_code=201)
def create_direct_image_batch(db: DbDep, user: ProductUploader) -> dict[str, Any]:
    try:
        return direct_service.create_batch(db, user_id=user.id)
    except bulk_service.BulkImageError as exc:
        raise _translate_bulk(exc) from exc


@router.get("/bulk-images/active")
def get_active_direct_image_batch(
    db: DbDep, user: ProductUploader
) -> dict[str, Any] | None:
    return direct_service.get_active(
        db,
        user_id=user.id,
        is_admin=user.role in ("superadmin", "admin"),
    )


@router.get("/bulk-images")
def list_direct_image_batches(
    db: DbDep, user: ProductUploader, limit: int = Query(20, ge=1, le=100)
) -> dict[str, Any]:
    return {"items": direct_service.list_history(db, user_id=user.id, limit=limit)}


@router.post("/bulk-images/{batch_id}/files", status_code=201)
async def upload_direct_image_file(
    batch_id: int,
    db: DbDep,
    user: ProductUploader,
    file: UploadFile = File(...),
    product_id: int | None = Form(None),
) -> dict[str, Any]:
    raw = await file.read()
    try:
        return direct_service.upload_file(
            db,
            batch_id=batch_id,
            user_id=user.id,
            is_admin=user.role in ("superadmin", "admin"),
            filename=file.filename or "image",
            content=raw,
            content_type=file.content_type or "application/octet-stream",
            product_id=product_id,
        )
    except bulk_service.BulkImageError as exc:
        raise _translate_bulk(exc) from exc


@router.post("/bulk-images/{batch_id}/validate")
def validate_direct_image_batch(
    batch_id: int, db: DbDep, user: ProductUploader
) -> dict[str, Any]:
    try:
        return direct_service.validate_batch(
            db,
            batch_id=batch_id,
            user_id=user.id,
            is_admin=user.role in ("superadmin", "admin"),
        )
    except bulk_service.BulkImageError as exc:
        raise _translate_bulk(exc) from exc


@router.patch("/bulk-images/{batch_id}/rows/{row_id}")
def update_direct_image_row(
    batch_id: int,
    row_id: int,
    body: DirectImageRowUpdate,
    db: DbDep,
    user: ProductUploader,
) -> dict[str, Any]:
    try:
        return direct_service.update_row(
            db,
            batch_id=batch_id,
            row_id=row_id,
            user_id=user.id,
            is_admin=user.role in ("superadmin", "admin"),
            product_id=body.product_id,
            decision=body.decision,
        )
    except bulk_service.BulkImageError as exc:
        raise _translate_bulk(exc) from exc


@router.put("/bulk-images/{batch_id}/rows/{row_id}/file")
async def replace_direct_image_row_file(
    batch_id: int,
    row_id: int,
    db: DbDep,
    user: ProductUploader,
    file: UploadFile = File(...),
) -> dict[str, Any]:
    try:
        return direct_service.replace_row_file(
            db,
            batch_id=batch_id,
            row_id=row_id,
            user_id=user.id,
            is_admin=user.role in ("superadmin", "admin"),
            filename=file.filename or "image",
            content=await file.read(),
            content_type=file.content_type or "application/octet-stream",
        )
    except bulk_service.BulkImageError as exc:
        raise _translate_bulk(exc) from exc


@router.put("/bulk-images/{batch_id}/plans/{product_id}")
def save_direct_image_plan(
    batch_id: int,
    product_id: int,
    body: DirectImagePlanUpdate,
    db: DbDep,
    user: ProductUploader,
) -> dict[str, Any]:
    try:
        return direct_service.save_plan(
            db,
            batch_id=batch_id,
            product_id=product_id,
            user_id=user.id,
            is_admin=user.role in ("superadmin", "admin"),
            items=body.items,
            expected_revision=body.expected_revision,
        )
    except bulk_service.BulkImageError as exc:
        raise _translate_bulk(exc) from exc


@router.post("/bulk-images/{batch_id}/rows/{row_id}/retry")
def retry_direct_image_row(
    batch_id: int, row_id: int, db: DbDep, user: ProductUploader
) -> dict[str, Any]:
    try:
        return direct_service.retry_row(
            db,
            batch_id=batch_id,
            row_id=row_id,
            user_id=user.id,
            is_admin=user.role in ("superadmin", "admin"),
        )
    except bulk_service.BulkImageError as exc:
        raise _translate_bulk(exc) from exc


@router.post("/bulk-images/{batch_id}/resume", status_code=202)
def resume_direct_image_batch(
    batch_id: int, db: DbDep, user: ProductUploader
) -> dict[str, Any]:
    try:
        direct_service.resume_batch(
            db,
            batch_id=batch_id,
            user_id=user.id,
            is_admin=user.role in ("superadmin", "admin"),
        )
    except bulk_service.BulkImageError as exc:
        raise _translate_bulk(exc) from exc
    get_job_runner().submit(bulk_service._run_commit_async, batch_id)
    # The production runner returns before the worker starts. The synchronous
    # test runner can finish in another session before returning, so expire the
    # request session to avoid serializing its stale identity-map values.
    db.expire_all()
    return bulk_service.list_batch(
        db,
        batch_id=batch_id,
        user_id=user.id,
        is_admin=user.role in ("superadmin", "admin"),
    )


@router.get("/bulk-images/template")
def download_bulk_image_template(
    db: DbDep,
    user: ProductUploader,
    country_ids: list[int] | None = Query(None),
    port_ids: list[int] | None = Query(None),
    only_missing_images: bool = Query(False),
) -> Response:
    """Generate + return the directory-tree ZIP filtered by the given
    criteria. Empty filters = all products with valid (country, port,
    code) triple.

    Returns 200 with the ZIP body or 404 if the filter produced zero
    products (so the user knows the filter was too narrow).
    """
    del user  # auth only — caller identity isn't used for templating
    zip_bytes = bulk_service.build_template(
        db,
        country_ids=country_ids,
        port_ids=port_ids,
        only_missing_images=only_missing_images,
    )
    if len(zip_bytes) < 100:  # smallest meaningful ZIP is ~50 bytes empty
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "当前筛选条件下没有产品，请放宽筛选",
        )
    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={
            "Content-Disposition": 'attachment; filename="product-images-template.zip"',
        },
    )


@router.post("/bulk-images/preview")
async def preview_bulk_image_upload(
    db: DbDep,
    user: ProductUploader,
    file: UploadFile = File(...),
) -> dict[str, Any]:
    """Upload ZIP → parse + stage → return batch_id + counts.

    Caller follows with GET /bulk-images/{id} for the row-level detail,
    then POST /bulk-images/{id}/commit when satisfied.
    """
    if not file.filename or not file.filename.lower().endswith(".zip"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "必须上传 .zip 文件")
    raw = await file.read()
    if len(raw) > bulk_upload.MAX_UNZIPPED_TOTAL_BYTES:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            "ZIP 文件超过 100MB 上限，请拆分后分批上传",
        )
    try:
        batch = bulk_service.create_preview(
            db,
            user_id=user.id,
            zip_filename=file.filename,
            zip_bytes=raw,
        )
    except bulk_service.BulkImageError as exc:
        raise _translate_bulk(exc) from exc
    return bulk_service.list_batch(
        db,
        batch_id=batch.id,
        user_id=user.id,
        is_admin=user.role in ("superadmin", "admin"),
    )


@router.get("/bulk-images/{batch_id}")
def get_bulk_image_batch(
    batch_id: int, db: DbDep, user: ProductUploader
) -> dict[str, Any]:
    try:
        return bulk_service.list_batch(
            db,
            batch_id=batch_id,
            user_id=user.id,
            is_admin=user.role in ("superadmin", "admin"),
        )
    except bulk_service.BulkImageError as exc:
        raise _translate_bulk(exc) from exc


@router.post("/bulk-images/{batch_id}/commit", status_code=202)
def commit_bulk_image_batch(
    batch_id: int, db: DbDep, user: ProductUploader
) -> dict[str, Any]:
    """Flip status to `processing` synchronously, schedule the
    ingestion job, return 202 + the batch state.

    Frontend polls GET /bulk-images/{id} until status flips to
    `completed` / `error`. ingested_count rises during processing so
    the UI can render a progress bar.
    """
    try:
        bulk_service.trigger_commit(
            db,
            batch_id=batch_id,
            user_id=user.id,
            is_admin=user.role in ("superadmin", "admin"),
        )
    except bulk_service.BulkImageError as exc:
        raise _translate_bulk(exc) from exc

    get_job_runner().submit(bulk_service._run_commit_async, batch_id)
    db.expire_all()
    return bulk_service.list_batch(
        db,
        batch_id=batch_id,
        user_id=user.id,
        is_admin=user.role in ("superadmin", "admin"),
    )


@router.delete("/bulk-images/{batch_id}", status_code=204)
def cancel_bulk_image_batch(
    batch_id: int,
    db: DbDep,
    user: ProductUploader,
    force: bool = Query(
        False,
        description=(
            "Also allow cancelling a `processing` batch. Used by the UI "
            "when polling detects the worker has been silent for 15+ min "
            "(background job died / process restart)."
        ),
    ),
) -> Response:
    try:
        bulk_service.cancel_batch(
            db,
            batch_id=batch_id,
            user_id=user.id,
            is_admin=user.role in ("superadmin", "admin"),
            force=force,
        )
    except bulk_service.BulkImageError as exc:
        raise _translate_bulk(exc) from exc
    return Response(status_code=204)
