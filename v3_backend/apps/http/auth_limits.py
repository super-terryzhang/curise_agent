"""Database-backed auth limits shared by all application workers."""

import time

from fastapi import Depends, HTTPException, Request
from sqlalchemy import delete
from sqlalchemy.orm import Session

from domains.identity.audit import fingerprint
from domains.identity.models import AuthRateWindow
from infrastructure.config import settings
from infrastructure.db.session import get_db


def consume(db: Session, identity: str, maximum: int, seconds: int) -> int:
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert

    now = int(time.time())
    window = (now // seconds) * seconds
    insert = pg_insert if db.bind.dialect.name == "postgresql" else sqlite_insert
    statement = insert(AuthRateWindow).values(key=fingerprint(identity), window=window, count=1)
    statement = statement.on_conflict_do_update(
        index_elements=[AuthRateWindow.key, AuthRateWindow.window],
        set_={"count": AuthRateWindow.count + 1},
    ).returning(AuthRateWindow.count)
    count = db.scalar(statement)
    db.execute(delete(AuthRateWindow).where(AuthRateWindow.window < now - 1800))
    db.commit()
    return seconds - (now - window) if count > maximum else 0


async def auth_rate_limit(request: Request, db: Session = Depends(get_db)) -> None:
    if request.method not in ("POST", "PATCH", "DELETE"):
        return
    path = request.url.path
    # Never trust arbitrary X-Forwarded-For values. Deployment must configure
    # its ASGI trusted proxy boundary; request.client is the effective peer.
    peer = request.client.host if request.client else "unknown"
    retry = consume(db, f"auth-source:{peer}", settings.AUTH_SOURCE_PER_MINUTE, 60)
    if retry:
        raise HTTPException(429, "请求过于频繁，请稍后重试", headers={"Retry-After": str(retry)})
    if path.endswith("/login"):
        try:
            body = await request.json()
            email = str(body.get("email", ""))[:320] if isinstance(body, dict) else ""
        except (ValueError, TypeError):
            email = ""
        retry = max(
            retry,
            consume(
                db, "auth-account:" + email.casefold(), settings.AUTH_ACCOUNT_PER_15_MINUTES, 900
            ),
        )
    if retry:
        raise HTTPException(429, "请求过于频繁，请稍后重试", headers={"Retry-After": str(retry)})
