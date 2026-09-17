"""Master data ORM models.

Maps to existing Supabase tables shared with v2:
- `countries`, `ports`, `categories`, `suppliers`, `supplier_categories`,
  `products`, `v2_exchange_rates`.

All columns match v2's `core/models.py` bit-for-bit — v2 and v3 must read
and write identical rows.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from infrastructure.db.base import Base


class Country(Base):
    __tablename__ = "countries"
    __table_args__ = {"extend_existing": True}

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    code: Mapped[str | None] = mapped_column(String(3), nullable=True)
    status: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class Port(Base):
    __tablename__ = "ports"
    __table_args__ = {"extend_existing": True}

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    country_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("countries.id"), nullable=True
    )
    location: Mapped[str | None] = mapped_column(String(200), nullable=True)
    status: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class Category(Base):
    __tablename__ = "categories"
    __table_args__ = {"extend_existing": True}

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class Supplier(Base):
    __tablename__ = "suppliers"
    __table_args__ = {"extend_existing": True}

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    country_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("countries.id"), nullable=True
    )
    contact: Mapped[str | None] = mapped_column(String(100), nullable=True)
    email: Mapped[str | None] = mapped_column(String(100), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(20), nullable=True)
    address: Mapped[str | None] = mapped_column(Text, nullable=True)
    zip_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    fax: Mapped[str | None] = mapped_column(String(50), nullable=True)
    default_payment_method: Mapped[str | None] = mapped_column(String(100), nullable=True)
    default_payment_terms: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class SupplierCategory(Base):
    __tablename__ = "supplier_categories"
    __table_args__ = (
        UniqueConstraint("supplier_id", "category_id"),
        {"extend_existing": True},
    )

    supplier_id: Mapped[int] = mapped_column(Integer, ForeignKey("suppliers.id"), primary_key=True)
    category_id: Mapped[int] = mapped_column(Integer, ForeignKey("categories.id"), primary_key=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, default=datetime.utcnow)


class Product(Base):
    __tablename__ = "products"
    __table_args__ = (
        CheckConstraint(
            "purchase_price_effective_from IS NULL OR "
            "purchase_price_effective_to IS NULL OR "
            "purchase_price_effective_from <= purchase_price_effective_to",
            name="ck_products_purchase_price_period",
        ),
        CheckConstraint(
            "selling_price_effective_from IS NULL OR "
            "selling_price_effective_to IS NULL OR "
            "selling_price_effective_from <= selling_price_effective_to",
            name="ck_products_selling_price_period",
        ),
        {"extend_existing": True, "sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_name_en: Mapped[str] = mapped_column(String(100), nullable=False)
    product_name_jp: Mapped[str | None] = mapped_column(String(100), nullable=True)
    code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    country_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("countries.id"), nullable=True
    )
    category_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("categories.id"), nullable=True
    )
    supplier_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("suppliers.id"), nullable=True
    )
    port_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("ports.id"), nullable=True)
    unit: Mapped[str | None] = mapped_column(String(20), nullable=True)
    price: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    # Contract selling price — our side of record for cruise PO comparison.
    # Distinct from `price` (default/list, may drift). When set, the matcher
    # includes it in match_results so finance can see PO `unit_price` vs
    # `contract_price` deltas (Felix 2026-06-06, migration 0014).
    contract_price: Mapped[Decimal | None] = mapped_column(
        Numeric(10, 2), nullable=True
    )
    # Price periods are intentionally separate from Product.effective_*.
    # Product.effective_* controls whether the product itself can match;
    # these four dates describe the validity of each commercial price.
    purchase_price_effective_from: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True
    )
    purchase_price_effective_to: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True
    )
    selling_price_effective_from: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True
    )
    selling_price_effective_to: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True
    )
    unit_size: Mapped[str | None] = mapped_column(String(50), nullable=True)
    pack_size: Mapped[str | None] = mapped_column(String(50), nullable=True)
    country_of_origin: Mapped[str | None] = mapped_column(String(50), nullable=True)
    brand: Mapped[str | None] = mapped_column(String(100), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(20), nullable=True)
    effective_from: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    effective_to: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[bool] = mapped_column(Boolean, default=True)

    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    price_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    __mapper_args__ = {"version_id_col": revision}


class ProductPriceHistory(Base):
    """Independent append-only events; survives product, user and upload deletion."""

    __tablename__ = "v3_product_price_history"
    __table_args__ = (
        UniqueConstraint("product_id", "version", name="uq_product_price_version"),
        UniqueConstraint("source_key", name="uq_product_price_source"),
        CheckConstraint("version IS NULL OR version >= 0", name="ck_price_history_version"),
        CheckConstraint("event_type IN ('initial','baseline','change','restore','delete','legacy')", name="ck_price_history_event"),
        Index("ix_price_history_product_time", "product_id", "recorded_at", "id"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    # Intentionally not a cascading FK: identity remains after product deletion.
    product_id: Mapped[int] = mapped_column(Integer, nullable=False)
    version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    event_type: Mapped[str] = mapped_column(String(16), nullable=False)
    before_price: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    after_price: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    before_contract_price: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    after_contract_price: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    before_context: Mapped[dict | None] = mapped_column(JSON)
    after_context: Mapped[dict | None] = mapped_column(JSON)
    changed_fields: Mapped[list] = mapped_column(JSON, nullable=False)
    actor_id: Mapped[int | None] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    source_batch_id: Mapped[int | None] = mapped_column(Integer)
    source_key: Mapped[str | None] = mapped_column(String(120))
    restores_id: Mapped[str | None] = mapped_column(String(32))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ProductPricePeriod(Base):
    """One non-overlapping purchase or selling price validity interval."""

    __tablename__ = "v3_product_price_periods"
    __table_args__ = (
        CheckConstraint(
            "price_type IN ('purchase', 'selling')",
            name="ck_product_price_period_type",
        ),
        CheckConstraint(
            "amount >= 0 AND amount <= 99999999.99",
            name="ck_product_price_period_amount",
        ),
        CheckConstraint(
            "effective_from <= effective_to",
            name="ck_product_price_period_dates",
        ),
        UniqueConstraint(
            "product_id",
            "price_type",
            "effective_from",
            "effective_to",
            name="uq_product_price_period_bounds",
        ),
        Index(
            "ix_product_price_period_lookup",
            "product_id",
            "price_type",
            "status",
            "effective_from",
            "effective_to",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    product_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("products.id", ondelete="CASCADE"), nullable=False
    )
    price_type: Mapped[str] = mapped_column(String(16), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    currency: Mapped[str | None] = mapped_column(String(20), nullable=True)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    source: Mapped[str] = mapped_column(String(20), default="internal", nullable=False)
    source_batch_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    updated_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class ProductImage(Base):
    """One image attached to a Product (R5 2026-06-22).

    Three storage keys per row — original (EXIF-normalized JPEG), 400px
    medium, 80px thumbnail. All generated at upload time by
    `domains.masterdata.images.thumbnails.generate_thumbnails`. The keys
    are opaque to anything outside `infrastructure.storage`, which means
    we can switch GCS ↔ Supabase ↔ Local without touching this table.

    `display_order = 0` means primary image (no separate is_primary
    boolean — one source of truth). Listing within a product is always
    `ORDER BY display_order ASC`.
    """

    __tablename__ = "v3_product_images"
    __table_args__ = (
        Index("ix_product_images_pid_order", "product_id", "display_order"),
        {"extend_existing": True},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    product_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("products.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    storage_key: Mapped[str] = mapped_column(String(500), nullable=False)
    thumbnail_key: Mapped[str] = mapped_column(String(500), nullable=False)
    medium_key: Mapped[str] = mapped_column(String(500), nullable=False)

    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    file_type: Mapped[str] = mapped_column(String(30), nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)

    display_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    alt_text: Mapped[str | None] = mapped_column(String(500), nullable=True)

    uploaded_by_user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    # Durable idempotency key for direct bulk-image ingestion.  Normal
    # single-image uploads leave it NULL; PostgreSQL permits multiple NULLs
    # under a unique constraint while preventing two formal images for the
    # same staging operation.
    source_bulk_staging_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True, unique=True, index=True
    )
    uploaded_at: Mapped[datetime | None] = mapped_column(
        DateTime, default=datetime.utcnow
    )


class ExchangeRate(Base):
    __tablename__ = "v2_exchange_rates"
    __table_args__ = (
        UniqueConstraint(
            "from_currency",
            "to_currency",
            "effective_date",
            name="uq_exchange_rate_pair_date",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    from_currency: Mapped[str] = mapped_column(String(3), nullable=False, index=True)
    to_currency: Mapped[str] = mapped_column(String(3), nullable=False, index=True)
    rate: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    effective_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(50), default="manual")
    created_at: Mapped[datetime | None] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )
