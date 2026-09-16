"""Section 9 — Web API: `/api/data/*` master-data HTTP contract.

测试目标：
    masterdata 路由是 v2 frontend 的核心数据源。这里验证 6 个实体的 CRUD HTTP
    契约：list 返回的形状（K1/K2/K3 之后 list_products 必须是 {total, items}），
    POST/PATCH/DELETE 的状态码语义（201/200/204/409/404），以及 RBAC——
    employee 角色不能写入。

为什么重要：
    list_products 的形状是 K1/K2/K3 事件的回归点；frontend 直接断言它是
    `{total, items}`。FK 删除冲突必须是 409（不是 500），否则前端无法分辨。
    employee 能写就违背了所有的 admin-only 写入语义。

设计方法：
    用 conftest 的 `client` + `seed_user` (superadmin) + `auth_tokens`
    (employee)。每个测试都先 login，拿到 token，发请求。汇率 fetch
    用 monkeypatch 把 `httpx.Client` 替换成 fake。
"""

from __future__ import annotations

from typing import Any

from test_v2.fixtures.helpers import login


# ─── Helpers ─────────────────────────────────────────────────


def _admin_headers(client, seed_user) -> dict[str, str]:
    """Log in as the superadmin seeded by conftest's `seed_user`."""
    return login(client, "admin@example.com", "password123")


