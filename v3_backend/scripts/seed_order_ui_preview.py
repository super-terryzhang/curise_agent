"""Create a disposable local database for order-management UI review.

Run with an explicit DATABASE_URL so the normal development/production
database is never modified, for example:

    DATABASE_URL=sqlite:///./order-ui-preview.db \
      .venv/bin/python scripts/seed_order_ui_preview.py
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

# Importing the app registers every domain model on the shared metadata.
import main  # noqa: E402,F401
from domains.identity.models import User  # noqa: E402
from domains.inquiry.models import SupplierTemplate  # noqa: E402
from domains.inquiry.standard_template import (  # noqa: E402
    STANDARD_TEMPLATE_ASSET,
    STANDARD_TEMPLATE_NAME,
    standard_template_definition,
)
from domains.masterdata.models import Country, Port, Product, Supplier  # noqa: E402
from domains.orders.models import Order, OrderGroup  # noqa: E402
from infrastructure.config import settings  # noqa: E402
from infrastructure.db.base import Base  # noqa: E402
from infrastructure.db.engine import engine  # noqa: E402
from infrastructure.db.session import SessionLocal  # noqa: E402
from infrastructure.security import hash_password  # noqa: E402


def _orders_for(
    *,
    user_id: int,
    group_id: int,
    port_id: int,
    ship: str,
    day: str,
    po_numbers: list[str],
    anomaly_index: int | None = None,
) -> list[Order]:
    orders: list[Order] = []
    for index, po_number in enumerate(po_numbers):
        has_anomaly = index == anomaly_index
        product_count = 8 + index * 3
        orders.append(
            Order(
                user_id=user_id,
                group_id=group_id,
                filename=f"{po_number}.pdf",
                file_type="pdf",
                status="ready",
                po_number=po_number,
                ship_name=ship,
                loading_date=day,
                delivery_date=day,
                port_id=port_id,
                destination_port=None,
                product_count=product_count,
                fulfillment_status="pending",
                order_metadata={
                    "source": "local-ui-preview",
                    "delivery_time_notes": "10:00–12:00（本地验收数据）",
                    "delivery_contact": "港口验收担当 090-0000-0000",
                },
                anomaly_data={
                    "requires_human_review": has_anomaly,
                    "error_count": 1 if has_anomaly else 0,
                    "blocking_count": 0,
                    "total_anomalies": 1 if has_anomaly else 0,
                },
                products=[
                    {
                        "line_number": line_number,
                        "product_name": f"示例商品 {line_number}",
                        "quantity": line_number * 2,
                        "unit": "CTN",
                    }
                    for line_number in range(1, product_count + 1)
                ],
                created_at=datetime.utcnow(),
                processed_at=datetime.utcnow(),
            )
        )
    return orders


def _ensure_matching_preview(db, admin_id: int) -> None:
    country = db.query(Country).filter(Country.code == "JPN").first()
    if country is None:
        country = Country(name="日本", code="JPN", status=True)
        db.add(country)
        db.flush()

    preview_ports = (
        db.query(Port)
        .filter(Port.name.in_(("横滨港", "神户港", "东京港", "长崎港")))
        .all()
    )
    port_locations = {
        "横滨港": "〒231-0002 神奈川県横浜市中区海岸通1-1 横浜港",
        "神户港": "〒650-0042 兵庫県神戸市中央区波止場町 神戸港",
        "东京港": "〒135-0064 東京都江東区青海 東京港",
        "长崎港": "〒850-0035 長崎県長崎市元船町 長崎港",
    }
    for port in preview_ports:
        port.country_id = country.id
        port.status = True
        port.location = port_locations[port.name]

    suppliers: list[Supplier] = []
    supplier_profiles = (
        {
            "name": "Yokohama Marine Foods",
            "contact": "田中 太郎",
            "phone": "045-000-1001",
            "fax": "045-000-1002",
            "email": "sales@yokohama-marine.example",
            "address": "神奈川県横浜市中区山下町100-1",
            "zip_code": "231-0023",
            "default_payment_method": "銀行振込",
            "default_payment_terms": "月末締め翌月末払い",
        },
        {
            "name": "Kobe Fresh Supply",
            "contact": "山田 花子",
            "phone": "078-000-2001",
            "fax": "078-000-2002",
            "email": "orders@kobe-fresh.example",
            "address": "兵庫県神戸市中央区港島1-2-3",
            "zip_code": "650-0045",
            "default_payment_method": "銀行振込",
            "default_payment_terms": "納品月末締め翌月30日払い",
        },
        {
            "name": "Pacific General Trading",
            "contact": "佐藤 次郎",
            "phone": "03-0000-3001",
            "fax": "03-0000-3002",
            "email": "rfq@pacific-general.example",
            "address": "東京都中央区築地4-5-6",
            "zip_code": "104-0045",
            "default_payment_method": "電信送金（T/T）",
            "default_payment_terms": "請求書受領後30日",
        },
    )
    for profile in supplier_profiles:
        name = profile["name"]
        supplier = db.query(Supplier).filter(Supplier.name == name).first()
        if supplier is None:
            supplier = Supplier(country_id=country.id, status=True, **profile)
            db.add(supplier)
            db.flush()
        else:
            supplier.country_id = country.id
            supplier.status = True
            for key, value in profile.items():
                setattr(supplier, key, value)
        suppliers.append(supplier)

    definition = standard_template_definition()
    template_file = definition["template_file_url"]
    local_template = BACKEND_ROOT / "uploads" / template_file
    if not local_template.is_file():
        if not STANDARD_TEMPLATE_ASSET.is_file():
            raise RuntimeError(
                f"Canonical inquiry template is missing: {STANDARD_TEMPLATE_ASSET}"
            )
        local_template.parent.mkdir(parents=True, exist_ok=True)
        local_template.write_bytes(STANDARD_TEMPLATE_ASSET.read_bytes())
    template = (
        db.query(SupplierTemplate)
        .filter(
            SupplierTemplate.template_name.in_(
                (STANDARD_TEMPLATE_NAME, "本地验收标准询价模板")
            )
        )
        .first()
    )
    if template is None:
        template = SupplierTemplate(
            template_name=definition["template_name"],
            supplier_ids=[supplier.id for supplier in suppliers],
            country_id=country.id,
            template_file_url=template_file,
            field_positions=definition["field_positions"],
            has_product_table=True,
            product_table_config=definition["product_table_config"],
            template_styles=definition["template_styles"],
            created_by=admin_id,
        )
        db.add(template)
    else:
        template.template_name = definition["template_name"]
        template.supplier_ids = [supplier.id for supplier in suppliers]
        template.country_id = country.id
        template.template_file_url = template_file
        template.field_positions = definition["field_positions"]
        template.has_product_table = True
        template.product_table_config = definition["product_table_config"]
        template.template_styles = definition["template_styles"]

    preview_orders = (
        db.query(Order)
        .filter(Order.user_id == admin_id, Order.group_id.is_not(None))
        .order_by(Order.id.asc())
        .all()
    )
    for order_index, preview_order in enumerate(preview_orders):
        metadata = dict(preview_order.order_metadata or {})
        metadata.setdefault(
            "delivery_time_notes", "10:00–12:00（本地验收数据）"
        )
        metadata.setdefault("delivery_contact", "港口验收担当 090-0000-0000")
        preview_order.order_metadata = metadata
        total = preview_order.product_count or len(preview_order.products or [])
        has_unmatched = preview_order.po_number == "PO-KOB-260924-02"
        results = []
        source_products = []
        for row_index in range(total):
            raw_product = (
                (preview_order.products or [])[row_index]
                if row_index < len(preview_order.products or [])
                else {"product_name": f"示例商品 {row_index + 1}"}
            )
            product_code = f"DEMO-{preview_order.id:03d}-{row_index + 1:03d}"
            product = {
                **raw_product,
                "line_id": raw_product.get("line_id") or f"line-{row_index + 1:05d}",
                "source_line": raw_product.get("source_line") or str(row_index + 1),
                "page": raw_product.get("page") or 1,
                "product_code": product_code,
            }
            source_products.append(product)
            if has_unmatched and row_index == 3:
                results.append({
                    **product,
                    "match_status": "not_matched",
                    "match_score": 0,
                    "match_reason": "未找到可信的数据库商品匹配",
                })
                continue
            supplier = suppliers[(order_index + row_index) % len(suppliers)]
            master_product = (
                db.query(Product)
                .filter(
                    Product.code == product_code,
                    Product.country_id == country.id,
                    Product.port_id == preview_order.port_id,
                )
                .first()
            )
            if master_product is None:
                master_product = Product(
                    code=product_code,
                    product_name_en=product.get("product_name") or product_code,
                    country_id=country.id,
                    port_id=preview_order.port_id,
                    supplier_id=supplier.id,
                    unit=product.get("unit") or "CTN",
                    price=1000 + row_index * 50,
                    contract_price=1200 + row_index * 50,
                    currency="JPY",
                    status=True,
                )
                db.add(master_product)
                db.flush()
            else:
                master_product.supplier_id = supplier.id
                master_product.status = True
            results.append({
                **product,
                "match_status": "matched",
                "match_score": 1,
                "match_reason": "产品代码与供货范围匹配",
                "matched_product": {
                    "id": master_product.id,
                    "code": master_product.code,
                    "product_name_en": product.get("product_name"),
                    "product_name_jp": None,
                    "price": 1000 + row_index * 50,
                    "contract_price": 1200 + row_index * 50,
                    "currency": "JPY",
                    "supplier_id": supplier.id,
                    "category_id": None,
                    "pack_size": None,
                    "unit": product.get("unit") or "CTN",
                },
            })
        preview_order.products = source_products
        preview_order.country_id = country.id
        preview_order.match_results = results
        preview_order.match_statistics = {
            "total": total,
            "matched": total - (1 if has_unmatched else 0),
            "not_matched": 1 if has_unmatched else 0,
            "match_rate": round((total - (1 if has_unmatched else 0)) / total * 100, 2) if total else 0,
        }


def main_seed() -> None:
    if not settings.DATABASE_URL.startswith("sqlite:///./order-ui-preview.db"):
        raise RuntimeError(
            "Refusing to seed: DATABASE_URL must target ./order-ui-preview.db"
        )

    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        existing = db.query(User).filter(User.email == "admin@example.com").first()
        if existing is not None:
            _ensure_matching_preview(db, existing.id)
            db.commit()
            print("Updated local order UI preview data.")
            return

        admin = User(
            email="admin@example.com",
            hashed_password=hash_password("adminpassword"),
            full_name="本地验收管理员",
            role="superadmin",
            is_active=True,
            is_superuser=True,
            is_default_password=False,
            password_changed_at=datetime.utcnow(),
        )
        db.add(admin)
        db.flush()

        country = Country(name="日本", code="JPN", status=True)
        db.add(country)
        db.flush()

        ports = [
            Port(name="横滨港", code="JPYOK", country_id=country.id),
            Port(name="神户港", code="JPUKB", country_id=country.id),
            Port(name="东京港", code="JPTYO", country_id=country.id),
            Port(name="长崎港", code="JPNGS", country_id=country.id),
        ]
        db.add_all(ports)
        db.flush()

        samples = [
            ("DIAMOND PRINCESS 供船安排", "DIAMOND PRINCESS", "2026-09-20", 0,
             ["PO-YOK-260920-01", "PO-YOK-260920-02"], None),
            ("CELEBRITY MILLENNIUM 供船安排", "CELEBRITY MILLENNIUM", "2026-09-24", 1,
             ["PO-KOB-260924-01", "PO-KOB-260924-02", "PO-KOB-260924-03"], 1),
            ("MSC BELLISSIMA 供船安排", "MSC BELLISSIMA", "2026-09-27", 2,
             ["PO-TYO-260927-01"], None),
            ("QUEEN ELIZABETH 供船安排", "QUEEN ELIZABETH", "2026-10-02", 3,
             ["PO-NGS-261002-01", "PO-NGS-261002-02", "PO-NGS-261002-03", "PO-NGS-261002-04"], None),
        ]
        for name, ship, day, port_index, po_numbers, anomaly_index in samples:
            group = OrderGroup(
                user_id=admin.id,
                name=name,
                ship_name=ship,
                loading_date=day,
            )
            db.add(group)
            db.flush()
            db.add_all(
                _orders_for(
                    user_id=admin.id,
                    group_id=group.id,
                    port_id=ports[port_index].id,
                    ship=ship,
                    day=day,
                    po_numbers=po_numbers,
                    anomaly_index=anomaly_index,
                )
            )

        db.add(
            Order(
                user_id=admin.id,
                filename="PO-UNCLASSIFIED-01.pdf",
                file_type="pdf",
                status="ready",
                po_number="PO-UNCLASSIFIED-01",
                ship_name="SAPPHIRE PRINCESS",
                loading_date=None,
                port_id=ports[0].id,
                product_count=5,
                fulfillment_status="pending",
                order_metadata={"source": "local-ui-preview"},
                anomaly_data={
                    "requires_human_review": True,
                    "error_count": 1,
                    "blocking_count": 1,
                    "total_anomalies": 2,
                },
            )
        )
        _ensure_matching_preview(db, admin.id)
        db.commit()
        print("Created local order UI preview data.")
    finally:
        db.close()


if __name__ == "__main__":
    main_seed()
