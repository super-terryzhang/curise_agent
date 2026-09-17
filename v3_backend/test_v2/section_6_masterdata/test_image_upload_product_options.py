from __future__ import annotations

from sqlalchemy import event

from domains.masterdata import service as masterdata_service
from domains.masterdata.models import Country, Port, Product, ProductImage


def _seed_products(db):
    japan = Country(name="日本", code="JP")
    china = Country(name="中国", code="CN")
    db.add_all([japan, china])
    db.flush()

    osaka = Port(name="大阪", code="OSA", country_id=japan.id)
    shanghai = Port(name="上海", code="SHA", country_id=china.id)
    db.add_all([osaka, shanghai])
    db.flush()

    tea = Product(
        product_name_en="TEA ICED VITALITY 32 OZ",
        product_name_jp="アイスティー",
        code="99PRD80671",
        country_id=japan.id,
        port_id=osaka.id,
        status=True,
    )
    juice = Product(
        product_name_en="JUICE GRAPEFRUIT LTR",
        code="99PRD23645",
        country_id=japan.id,
        port_id=osaka.id,
        status=True,
    )
    sauce = Product(
        product_name_en="SAUCE KETCHUP",
        code="99PRD011786",
        country_id=china.id,
        port_id=shanghai.id,
        status=True,
    )
    db.add_all([tea, juice, sauce])
    db.flush()
    db.add(
        ProductImage(
            product_id=tea.id,
            storage_key="products/tea/full.jpg",
            thumbnail_key="products/tea/thumb.jpg",
            medium_key="products/tea/medium.jpg",
            filename="tea.jpg",
            file_type="image/jpeg",
            file_size_bytes=128,
            display_order=0,
            uploaded_by_user_id=1,
        )
    )
    db.commit()
    return japan, osaka, tea, juice, sauce


def test_image_upload_options_searches_code_and_name_with_location_filters(db):
    japan, osaka, tea, _juice, _sauce = _seed_products(db)

    result = masterdata_service.list_image_upload_products(
        db,
        search="iced vitality",
        country_id=japan.id,
        port_id=osaka.id,
        only_without_images=False,
        limit=30,
        offset=0,
    )

    assert result["total"] == 1
    item = result["items"][0]
    assert set(item) == {
        "id",
        "code",
        "product_name_en",
        "product_name_jp",
        "country_id",
        "country_name",
        "port_id",
        "port_name",
        "thumbnail_url",
        "image_count",
    }
    assert item | {"thumbnail_url": None} == {
        "id": tea.id,
        "code": "99PRD80671",
        "product_name_en": "TEA ICED VITALITY 32 OZ",
        "product_name_jp": "アイスティー",
        "country_id": japan.id,
        "country_name": "日本",
        "port_id": osaka.id,
        "port_name": "大阪",
        "thumbnail_url": None,
        "image_count": 1,
    }
    assert "/uploads/products/tea/thumb.jpg?" in item["thumbnail_url"]

    japanese_result = masterdata_service.list_image_upload_products(
        db,
        search="アイスティー",
        country_id=japan.id,
        port_id=osaka.id,
        limit=30,
        offset=0,
    )
    assert japanese_result["total"] == 1
    assert [row["id"] for row in japanese_result["items"]] == [tea.id]


def test_image_upload_options_can_return_only_products_without_images_in_two_queries(
    db, engine
):
    _japan, _osaka, _tea, juice, _sauce = _seed_products(db)
    statements: list[str] = []

    def record_statement(_conn, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", record_statement)
    try:
        result = masterdata_service.list_image_upload_products(
            db,
            search="grapefruit",
            only_without_images=True,
            limit=30,
            offset=0,
        )
    finally:
        event.remove(engine, "before_cursor_execute", record_statement)

    assert result["total"] == 1
    assert [item["id"] for item in result["items"]] == [juice.id]
    assert result["items"][0]["image_count"] == 0
    assert result["items"][0]["thumbnail_url"] is None
    assert len(statements) == 2, statements


def test_image_upload_options_http_contract(client, db, auth_tokens):
    japan, osaka, _tea, juice, _sauce = _seed_products(db)
    headers = {"Authorization": f"Bearer {auth_tokens['access_token']}"}

    response = client.get(
        "/api/data/products/image-upload-options",
        params={
            "search": "99PRD23645",
            "country_id": japan.id,
            "port_id": osaka.id,
            "only_without_images": "true",
            "limit": 30,
        },
        headers=headers,
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 1
    assert [item["id"] for item in body["items"]] == [juice.id]
