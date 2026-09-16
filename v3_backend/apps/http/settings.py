"""Settings HTTP endpoints — `/api/settings/*`.

Compatible with v2 frontend's `settings-api.ts`. Phase 4 ships:
- field-schemas CRUD + seed-defaults
- order-templates CRUD + infer + analyze-pdf
- supplier-templates CRUD + upload-file + analyze (delegates to inquiry domain)
- delivery-locations CRUD
- company-config read/update
- countries (read-only mirror of /api/data/countries)
- tools / skills empty stubs (Phase 6 will fill)

Removed 2026-05-28: `/api/settings/suppliers` GET + PATCH. Supplier info
(including inquiry-letterhead fields like address/zip_code/fax/payment_*)
is now edited exclusively via `/api/data/suppliers/{id}` PATCH. The split
was an accident of history — same `suppliers` table, two write surfaces,
no source of truth for the overlapping fields (contact/email/phone).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile, status

from apps.http._deps import Admin, DbDep
from domains.inquiry import service as inquiry_service
from domains.inquiry.errors import (
    BadRequest as InquiryBadRequest,
)
from domains.inquiry.errors import InquiryError
from domains.inquiry.errors import (
    NotFound as InquiryNotFound,
)
from domains.inquiry.schemas import (
    SupplierTemplateCreate,
    SupplierTemplateResponse,
    SupplierTemplateUpdate,
)
from domains.masterdata import repository as md_repo
from domains.settings import service
from domains.settings.errors import BadRequest, Conflict, NotFound, SettingsError
from domains.settings.schemas import (
    CompanyConfigResponse,
    CompanyConfigUpdate,
    DeliveryLocationCreate,
    DeliveryLocationResponse,
    DeliveryLocationUpdate,
    FieldDefinitionCreate,
    FieldDefinitionResponse,
    FieldDefinitionUpdate,
    FieldSchemaCreate,
    FieldSchemaResponse,
    OrderFormatTemplateCreate,
    OrderFormatTemplateResponse,
    OrderFormatTemplateUpdate,
    PdfAnalysisResponse,
    TemplateInferRequest,
    TemplateInferResponse,
)
from infrastructure.storage import get_storage

router = APIRouter(prefix="/settings", tags=["settings"])


def _translate(exc: SettingsError) -> HTTPException:
    if isinstance(exc, NotFound):
        return HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
    if isinstance(exc, Conflict):
        return HTTPException(status.HTTP_409_CONFLICT, str(exc))
    if isinstance(exc, BadRequest):
        return HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    return HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(exc))


def _translate_inquiry(exc: InquiryError) -> HTTPException:
    if isinstance(exc, InquiryNotFound):
        return HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
    if isinstance(exc, InquiryBadRequest):
        return HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    return HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(exc))


# ═════ Field Schemas ═══════════════════════════════════════════


@router.get("/field-schemas", response_model=list[FieldSchemaResponse])
def list_field_schemas(db: DbDep, _admin: Admin) -> list[FieldSchemaResponse]:
    return [FieldSchemaResponse.model_validate(s) for s in service.list_field_schemas(db)]


@router.post(
    "/field-schemas", response_model=FieldSchemaResponse, status_code=status.HTTP_201_CREATED
)
def create_field_schema(body: FieldSchemaCreate, db: DbDep, admin: Admin) -> FieldSchemaResponse:
    schema = service.create_field_schema(db, body, created_by=admin.id)
    return FieldSchemaResponse.model_validate(schema)


@router.get("/field-schemas/{schema_id}", response_model=FieldSchemaResponse)
def get_field_schema(schema_id: int, db: DbDep, _admin: Admin) -> FieldSchemaResponse:
    try:
        return FieldSchemaResponse.model_validate(service.get_field_schema(db, schema_id))
    except SettingsError as exc:
        raise _translate(exc) from exc


@router.put("/field-schemas/{schema_id}", response_model=FieldSchemaResponse)
def update_field_schema(
    schema_id: int, body: FieldSchemaCreate, db: DbDep, _admin: Admin
) -> FieldSchemaResponse:
    try:
        return FieldSchemaResponse.model_validate(service.update_field_schema(db, schema_id, body))
    except SettingsError as exc:
        raise _translate(exc) from exc


@router.delete("/field-schemas/{schema_id}")
def delete_field_schema(schema_id: int, db: DbDep, _admin: Admin) -> dict[str, str]:
    try:
        service.delete_field_schema(db, schema_id)
    except SettingsError as exc:
        raise _translate(exc) from exc
    return {"detail": "已删除"}


@router.post("/field-schemas/seed-defaults", response_model=FieldSchemaResponse)
def seed_default_field_schema(db: DbDep, admin: Admin) -> FieldSchemaResponse:
    return FieldSchemaResponse.model_validate(
        service.seed_default_field_schema(db, created_by=admin.id)
    )


# ─── Field Definitions ────────────────────────────────────────


@router.post(
    "/field-schemas/{schema_id}/definitions",
    response_model=FieldDefinitionResponse,
    status_code=status.HTTP_201_CREATED,
)
def add_field_definition(
    schema_id: int, body: FieldDefinitionCreate, db: DbDep, _admin: Admin
) -> FieldDefinitionResponse:
    try:
        return FieldDefinitionResponse.model_validate(
            service.add_field_definition(db, schema_id, body)
        )
    except SettingsError as exc:
        raise _translate(exc) from exc


@router.put(
    "/field-schemas/{schema_id}/definitions/{def_id}",
    response_model=FieldDefinitionResponse,
)
def update_field_definition(
    schema_id: int,
    def_id: int,
    body: FieldDefinitionUpdate,
    db: DbDep,
    _admin: Admin,
) -> FieldDefinitionResponse:
    try:
        return FieldDefinitionResponse.model_validate(
            service.update_field_definition(db, schema_id, def_id, body)
        )
    except SettingsError as exc:
        raise _translate(exc) from exc


@router.delete("/field-schemas/{schema_id}/definitions/{def_id}")
def delete_field_definition(
    schema_id: int, def_id: int, db: DbDep, _admin: Admin
) -> dict[str, str]:
    try:
        service.delete_field_definition(db, schema_id, def_id)
    except SettingsError as exc:
        raise _translate(exc) from exc
    return {"detail": "已删除"}


# ═════ Order Format Templates ══════════════════════════════════


@router.post("/order-templates/infer", response_model=TemplateInferResponse)
def infer_order_template(body: TemplateInferRequest, _admin: Admin) -> TemplateInferResponse:
    return TemplateInferResponse(**service.infer_order_template_meta(body))


@router.post("/order-templates/analyze-pdf", response_model=PdfAnalysisResponse)
async def analyze_pdf_template(
    _admin: Admin,
    file: UploadFile = File(...),
) -> PdfAnalysisResponse:
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "请上传 .pdf 文件")
    content = await file.read()
    if not content:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "文件为空")
    storage = get_storage()
    file_url = storage.upload("templates", file.filename, content, content_type="application/pdf")
    result = service.analyze_order_template_pdf(content)
    result["sample_file_url"] = file_url
    return PdfAnalysisResponse(**result)


@router.get("/order-templates", response_model=list[OrderFormatTemplateResponse])
def list_order_templates(db: DbDep, _admin: Admin) -> list[OrderFormatTemplateResponse]:
    return [OrderFormatTemplateResponse.model_validate(t) for t in service.list_order_templates(db)]


@router.post(
    "/order-templates",
    response_model=OrderFormatTemplateResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_order_template(
    body: OrderFormatTemplateCreate, db: DbDep, admin: Admin
) -> OrderFormatTemplateResponse:
    return OrderFormatTemplateResponse.model_validate(
        service.create_order_template(db, body, created_by=admin.id)
    )


@router.get("/order-templates/{tpl_id}", response_model=OrderFormatTemplateResponse)
def get_order_template(tpl_id: int, db: DbDep, _admin: Admin) -> OrderFormatTemplateResponse:
    try:
        return OrderFormatTemplateResponse.model_validate(service.get_order_template(db, tpl_id))
    except SettingsError as exc:
        raise _translate(exc) from exc


@router.put("/order-templates/{tpl_id}", response_model=OrderFormatTemplateResponse)
def update_order_template(
    tpl_id: int,
    body: OrderFormatTemplateUpdate,
    db: DbDep,
    _admin: Admin,
) -> OrderFormatTemplateResponse:
    try:
        return OrderFormatTemplateResponse.model_validate(
            service.update_order_template(db, tpl_id, body)
        )
    except SettingsError as exc:
        raise _translate(exc) from exc


@router.delete("/order-templates/{tpl_id}")
def delete_order_template(tpl_id: int, db: DbDep, _admin: Admin) -> dict[str, str]:
    try:
        service.delete_order_template(db, tpl_id)
    except SettingsError as exc:
        raise _translate(exc) from exc
    return {"detail": "已删除"}


# ═════ Supplier Templates (delegated to inquiry domain) ════════


@router.get("/supplier-templates", response_model=list[SupplierTemplateResponse])
def list_supplier_templates(
    db: DbDep,
    _admin: Admin,
    supplier_id: int | None = Query(default=None),
    include_legacy: bool = Query(default=False),  # noqa: ARG001 (Phase 5 will use)
) -> list[SupplierTemplateResponse]:
    return [
        SupplierTemplateResponse.model_validate(t)
        for t in inquiry_service.list_supplier_templates(db, supplier_id=supplier_id)
    ]


@router.post(
    "/supplier-templates",
    response_model=SupplierTemplateResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_supplier_template(
    body: SupplierTemplateCreate, db: DbDep, admin: Admin
) -> SupplierTemplateResponse:
    return SupplierTemplateResponse.model_validate(
        inquiry_service.create_supplier_template(db, body, created_by=admin.id)
    )


@router.get("/supplier-templates/{tpl_id}", response_model=SupplierTemplateResponse)
def get_supplier_template(tpl_id: int, db: DbDep, _admin: Admin) -> SupplierTemplateResponse:
    try:
        return SupplierTemplateResponse.model_validate(
            inquiry_service.get_supplier_template(db, tpl_id)
        )
    except InquiryError as exc:
        raise _translate_inquiry(exc) from exc


@router.put("/supplier-templates/{tpl_id}", response_model=SupplierTemplateResponse)
def update_supplier_template(
    tpl_id: int,
    body: SupplierTemplateUpdate,
    db: DbDep,
    _admin: Admin,
) -> SupplierTemplateResponse:
    try:
        return SupplierTemplateResponse.model_validate(
            inquiry_service.update_supplier_template(db, tpl_id, body)
        )
    except InquiryError as exc:
        raise _translate_inquiry(exc) from exc


@router.delete("/supplier-templates/{tpl_id}")
def delete_supplier_template(tpl_id: int, db: DbDep, _admin: Admin) -> dict[str, str]:
    try:
        inquiry_service.delete_supplier_template(db, tpl_id)
    except InquiryError as exc:
        raise _translate_inquiry(exc) from exc
    return {"detail": "已删除"}


@router.post("/supplier-templates/{tpl_id}/upload-file", response_model=SupplierTemplateResponse)
async def upload_supplier_template_file(
    tpl_id: int,
    db: DbDep,
    _admin: Admin,
    file: UploadFile = File(...),
) -> SupplierTemplateResponse:
    if not file.filename:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "文件名不能为空")
    content = await file.read()
    try:
        tpl = inquiry_service.upload_supplier_template_file(
            db, tpl_id, filename=file.filename, content=content
        )
    except InquiryError as exc:
        raise _translate_inquiry(exc) from exc
    return SupplierTemplateResponse.model_validate(tpl)


@router.post("/supplier-templates/analyze")
async def analyze_supplier_template(
    _admin: Admin,
    file: UploadFile = File(...),
    order_template_id: int | None = Form(None),
) -> dict[str, Any]:
    if not file.filename:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "文件名不能为空")
    content = await file.read()
    try:
        return inquiry_service.analyze_supplier_template(
            filename=file.filename,
            content=content,
            order_template_id=order_template_id,
        )
    except InquiryError as exc:
        raise _translate_inquiry(exc) from exc


# ═════ Countries (read-only mirror) ═════════════════════════════


@router.get("/countries")
def list_countries(db: DbDep, _admin: Admin) -> list[dict[str, Any]]:
    return [{"id": c.id, "name": c.name, "code": c.code} for c in md_repo.list_countries(db)]


# ═════ Delivery Locations ═══════════════════════════════════════


@router.get("/delivery-locations", response_model=list[DeliveryLocationResponse])
def list_delivery_locations(
    db: DbDep,
    _admin: Admin,
    port_id: int | None = Query(default=None),
) -> list[DeliveryLocationResponse]:
    rows = service.list_delivery_locations(db, port_id=port_id)
    out: list[DeliveryLocationResponse] = []
    for loc in rows:
        data = DeliveryLocationResponse.model_validate(loc)
        if loc.port_id:
            port = md_repo.get_port(db, loc.port_id)
            if port:
                data.port_name = port.name
        out.append(data)
    return out


@router.post(
    "/delivery-locations",
    response_model=DeliveryLocationResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_delivery_location(
    body: DeliveryLocationCreate, db: DbDep, admin: Admin
) -> DeliveryLocationResponse:
    loc = service.create_delivery_location(db, body, created_by=admin.id)
    return DeliveryLocationResponse.model_validate(loc)


@router.put("/delivery-locations/{loc_id}", response_model=DeliveryLocationResponse)
def update_delivery_location(
    loc_id: int,
    body: DeliveryLocationUpdate,
    db: DbDep,
    _admin: Admin,
) -> DeliveryLocationResponse:
    try:
        loc = service.update_delivery_location(db, loc_id, body)
    except SettingsError as exc:
        raise _translate(exc) from exc
    return DeliveryLocationResponse.model_validate(loc)


@router.delete("/delivery-locations/{loc_id}")
def delete_delivery_location(loc_id: int, db: DbDep, _admin: Admin) -> dict[str, str]:
    try:
        service.delete_delivery_location(db, loc_id)
    except SettingsError as exc:
        raise _translate(exc) from exc
    return {"detail": "已删除"}


# ═════ Company Config ══════════════════════════════════════════


@router.get("/company-config", response_model=list[CompanyConfigResponse])
def get_company_config(db: DbDep, _admin: Admin) -> list[CompanyConfigResponse]:
    return [CompanyConfigResponse.model_validate(c) for c in service.list_company_config(db)]


@router.put("/company-config")
def update_company_config(body: CompanyConfigUpdate, db: DbDep, admin: Admin) -> dict[str, str]:
    service.update_company_config(db, body, updated_by=admin.id)
    return {"detail": "已更新"}


# Tools / Skills stubs live in `settings_phase6_stubs.py` so this file stays small.
