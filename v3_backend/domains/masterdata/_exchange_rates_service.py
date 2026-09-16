"""Exchange rate CRUD + external API fetch."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import httpx
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from domains.masterdata import repository as repo
from domains.masterdata.errors import Conflict, NotFound, UpstreamUnavailable
from domains.masterdata.models import ExchangeRate
from domains.masterdata.schemas import (
    ExchangeRateCreate,
    ExchangeRateUpdate,
    FetchRatesResult,
)


def list_exchange_rates(
    db: Session,
    *,
    from_currency: str | None = None,
    to_currency: str | None = None,
) -> list[dict[str, Any]]:
    rows = repo.list_exchange_rates(db, from_currency=from_currency, to_currency=to_currency)
    return [_serialize(r) for r in rows]


def create_exchange_rate(db: Session, body: ExchangeRateCreate) -> dict[str, Any]:
    obj = ExchangeRate(
        from_currency=body.from_currency.upper(),
        to_currency=body.to_currency.upper(),
        rate=body.rate,
        effective_date=body.effective_date,
        source="manual",
    )
    db.add(obj)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise Conflict("该币种对在该日期已有汇率记录") from exc
    db.refresh(obj)
    return _serialize(obj)


def update_exchange_rate(db: Session, rate_id: int, body: ExchangeRateUpdate) -> dict[str, Any]:
    obj = repo.get_exchange_rate_row(db, rate_id)
    if obj is None:
        raise NotFound("汇率记录不存在")
    data = body.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(obj, k, v)
    obj.updated_at = datetime.now(UTC).replace(tzinfo=None)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise Conflict("该币种对在该日期已有汇率记录") from exc
    db.refresh(obj)
    return _serialize(obj)


def delete_exchange_rate(db: Session, rate_id: int) -> None:
    obj = repo.get_exchange_rate_row(db, rate_id)
    if obj is None:
        raise NotFound("汇率记录不存在")
    db.delete(obj)
    db.commit()


def fetch_exchange_rates(
    db: Session,
    *,
    base: str,
    targets: list[str],
    client: httpx.Client | None = None,
) -> FetchRatesResult:
    """Fetch latest rates from open.er-api.com and upsert into DB.

    `client` is injectable for tests.
    """
    base = base.upper()
    http_client = client or httpx.Client(timeout=15.0)
    try:
        try:
            resp = http_client.get(f"https://open.er-api.com/v6/latest/{base}")
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            raise UpstreamUnavailable(f"获取汇率失败: {exc}") from exc
    finally:
        if client is None:
            http_client.close()

    if data.get("result") != "success":
        raise UpstreamUnavailable(f"API 返回错误: {data.get('error-type', 'unknown')}")

    rates = data.get("rates", {}) or {}
    today = date.today()
    want_codes = [c.upper() for c in targets] if targets else list(rates.keys())

    now = datetime.now(UTC).replace(tzinfo=None)
    rows = [
        {
            "from_currency": base,
            "to_currency": code,
            "rate": rates[code],
            "effective_date": today,
            "source": "api",
            "created_at": now,
            "updated_at": now,
        }
        for code in want_codes
        if code != base and code in rates
    ]
    if not rows:
        return FetchRatesResult(created=0, updated=0, base=base, date=str(today))

    # Pre-count how many of these pairs already have a row for today.
    # Used to report created vs updated to the caller. Single round-trip.
    target_codes = [r["to_currency"] for r in rows]
    existing_count = db.execute(
        select(func.count(ExchangeRate.id)).where(
            ExchangeRate.from_currency == base,
            ExchangeRate.to_currency.in_(target_codes),
            ExchangeRate.effective_date == today,
        )
    ).scalar_one()

    # Postgres UPSERT — atomic, concurrent-safe. Cloud Scheduler retries
    # on 5xx fire two near-simultaneous requests; the old SELECT-then-
    # INSERT pattern lost the race and tripped `uq_exchange_rate_pair_date`.
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    stmt = pg_insert(ExchangeRate).values(rows)
    stmt = stmt.on_conflict_do_update(
        constraint="uq_exchange_rate_pair_date",
        set_={
            "rate": stmt.excluded.rate,
            "source": stmt.excluded.source,
            "updated_at": stmt.excluded.updated_at,
        },
    )
    db.execute(stmt)
    db.commit()
    return FetchRatesResult(
        created=len(rows) - existing_count,
        updated=existing_count,
        base=base,
        date=str(today),
    )


def _serialize(r: ExchangeRate) -> dict[str, Any]:
    return {
        "id": r.id,
        "from_currency": r.from_currency,
        "to_currency": r.to_currency,
        "rate": float(r.rate),
        "effective_date": str(r.effective_date),
        "source": r.source,
        "created_at": r.created_at,
        "updated_at": r.updated_at,
    }
