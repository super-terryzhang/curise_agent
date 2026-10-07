"""Explicit isolated-service smoke: creates and deletes only its reserved test product.

Set VERIFY_PASSWORD in the environment. Never print passwords or access tokens.
Stops on the first unexpected result; an interrupted test may retain its new product.
"""

import json
import os
import urllib.error
import urllib.request

BASE = "https://cruise-v3-database-setup-20261007-871185913501.asia-northeast1.run.app"
CODE = "MANUALSMOKE20261007"


def call(method, path, body=None, *, expected=200, token=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(
        BASE + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers=headers,
        method=method,
    )
    try:
        response = urllib.request.urlopen(request, timeout=60)
    except urllib.error.HTTPError as exc:
        response = exc
    with response:
        data = json.load(response)
        assert response.code == expected, (method, path, response.code, data)
        return data


def main():
    token = call(
        "POST",
        "/api/auth/login",
        {"email": "clean-admin@cruise.local", "password": os.environ["VERIFY_PASSWORD"]},
    )["access_token"]

    def api(method, path, body=None, expected=200):
        return call(method, "/api/database-setup" + path, body, expected=expected, token=token)

    before = api("GET", "/status")
    assert before["database_name"] == "cruise_v3_clean"
    assert api("GET", f"/products?q={CODE}")["total"] == 0, (
        "Reserved code exists; no writes allowed"
    )
    ports = call("GET", "/api/data/ports", token=token)
    port = next(row for row in ports if row["name"] == "大阪" and row["status"])
    product = call(
        "POST",
        "/api/data/products",
        {
            "code": CODE,
            "product_name_en": "Manual smoke only",
            "port_id": port["id"],
            "country_id": port["country_id"],
            "unit": "KG",
            "status": True,
        },
        expected=201,
        token=token,
    )
    base = f"/products/{product['id']}"
    config = api("GET", base + "/edit-config")
    values = {**config["values"], "product_name": "Manual smoke edited", "brand": "SMOKE"}
    for field in config["fields"]:
        if field["required"] and values.get(field["key"]) in (None, ""):
            assert field["options"], "Required field needs explicit test value"
            values[field["key"]] = field["options"][0]["value"]
    body = {
        "expected_revision": config["expected_revision"],
        "extension_revision": config["extension_revision"],
        "schema_version": config["schema_version"],
        "values": values,
    }
    saved = api("PATCH", base, body)
    assert saved["name"] == "Manual smoke edited" and saved["brand"] == "SMOKE"
    api("PATCH", base, body, expected=409)
    period_body = {
        "price_type": "purchase",
        "amount": 100,
        "currency": "JPY",
        "effective_from": "2028-01-01",
        "effective_to": "2028-01-31",
    }
    period = api("POST", base + "/periods", period_body, expected=201)
    api("POST", base + "/periods", period_body, expected=409)
    price_path = base + f"/periods/{period['id']}"
    changed = api(
        "PATCH",
        price_path,
        {
            "amount": 135,
            "currency": "JPY",
            "effective_from": "2028-01-01",
            "effective_to": "2028-02-28",
            "expected_revision": period["revision"],
        },
    )
    assert changed["amount"] == 135
    api("DELETE", price_path, {"expected_revision": period["revision"]}, expected=409)
    api("DELETE", price_path, {"expected_revision": changed["revision"]})
    assert api("GET", base)["price_periods"] == []
    api("POST", base + "/periods", period_body, expected=201)
    api(
        "POST",
        base + "/periods",
        {**period_body, "price_type": "selling", "amount": 200},
        expected=201,
    )
    preview = api("GET", base + "/deletion")
    assert preview["can_delete"] and preview["period_count"] == 2
    confirmation = {
        "expected_revision": preview["expected_revision"],
        "expected_extension_revision": preview["expected_extension_revision"],
        "confirm_code": "WRONG",
    }
    api("DELETE", base, confirmation, expected=409)
    result = api("DELETE", base, {**confirmation, "confirm_code": CODE})
    assert result["deleted_periods"] == 2
    api("GET", base, expected=404)
    after = api("GET", "/status")
    assert (before["product_count"], before["price_period_count"]) == (
        after["product_count"],
        after["price_period_count"],
    )
    print(
        json.dumps(
            {
                "result": "passed",
                "product_id": product["id"],
                "code": CODE,
                "before": before,
                "after": after,
                "test_product_permanently_deleted": True,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