def _create_country(client, headers, *, name: str = "Japan", code: str = "JP") -> dict[str, Any]:
    r = client.post(
        "/api/data/countries",
        json={"name": name, "code": code, "status": True},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    return r.json()


# ─── GET each entity ─────────────────────────────────────────


def test_list_countries_returns_list(client, seed_user):
    """GET /api/data/countries returns a JSON list (even when empty)."""
    headers = _admin_headers(client, seed_user)
    r = client.get("/api/data/countries", headers=headers)
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_list_categories_returns_list(client, seed_user):
    headers = _admin_headers(client, seed_user)
    r = client.get("/api/data/categories", headers=headers)
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_list_ports_returns_list(client, seed_user):
    headers = _admin_headers(client, seed_user)
    r = client.get("/api/data/ports", headers=headers)
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_list_suppliers_returns_list(client, seed_user):
    headers = _admin_headers(client, seed_user)
    r = client.get("/api/data/suppliers", headers=headers)
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_list_exchange_rates_returns_list(client, seed_user):
    headers = _admin_headers(client, seed_user)
    r = client.get("/api/data/exchange-rates", headers=headers)
    assert r.status_code == 200
    assert isinstance(r.json(), list)


# ─── list_products shape (K1/K2/K3 regression) ───────────────


def test_list_products_returns_total_items_shape(client, seed_user):
    """list_products MUST return {total, items} — frontend asserts this.

    K1/K2/K3 incident regression: agent tools and frontend both assume
    {total: int, items: [...]}. Returning a plain list breaks both.
    """
    headers = _admin_headers(client, seed_user)
    r = client.get("/api/data/products", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, dict)
    assert "total" in body and "items" in body
    assert isinstance(body["total"], int)
    assert isinstance(body["items"], list)


# ─── POST create + conflict ──────────────────────────────────


def test_create_country_returns_201_with_id(client, seed_user):
    headers = _admin_headers(client, seed_user)
    obj = _create_country(client, headers, name="Australia", code="AU")
    assert obj["id"] > 0
    assert obj["name"] == "Australia"
    assert obj["code"] == "AU"


def test_create_country_duplicate_code_returns_409(client, seed_user):
    """Reusing an existing code is a Conflict, not a 500."""
    headers = _admin_headers(client, seed_user)
    _create_country(client, headers, name="USA", code="US")
    r = client.post(
        "/api/data/countries",
        json={"name": "United States", "code": "US"},
        headers=headers,
    )
    assert r.status_code == 409, r.text


# ─── DELETE with FK reference → 409 ──────────────────────────


def test_delete_country_with_referencing_port_returns_409(client, seed_user):
    """A country referenced by a Port cannot be deleted — 409 Conflict."""
    headers = _admin_headers(client, seed_user)
    country = _create_country(client, headers, name="UK", code="UK")
    # Create a port that depends on this country.
    rp = client.post(
        "/api/data/ports",
        json={"name": "London", "code": "LON", "country_id": country["id"]},
        headers=headers,
    )
    assert rp.status_code == 201, rp.text

    r = client.delete(f"/api/data/countries/{country['id']}", headers=headers)
    assert r.status_code == 409, r.text


# ─── PATCH + DELETE products ─────────────────────────────────


def test_patch_product_updates_fields(client, seed_user, db):
    """PATCH a product → fields updated and reflected on GET."""
    from test_v2.fixtures.helpers import seed_product

    headers = _admin_headers(client, seed_user)
    product = seed_product(db, code="X-1", name="Widget", price=12.0)

    r = client.patch(
        f"/api/data/products/{product.id}",
        json={"price": 19.5, "unit": "PCS", "expected_revision": product.revision},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["price"] == 19.5
    assert body["unit"] == "PCS"


def test_delete_product_removes_row(client, seed_user, db):
    """DELETE → 204, and the row is gone (verified via a fresh session)."""
    from sqlalchemy.orm import Session as _Session

    from domains.masterdata.models import Product

    from test_v2.fixtures.helpers import seed_product

    headers = _admin_headers(client, seed_user)
    product = seed_product(db, code="Y-1", name="Gadget", price=2.5)
    pid = product.id

    r = client.delete(f"/api/data/products/{pid}", params={"expected_revision": product.revision}, headers=headers)
    assert r.status_code == 204

    # Verify deletion through a fresh session (avoids identity-map cache).
    with _Session(bind=db.bind) as s2:
        assert s2.get(Product, pid) is None


# ─── Exchange rates create ────────────────────────────────────


def test_create_exchange_rate_returns_201(client, seed_user):
    headers = _admin_headers(client, seed_user)
    r = client.post(
        "/api/data/exchange-rates",
        json={
            "from_currency": "USD",
            "to_currency": "JPY",
            "rate": 150.5,
            "effective_date": "2026-01-01",
        },
        headers=headers,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["from_currency"] == "USD"
    assert body["to_currency"] == "JPY"
    assert float(body["rate"]) == 150.5


# ─── RBAC ────────────────────────────────────────────────────


def test_employee_cannot_create_country(client, auth_tokens):
    """employee role gets 403 on write endpoints (`Admin` guard)."""
    headers = {"Authorization": f"Bearer {auth_tokens['access_token']}"}
    r = client.post(
        "/api/data/countries",
        json={"name": "Forbidden", "code": "FB"},
        headers=headers,
    )
    assert r.status_code == 403, r.text


def test_admin_can_create_country(client, seed_user):
    """Sanity: admin (superadmin) can in fact write where employee can't."""
    headers = _admin_headers(client, seed_user)
    r = client.post(
        "/api/data/countries",
        json={"name": "Singapore", "code": "SG"},
        headers=headers,
    )
    assert r.status_code == 201, r.text


def test_employee_can_read_products(client, auth_tokens):
    """Read endpoints use the `Writer` guard, which DOES include employee."""
    headers = {"Authorization": f"Bearer {auth_tokens['access_token']}"}
    r = client.get("/api/data/products", headers=headers)
    assert r.status_code == 200, r.text


# ─── fetch exchange rates (external API) ─────────────────────


class _FakeHttpxClient:
    """Drop-in replacement for httpx.Client used in _exchange_rates_service.

    Returns a canned successful response so the route can be exercised
    without hitting open.er-api.com.
    """

    def __init__(self, *args, **kwargs):  # noqa: ARG002
        pass

    def get(self, _url: str) -> Any:
        return _FakeResp()

    def close(self) -> None:
        pass


class _FakeResp:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return {
            "result": "success",
            "rates": {"JPY": 150.0, "EUR": 0.92, "USD": 1.0},
        }


def test_fetch_exchange_rates_uses_external_api(client, seed_user, monkeypatch):
    """POST /api/data/exchange-rates/fetch → upserts rows from external API.

    We monkeypatch httpx.Client inside the exchange-rates service so the
    request never leaves the test process.
    """
    headers = _admin_headers(client, seed_user)

    monkeypatch.setattr(
        "domains.masterdata._exchange_rates_service.httpx.Client",
        _FakeHttpxClient,
    )

    r = client.post(
        "/api/data/exchange-rates/fetch",
        json={"base_currency": "USD", "target_currencies": ["JPY", "EUR"]},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["base"] == "USD"
    # Two new rows for JPY+EUR; USD is filtered out (== base).
    assert body["created"] + body["updated"] >= 2
