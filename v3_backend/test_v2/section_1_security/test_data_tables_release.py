"""Only the explicit disabled-module bridge can accept the preceding schema."""

from unittest.mock import MagicMock, Mock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from apps.http import startup
from infrastructure.config import Settings, settings


@pytest.mark.parametrize(
    "expected,current,transition,enabled,passes",
    [
        ("0034_drop_product_validity", "0034_drop_product_validity", "", False, True),
        ("0035_custom_data_tables", "0035_custom_data_tables", "", True, True),
        ("0035_custom_data_tables", "0034_drop_product_validity", "", False, False),
        (
            "0035_custom_data_tables",
            "0034_drop_product_validity",
            "custom_data_tables_0034_0035",
            False,
            True,
        ),
        (
            "0035_custom_data_tables",
            "0035_custom_data_tables",
            "custom_data_tables_0034_0035",
            False,
            True,
        ),
        ("0035_custom_data_tables", "9999_future", "custom_data_tables_0034_0035", False, False),
        (
            "0035_custom_data_tables",
            "0034_drop_product_validity",
            "custom_data_tables_0034_0035",
            True,
            False,
        ),
        (
            "0034_drop_product_validity",
            "0034_drop_product_validity",
            "custom_data_tables_0034_0035",
            False,
            False,
        ),
    ],
)
def test_startup_matrix(monkeypatch, expected, current, transition, enabled, passes):
    monkeypatch.setattr(
        startup,
        "settings",
        Mock(CUSTOM_DATA_TABLES_ENABLED=enabled, SCHEMA_RELEASE_TRANSITION=transition),
    )
    monkeypatch.setattr(
        startup.ScriptDirectory, "from_config", lambda _: Mock(get_heads=lambda: [expected])
    )
    monkeypatch.setattr(
        startup.MigrationContext, "configure", lambda _: Mock(get_current_heads=lambda: [current])
    )
    engine = MagicMock()
    if passes:
        startup.verify_schema(engine)
    else:
        with pytest.raises(RuntimeError):
            startup.verify_schema(engine)


def test_settings_reject_enabled_bridge_and_unknown_transition():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, SCHEMA_RELEASE_TRANSITION="invalid")
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            CUSTOM_DATA_TABLES_ENABLED=True,
            SCHEMA_RELEASE_TRANSITION="custom_data_tables_0034_0035",
        )


def test_disabled_routes_fail_closed_while_products_continue(client, seed_user, monkeypatch):
    monkeypatch.setitem(settings.__dict__, "CUSTOM_DATA_TABLES_ENABLED", False)
    login = client.post(
        "/api/auth/login", json={"email": seed_user.email, "password": "password123"}
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    for method, path, body in [
        ("GET", "", None),
        ("POST", "", {"id": str(uuid4()), "name": "关闭"}),
        ("GET", f"/{uuid4()}/records", None),
    ]:
        response = client.request(method, "/api/data-tables" + path, headers=headers, json=body)
        assert response.status_code == 503
        assert response.json()["detail"]["code"] == "MODULE_DISABLED"
    assert client.get("/api/data/products", headers=headers).status_code == 200
