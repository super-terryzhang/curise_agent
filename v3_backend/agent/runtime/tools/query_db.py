"""Read-only business SQL compiled against an explicit table/column/syntax allowlist."""

from __future__ import annotations

import json
import time
from typing import Any

from sqlalchemy import text

from agent.runtime.deps import get_deps
from general_agent import ToolContext, tool

MAX_ROWS = 50

@tool(toolset="business_advanced", emoji="🗄️")
def query_db(sql: str, *, ctx: ToolContext) -> str:
    """Query approved business data (SELECT/CTE, at most 50 result rows).

    Available tables/columns:
    products: id, code, product_name_en, product_name_jp, country_id,
      category_id, supplier_id, port_id, unit, price, contract_price, unit_size, status
    suppliers: id, name, country_id, contact, email, phone, address, zip_code, fax
    countries: id, name, code, status
    ports: id, name, code, country_id, location, status
    categories: id, name, code, description, status
    v2_exchange_rates: id, from_currency, to_currency, rate, effective_date

    Accounts, credentials, chats and orders are not SQL-accessible. Use
    the dedicated order/financial tools, which enforce current permissions.
    """
    from agent.runtime.sql_policy import compile_query

    deps = get_deps(ctx)
    if deps.user_role not in ("admin", "superadmin"):
        return "Error: query permission denied"
    dialect = "postgres" if deps.db.bind.dialect.name == "postgresql" else "sqlite"
    try:
        validated = compile_query(sql, dialect)
    except Exception:
        return "Error: query is outside the approved business schema or syntax"
    sqlite_connection = None
    try:
        with deps.db.begin_nested():
            if dialect == "postgres":
                old_timeout = deps.db.execute(text("SHOW statement_timeout")).scalar_one()
                deps.db.execute(text("SELECT set_config('statement_timeout', '3000', true)"))
            else:
                sqlite_connection = deps.db.connection().connection.driver_connection
                deadline = time.monotonic() + 3
                sqlite_connection.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
            result = deps.db.execute(text(validated))
            columns = list(result.keys())
            rows = [_to_jsonable(dict(zip(columns, r, strict=True))) for r in result.fetchmany(MAX_ROWS + 1)]
            result.close()
            if dialect == "postgres":
                deps.db.execute(text("SELECT set_config('statement_timeout', :value, true)"), {"value": old_timeout})
    except Exception:
        return "Error: business query failed or exceeded its execution limit"
    finally:
        if sqlite_connection is not None:
            sqlite_connection.set_progress_handler(None, 0)
    return json.dumps(
        {"columns": columns, "rows": rows[:MAX_ROWS], "truncated": len(rows) > MAX_ROWS},
        ensure_ascii=False, default=str,
    )


def _to_jsonable(row: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in row.items():
        if isinstance(v, bytes):
            out[k] = v.decode("utf-8", errors="replace")
        elif hasattr(v, "isoformat"):
            out[k] = v.isoformat()
        elif v is None or isinstance(v, (str, int, float, bool, list, dict)):
            out[k] = v
        else:
            out[k] = str(v)
    return out
