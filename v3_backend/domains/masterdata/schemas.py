"""Pydantic DTOs for master data domain.

Shape matches v2 bit-for-bit — these are what the HTTP layer serializes
and what the v2 frontend expects.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from domains.masterdata._money import parse_product_price

# ─── Country ─────────────────────────────────────────────────


class CountryCreate(BaseModel):
    name: str
    code: str | None = None
    status: bool = True


class CountryUpdate(BaseModel):
    name: str | None = None
    code: str | None = None
    status: bool | None = None


class CountryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    code: str | None = None
    status: bool = True
    created_at: datetime | None = None
    updated_at: datetime | None = None


# ─── Category ────────────────────────────────────────────────


class CategoryCreate(BaseModel):
    name: str
    code: str | None = None
    description: str | None = None
    status: bool = True


class CategoryUpdate(BaseModel):
    name: str | None = None
    code: str | None = None
    description: str | None = None
    status: bool | None = None


class CategoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    code: str | None = None
    description: str | None = None
    status: bool = True
    created_at: datetime | None = None
    updated_at: datetime | None = None


# ─── Port ────────────────────────────────────────────────────


class PortCreate(BaseModel):
    name: str
    code: str | None = None
    country_id: int | None = None
    location: str | None = None
    status: bool = True


class PortUpdate(BaseModel):
    name: str | None = None
    code: str | None = None
    country_id: int | None = None
    location: str | None = None
    status: bool | None = None


class PortResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    code: str | None = None
    country_id: int | None = None
    country_name: str | None = None
    location: str | None = None
    status: bool = True
    created_at: datetime | None = None
    updated_at: datetime | None = None


# ─── Supplier ────────────────────────────────────────────────


class SupplierCreate(BaseModel):
    name: str
    country_id: int | None = None
    contact: str | None = None
    email: str | None = None
    phone: str | None = None
    # Inquiry letterhead fields — printed on the supplier-facing Excel sheet
    # via the supplier_address / supplier_fax / ... template keys (see
    # domains/inquiry/field_inspection.SUPPLIER_FIELD_MAP). All optional —
    # legacy suppliers seeded before 2026-05-28 leave these NULL.
    address: str | None = None
    zip_code: str | None = None
    fax: str | None = None
    default_payment_method: str | None = None
    default_payment_terms: str | None = None
    category_ids: list[int] = Field(default_factory=list)
    status: bool = True


class SupplierUpdate(BaseModel):
    name: str | None = None
    country_id: int | None = None
    contact: str | None = None
    email: str | None = None
    phone: str | None = None
    address: str | None = None
    zip_code: str | None = None
    fax: str | None = None
    default_payment_method: str | None = None
    default_payment_terms: str | None = None
    category_ids: list[int] | None = None
    status: bool | None = None


class SupplierResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    country_id: int | None = None
    country_name: str | None = None
    contact: str | None = None
    email: str | None = None
    phone: str | None = None
    address: str | None = None
    zip_code: str | None = None
    fax: str | None = None
    default_payment_method: str | None = None
    default_payment_terms: str | None = None
    categories: list[str] = Field(default_factory=list)
    category_ids: list[int] = Field(default_factory=list)
    status: bool = True
    created_at: datetime | None = None
    updated_at: datetime | None = None


# ─── Product ─────────────────────────────────────────────────


class ProductCreate(BaseModel):
    @field_validator("price", "contract_price", mode="before")
    @classmethod
    def validate_price(cls, value: Any) -> Any:
        return parse_product_price(value)

    product_name_en: str
    product_name_jp: str | None = None
    code: str | None = None
    country_id: int | None = None
    category_id: int | None = None
    supplier_id: int | None = None
    port_id: int | None = None
    unit: str | None = None
    price: float | None = Field(None, ge=0)
    # Contract selling price — see Product.contract_price (migration 0014).
    contract_price: float | None = Field(None, ge=0)
    purchase_price_effective_from: str | None = None
    purchase_price_effective_to: str | None = None
    selling_price_effective_from: str | None = None
    selling_price_effective_to: str | None = None
    unit_size: str | None = None
    pack_size: str | None = None
    country_of_origin: str | None = None
    brand: str | None = None
    currency: str | None = None
    effective_from: str | None = None
    effective_to: str | None = None
    status: bool = True


class ProductUpdate(BaseModel):
    expected_revision: int | None = Field(None, ge=1)

    @field_validator("price", "contract_price", mode="before")
    @classmethod
    def validate_price(cls, value: Any) -> Any:
        return parse_product_price(value)

    product_name_en: str | None = None
    product_name_jp: str | None = None
    code: str | None = None
    country_id: int | None = None
    category_id: int | None = None
    supplier_id: int | None = None
    port_id: int | None = None
    unit: str | None = None
    price: float | None = Field(None, ge=0)
    contract_price: float | None = Field(None, ge=0)
    purchase_price_effective_from: str | None = None
    purchase_price_effective_to: str | None = None
    selling_price_effective_from: str | None = None
    selling_price_effective_to: str | None = None
    unit_size: str | None = None
    pack_size: str | None = None
    country_of_origin: str | None = None
    brand: str | None = None
    currency: str | None = None
    effective_from: str | None = None
    effective_to: str | None = None
    status: bool | None = None


class ProductResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    product_name_en: str
    product_name_jp: str | None = None
    code: str | None = None
    country_id: int | None = None
    category_id: int | None = None
    supplier_id: int | None = None
    port_id: int | None = None
    country_name: str | None = None
    category_name: str | None = None
    supplier_name: str | None = None
    port_name: str | None = None
    unit: str | None = None
    price: float | None = None
    contract_price: float | None = None
    purchase_price_effective_from: str | None = None
    purchase_price_effective_to: str | None = None
    selling_price_effective_from: str | None = None
    selling_price_effective_to: str | None = None
    price_periods: list[dict[str, Any]] = Field(default_factory=list)
    unit_size: str | None = None
    pack_size: str | None = None
    country_of_origin: str | None = None
    brand: str | None = None
    currency: str | None = None
    effective_from: str | None = None
    effective_to: str | None = None
    status: bool = True
    # R5 (2026-06-22): primary image thumbnail signed URL + total count.
    # Populated by the list endpoint via a 1-query join (no N+1). Detail
    # endpoint exposes the full `images` list separately.
    thumbnail_url: str | None = None
    image_count: int = 0


class ProductPricePeriodCreate(BaseModel):
    @field_validator("amount", mode="before")
    @classmethod
    def validate_amount(cls, value: Any) -> Any:
        return parse_product_price(value)

    price_type: str
    amount: float = Field(ge=0)
    currency: str | None = None
    effective_from: date
    effective_to: date


class ProductPricePeriodUpdate(BaseModel):
    @field_validator("amount", mode="before")
    @classmethod
    def validate_amount(cls, value: Any) -> Any:
        return parse_product_price(value)

    amount: float | None = Field(None, ge=0)
    currency: str | None = None
    effective_from: date | None = None
    effective_to: date | None = None
    status: bool | None = None


class ProductPricePeriodResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    product_id: int
    price_type: str
    amount: float
    currency: str | None = None
    effective_from: date
    effective_to: date
    status: bool
    source: str
    source_batch_id: int | None = None
    created_by: int | None = None
    updated_by: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ProductListResponse(BaseModel):
    total: int
    items: list[ProductResponse]


# ─── Unit conversion rules ───────────────────────────────────


class UnitConversionRuleCreate(BaseModel):
    scope_type: Literal["source_unit", "product"]
    product_id: int | None = Field(None, ge=1)
    source_system: str = Field(min_length=1, max_length=30)
    source_unit: str = Field(min_length=1, max_length=50)
    target_unit: str = Field(min_length=1, max_length=50)
    source_quantity: Decimal = Field(gt=0)
    target_quantity: Decimal = Field(gt=0)
    target_step: Decimal | None = Field(None, gt=0)
    break_pack: bool | None = None
    pack_signature: str | None = Field(None, max_length=255)
    evidence: str = Field(min_length=1)
    valid_from: date | None = None
    valid_to: date | None = None

    @field_validator("source_system", "source_unit", "target_unit", "evidence")
    @classmethod
    def non_blank_text(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("不能为空")
        return stripped

    @model_validator(mode="after")
    def validate_scope_and_dates(self):
        if self.scope_type == "source_unit" and (
            self.product_id is not None or self.pack_signature is not None
        ):
            raise ValueError("来源单位规则不能指定产品或包装指纹")
        if self.scope_type == "product" and (
            self.product_id is None or not (self.pack_signature or "").strip()
        ):
            raise ValueError("商品规则必须指定产品和包装指纹")
        if self.valid_from and self.valid_to and self.valid_from > self.valid_to:
            raise ValueError("有效开始日期不能晚于结束日期")
        return self


class UnitConversionRuleVerify(BaseModel):
    expected_revision: int = Field(ge=1)
    evidence: str = Field(min_length=1)

    @field_validator("evidence")
    @classmethod
    def evidence_not_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("审核依据不能为空")
        return stripped


class UnitConversionRuleRetire(BaseModel):
    expected_revision: int = Field(ge=1)
    evidence: str | None = None

    @field_validator("evidence")
    @classmethod
    def evidence_not_blank(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            raise ValueError("停用依据不能为空")
        return stripped


class UnitConversionRuleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    scope_type: str
    product_id: int | None = None
    source_system: str
    source_unit: str
    target_unit: str
    source_quantity: Decimal
    target_quantity: Decimal
    target_step: Decimal | None = None
    break_pack: bool | None = None
    pack_signature: str | None = None
    status: str
    evidence: str
    valid_from: date | None = None
    valid_to: date | None = None
    verified_by: int | None = None
    verified_at: datetime | None = None
    created_by: int
    updated_by: int
    revision: int
    created_at: datetime
    updated_at: datetime


# ─── Product Images (R5 2026-06-22) ───────────────────────────


class ProductImageResponse(BaseModel):
    """Outbound DTO for a single product image.

    The three `*_url` fields are signed URLs valid for ~1 hour. Clients
    should refresh by re-fetching when the URL stops returning 200 — the
    storage_key (the persistent thing) is intentionally hidden.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    filename: str
    file_type: str
    file_size_bytes: int
    display_order: int
    alt_text: str | None = None
    uploaded_at: datetime | None = None
    # Three sizes — frontend picks based on context:
    #   thumbnail_url (80px) for list cells / gallery thumbnails
    #   medium_url    (400px) for gallery preview / inquiry email embed
    #   full_url      (original EXIF-normalized JPEG) for lightbox
    thumbnail_url: str
    medium_url: str
    full_url: str


