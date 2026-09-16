"""The migration entrypoint must never dispatch business writes."""

import pytest
from fastapi.testclient import TestClient

from apps.http.maintenance import app


@pytest.mark.parametrize("method,path", [
    ("POST", "/api/auth/login"), ("PATCH", "/api/data/products/1"),
    ("POST", "/api/data/uploads/1/commit"), ("POST", "/webhook/line"),
    ("DELETE", "/api/data/products/1"), ("GET", "/api/data/products"),
])
def test_maintenance_blocks_business_routes(method, path):
    with TestClient(app) as client:
        response = client.request(method, path)
    assert response.status_code == 503
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["retry-after"] == "120"


def test_maintenance_health_is_explicit_and_head_has_no_body():
    with TestClient(app) as client:
        assert client.get("/health").json() == {"status": "maintenance"}
        assert client.head("/health").content == b""
        assert client.post("/health").status_code == 503
