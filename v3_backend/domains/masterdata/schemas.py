"""Pydantic DTOs for master data domain.

Shape matches v2 bit-for-bit — these are what the HTTP layer serializes
and what the v2 frontend expects.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

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