class ProductImageUpdate(BaseModel):
    """PATCH body — only alt_text is user-mutable. display_order has its
    own bulk-reorder endpoint to avoid sequencing bugs when shuffling."""

    alt_text: str | None = None


class ProductImageReorderEntry(BaseModel):
    id: int
    display_order: int = Field(ge=0)


class ProductImageReorderBody(BaseModel):
    """Body for PUT /products/{id}/images/reorder.

    Must contain *every* image id belonging to the product — partial
    reorders are rejected at the service layer to keep display_order
    consistent."""

    items: list[ProductImageReorderEntry]


# ─── Exchange rate ───────────────────────────────────────────


class ExchangeRateCreate(BaseModel):
    from_currency: str = Field(..., max_length=3)
    to_currency: str = Field(..., max_length=3)
    rate: float = Field(..., gt=0)
    effective_date: date


class ExchangeRateUpdate(BaseModel):
    rate: float | None = Field(None, gt=0)
    effective_date: date | None = None


class ExchangeRateResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    from_currency: str
    to_currency: str
    rate: float
    effective_date: date
    source: str = "manual"
    created_at: datetime | None = None
    updated_at: datetime | None = None


class FetchRatesRequest(BaseModel):
    base_currency: str = "USD"
    target_currencies: list[str] = Field(default_factory=list)


class FetchRatesResult(BaseModel):
    created: int
    updated: int
    base: str
    date: str


# ─── Helpers ─────────────────────────────────────────────────


def datetime_to_date_str(value: Any) -> str | None:
    """Best-effort: convert datetime/date/str to YYYY-MM-DD string."""
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return str(value)
