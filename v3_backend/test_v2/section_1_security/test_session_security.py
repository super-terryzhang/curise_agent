"""Behavioral regression tests for revocation, first-login restrictions and audit."""

from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from domains.identity import service
from domains.identity.models import AuthSession, RefreshToken, SecurityEvent
from infrastructure.config import Settings, settings
from infrastructure.security import decode_access_token, hash_password, verify_password
from test_v2.fixtures.helpers import seed_user


def login(client, email):
    response = client.post("/api/auth/login", json={"email": email, "password": "password123"})
    assert response.status_code == 200, response.text
    return response.json()


def headers(tokens):
    return {"Authorization": "Bearer " + tokens["access_token"]}


def test_logout_only_current_session(client, db):
    seed_user(db, email="logout@example.test")
    first, second = login(client, "logout@example.test"), login(client, "logout@example.test")
    assert (
        client.post("/api/auth/logout", json={"refresh_token": first["refresh_token"]}).status_code
        == 200
    )
    assert client.get("/api/auth/me", headers=headers(first)).status_code == 401
    assert (
        client.post("/api/auth/refresh", json={"refresh_token": first["refresh_token"]}).status_code
        == 401
    )
    assert client.get("/api/auth/me", headers=headers(second)).status_code == 200


def test_password_change_revokes_all_old_sessions(client, db):
    seed_user(db, email="change@example.test")
    first, second = login(client, "change@example.test"), login(client, "change@example.test")
    changed = client.post(
        "/api/auth/change-password",
        headers=headers(first),
        json={"current_password": "password123", "new_password": "a-new-password-for-this-account"},
    )
    assert changed.status_code == 200, changed.text
    for old in [first, second]:
        assert client.get("/api/auth/me", headers=headers(old)).status_code == 401
    assert client.get("/api/auth/me", headers=headers(changed.json())).status_code == 200


def test_reset_restricts_every_business_entry(client, db):
    user = seed_user(db, email="temporary@example.test")
    old = login(client, user.email)
    temp = service.reset_user_password(db, user_id=user.id)
    assert client.get("/api/auth/me", headers=headers(old)).status_code == 401
    data = client.post("/api/auth/login", json={"email": user.email, "password": temp}).json()
    assert data["refresh_token"] == "" and data["user"]["is_default_password"]
    assert client.get("/api/auth/me", headers=headers(data)).status_code == 200
    for path in ["/api/documents", "/api/orders", "/api/users"]:
        assert client.get(path, headers=headers(data)).status_code == 401
    assert client.post("/api/auth/refresh", json={"refresh_token": ""}).status_code == 401
    changed = client.post(
        "/api/auth/change-password",
        headers=headers(data),
        json={"current_password": temp, "new_password": "safe-replacement-password"},
    )
    assert changed.status_code == 200, changed.text
    assert client.get("/api/auth/me", headers=headers(data)).status_code == 401
    assert client.get("/api/documents", headers=headers(changed.json())).status_code == 200
    assert (
        client.post("/api/auth/login", json={"email": user.email, "password": temp}).status_code
        == 401
    )


def test_restricted_logout_and_expiration(client, db):
    user = seed_user(db, email="temp-exp@example.test")
    temp = service.reset_user_password(db, user_id=user.id)
    data = client.post("/api/auth/login", json={"email": user.email, "password": temp}).json()
    assert (
        client.post(
            "/api/auth/logout", headers=headers(data), json={"refresh_token": ""}
        ).status_code
        == 200
    )
    assert client.get("/api/auth/me", headers=headers(data)).status_code == 401
    user.temporary_password_expires_at = service._now() - timedelta(seconds=1)
    db.commit()
    assert (
        client.post("/api/auth/login", json={"email": user.email, "password": temp}).status_code
        == 401
    )


def test_refresh_retry_same_successor_then_replay_revokes_only_family(client, db):
    user = seed_user(db, email="rotate@example.test")
    old, other = login(client, user.email), login(client, user.email)
    a = client.post("/api/auth/refresh", json={"refresh_token": old["refresh_token"]})
    b = client.post("/api/auth/refresh", json={"refresh_token": old["refresh_token"]})
    assert a.status_code == b.status_code == 200
    assert a.json()["refresh_token"] == b.json()["refresh_token"]
    row = db.scalar(
        select(RefreshToken).where(
            RefreshToken.token_hash == service.hash_refresh_token(old["refresh_token"])
        )
    )
    row.rotated_at = service._now() - timedelta(seconds=20)
    db.commit()
    assert (
        client.post("/api/auth/refresh", json={"refresh_token": old["refresh_token"]}).status_code
        == 401
    )
    assert client.get("/api/auth/me", headers=headers(a.json())).status_code == 401
    assert client.get("/api/auth/me", headers=headers(other)).status_code == 200


def test_absolute_session_expiration_blocks_refresh(client, db):
    user = seed_user(db, email="absolute@example.test")
    data = login(client, user.email)
    row = db.get(AuthSession, decode_access_token(data["access_token"])["sid"])
    row.expires_at = service._now() - timedelta(seconds=1)
    db.commit()
    assert (
        client.post("/api/auth/refresh", json={"refresh_token": data["refresh_token"]}).status_code
        == 401
    )
    assert client.get("/api/auth/me", headers=headers(data)).status_code == 401


def test_superadmin_cannot_disable_or_demote_self(client, db):
    root = seed_user(db, email="root@example.test", role="superadmin")
    data = login(client, root.email)
    for patch in [{"is_active": False}, {"role": "employee"}]:
        assert (
            client.patch(f"/api/users/{root.id}", headers=headers(data), json=patch).status_code
            == 400
        )
    assert client.get("/api/auth/me", headers=headers(data)).status_code == 200


def test_last_root_and_legal_handover(db):
    first = seed_user(db, email="root1@example.test", role="superadmin")
    with pytest.raises(service.CannotDeactivateSelf):
        service.update_user(db, user_id=first.id, is_active=False)
    db.rollback()
    second = seed_user(db, email="root2@example.test", role="superadmin")
    service.update_user(db, user_id=first.id, role="employee", acting_user_id=second.id)
    assert first.role == "employee" and second.is_active


def test_login_rate_limit_unknown_accounts(client, monkeypatch):
    from apps.http import auth_limits

    clock = [1800000000]
    monkeypatch.setattr(auth_limits, "time", SimpleNamespace(time=lambda: clock[0]))
    monkeypatch.setattr(settings, "AUTH_SOURCE_PER_MINUTE", 10)
    responses = [
        client.post(
            "/api/auth/login",
            json={"email": f"unknown{i}@example.test", "password": "bad"},
            headers={"X-Forwarded-For": f"192.0.2.{i}"},
        )
        for i in range(11)
    ]
    assert responses[-1].status_code == 429
    assert int(responses[-1].headers["retry-after"]) > 0
    clock[0] += 60
    assert (
        client.post(
            "/api/auth/login", json={"email": "unknown@example.test", "password": "bad"}
        ).status_code
        == 401
    )


def test_startup_requires_current_migration(engine):
    from sqlalchemy import text

    from apps.http.startup import verify_schema

    with pytest.raises(RuntimeError, match="migration required"):
        verify_schema(engine)
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
        connection.execute(text("INSERT INTO alembic_version VALUES ('0024_auth_hardening')"))
    with pytest.raises(RuntimeError, match="migration required"):
        verify_schema(engine)
    with engine.begin() as connection:
        connection.execute(text("UPDATE alembic_version SET version_num = '0030_direct_bulk_images'"))
    verify_schema(engine)


def test_old_project_preview_is_not_implicitly_allowed(client):
    response = client.options(
        "/api/auth/login",
        headers={
            "Origin": "https://cruise-v3-frontend-abc-terryzhang-jps-projects.vercel.app",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert response.headers.get("access-control-allow-origin") is None


def test_generic_failures_and_lock_recovery(client, db):
    user = seed_user(db, email="locked@example.test")
    unknown = client.post(
        "/api/auth/login", json={"email": "unknown@example.test", "password": "wrong"}
    )
    for _ in range(5):
        failed = client.post("/api/auth/login", json={"email": user.email, "password": "wrong"})
        assert failed.status_code == unknown.status_code == 401
        assert failed.json() == unknown.json()
    user.locked_until = service._now() - timedelta(seconds=1)
    db.commit()
    client.post("/api/auth/login", json={"email": user.email, "password": "wrong"})
    db.refresh(user)
    assert user.failed_login_attempts == 1 and user.locked_until is None


def test_audit_survives_revoke_and_contains_no_credentials(client, db):
    root = seed_user(db, email="auditroot@example.test", role="superadmin")
    employee = seed_user(db, email="auditemp@example.test")
    data = login(client, root.email)
    path = f"/api/users/{employee.id}/capabilities/financials.view"
    assert client.post(path, headers=headers(data)).status_code == 201
    assert client.delete(path, headers=headers(data)).status_code == 200
    events = list(db.scalars(select(SecurityEvent)))
    assert {"capability_granted", "capability_revoked"} <= {e.event_type for e in events}
    for event in events:
        assert "password123" not in str(event.details)
        assert data["access_token"] not in str(event.details)
        assert event.request_id
    other = login(client, employee.email)
    assert client.get("/api/auth/security-events", headers=headers(other)).status_code == 403
    assert client.get("/api/auth/security-events", headers=headers(data)).status_code == 200


def test_long_passwords_are_not_silently_truncated():
    first = "长口令" * 30 + "first"
    other = "长口令" * 30 + "other"
    hashed = hash_password(first)
    assert verify_password(first, hashed)
    assert not verify_password(other, hashed)


def test_tool_context_reloads_role_and_revoked_session(client, db):
    from agent.runtime.deps import V3Deps, get_deps

    user = seed_user(db, email="tool@example.test", role="admin")
    data = login(client, user.email)
    deps = V3Deps(
        db=db,
        user_id=user.id,
        user_role="admin",
        auth_session_id=decode_access_token(data["access_token"])["sid"],
    )
    ctx = SimpleNamespace(extras={"v3_deps": deps})
    user.role = "employee"
    db.commit()
    assert get_deps(ctx).user_role == "employee"
    service.logout(db, refresh_token=data["refresh_token"])
    with pytest.raises(service.AccountInactive):
        get_deps(ctx)


def test_production_configuration_rejects_defaults():
    with pytest.raises(ValueError):
        Settings(_env_file=None, ENV="production", SECRET_KEY="dev-secret-change-in-production")
    with pytest.raises(ValueError):
        Settings(_env_file=None, ENV="prodution")


def test_other_environment_and_wrong_purpose_rejected(client, db, monkeypatch):
    from jose import jwt

    user = seed_user(db, email="issuer@example.test")
    data = login(client, user.email)
    claims = decode_access_token(data["access_token"])
    claims["purpose"] = "download"
    token = jwt.encode(claims, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM)
    assert (
        client.get("/api/auth/me", headers={"Authorization": "Bearer " + token}).status_code == 401
    )
    monkeypatch.setattr(settings, "JWT_ISSUER", "different-environment")
    assert client.get("/api/auth/me", headers=headers(data)).status_code == 401


def test_delayed_retry_after_another_tab_rotates_stays_in_same_family(db):
    user = seed_user(db, email="retry-chain@example.test")
    first = service.login(db, email=user.email, password="password123")
    second = service.refresh_tokens(db, refresh_token=first.refresh_token)
    third = service.refresh_tokens(db, refresh_token=second.refresh_token)
    retry = service.refresh_tokens(db, refresh_token=first.refresh_token)
    assert retry.refresh_token == third.refresh_token
    assert (
        len(list(db.scalars(select(RefreshToken).where(RefreshToken.is_revoked.is_(False))))) == 1
    )


def test_queued_password_change_rechecks_revoked_session(db):
    user = seed_user(db, email="queued-change@example.test")
    first = service.login(db, email=user.email, password="password123")
    service.get_user_from_access_token(db, access_token=first.access_token)
    service.logout(db, refresh_token=first.refresh_token)
    with pytest.raises(service.AccountInactive):
        service.change_password(
            db, user=user, current_password="password123", new_password="new-valid-password-2026"
        )
    db.rollback()
    assert verify_password("password123", db.get(type(user), user.id).hashed_password)


def test_audit_write_failure_rolls_back_password_and_revocation(db, monkeypatch):
    from domains.identity import audit

    user = seed_user(db, email="atomic-change@example.test")
    first = service.login(db, email=user.email, password="password123")
    with monkeypatch.context() as patch:

        def fail(*args, **kwargs):
            raise RuntimeError("synthetic audit failure")

        patch.setattr(audit, "record", fail)
        with pytest.raises(RuntimeError):
            service.change_password(
                db,
                user=user,
                current_password="password123",
                new_password="new-valid-password-2026",
            )
    db.rollback()
    assert verify_password("password123", db.get(type(user), user.id).hashed_password)
    assert service.get_user_from_access_token(db, access_token=first.access_token).id == user.id


def test_retention_dry_run_and_apply_keep_current_records(db):
    from domains.identity import audit

    user = seed_user(db, email="retention@example.test")
    first = service.login(db, email=user.email, password="password123")
    audit.record(db, "synthetic_old_event")
    db.flush()
    old = db.scalar(select(SecurityEvent).where(SecurityEvent.event_type == "synthetic_old_event"))
    old.occurred_at = service._now() - timedelta(days=settings.SECURITY_EVENT_RETENTION_DAYS + 1)
    db.commit()
    assert audit.purge_expired(db) == 1
    assert db.get(SecurityEvent, old.id) is not None
    assert audit.purge_expired(db, apply=True) == 1
    assert audit.purge_expired_sessions(db, apply=True) == 0
    assert service.get_user_from_access_token(db, access_token=first.access_token).id == user.id


def test_access_log_filter_removes_download_and_bearer_credentials():
    import logging

    from infrastructure.log_redaction import CredentialFilter

    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        "",
        0,
        "GET %s Authorization: %s",
        ("/uploads/file.pdf?expires=123&signature=synthetic-signature", "Bearer synthetic-token"),
        None,
    )
    CredentialFilter().filter(record)
    assert "synthetic-signature" not in record.getMessage()
    assert "synthetic-token" not in record.getMessage()


def test_access_log_filter_preserves_uvicorn_formatter_arguments():
    """Clearing access-log args breaks Uvicorn's real five-field formatter."""
    import logging

    from uvicorn.logging import AccessFormatter

    from infrastructure.log_redaction import CredentialFilter

    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        "",
        0,
        '%s - "%s %s HTTP/%s" %d',
        (
            "127.0.0.1:12345",
            "GET",
            "/uploads/file.pdf?expires=123&signature=synthetic-signature",
            "1.1",
            200,
        ),
        None,
    )
    CredentialFilter().filter(record)

    rendered = AccessFormatter(
        '%(client_addr)s - "%(request_line)s" %(status_code)s',
        use_colors=False,
    ).format(record)

    assert "GET /uploads/file.pdf?[redacted] HTTP/1.1" in rendered
    assert "synthetic-signature" not in rendered


def test_production_configuration_accepts_explicit_safe_values():
    safe = {
        "_env_file": None,
        "ENV": "production",
        "SECRET_KEY": "synthetic-production-configuration-key",
        "DATABASE_URL": "postgresql://localhost/test",
        "JWT_ISSUER": "cruise-isolated-validation",
        "JWT_AUDIENCE": "cruise-api",
        "PUBLIC_BACKEND_URL": "https://backend.example.test",
        "STORAGE_BACKEND": "gcs",
        "ALLOWED_ORIGINS": ["https://frontend.example.test"],
    }
    assert Settings(**safe).ENV == "production"
    with pytest.raises(ValueError):
        Settings(_env_file=None, ENV="development", K_SERVICE="cloud-service")
    safe["JWT_ISSUER"] = "cruise-development"
    with pytest.raises(ValueError) as failure:
        Settings(**safe)
    assert safe["SECRET_KEY"] not in str(failure.value)
    assert "JWT_ISSUER" in str(failure.value)


def test_reenable_and_repeated_reset_never_revive_old_credentials(client, db):
    user = seed_user(db, email="reactivation@example.test")
    old = login(client, user.email)
    service.update_user(db, user_id=user.id, is_active=False)
    service.update_user(db, user_id=user.id, is_active=True)
    assert client.get("/api/auth/me", headers=headers(old)).status_code == 401
    first_temp = service.reset_user_password(db, user_id=user.id)
    second_temp = service.reset_user_password(db, user_id=user.id)
    assert first_temp != second_temp
    assert (
        client.post(
            "/api/auth/login", json={"email": user.email, "password": first_temp}
        ).status_code
        == 401
    )
    restricted = client.post("/api/auth/login", json={"email": user.email, "password": second_temp})
    assert restricted.status_code == 200
    from agent.runtime.deps import V3Deps, get_deps

    deps = SimpleNamespace(extras={"v3_deps": V3Deps(db=db, user_id=user.id)})
    with pytest.raises(service.AccountInactive):
        get_deps(deps)


def test_wrong_audience_and_rotated_key_reject_old_access(client, db, monkeypatch):
    from jose import jwt

    user = seed_user(db, email="audience@example.test")
    old = login(client, user.email)
    claims = decode_access_token(old["access_token"])
    claims["aud"] = "another-application"
    invalid = jwt.encode(claims, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM)
    assert (
        client.get("/api/auth/me", headers={"Authorization": "Bearer " + invalid}).status_code
        == 401
    )
    monkeypatch.setattr(settings, "SECRET_KEY", "synthetic-rotated-key-for-validation")
    assert client.get("/api/auth/me", headers=headers(old)).status_code == 401


def test_controlled_recovery_promotes_existing_account_without_hidden_user(db):
    from sqlalchemy import func

    from domains.identity.models import User

    user = seed_user(db, email="recovery@example.test", role="employee")
    original_hash = user.hashed_password
    before = db.scalar(select(func.count()).select_from(User))
    restored = service.update_user(db, user_id=user.id, role="superadmin", is_active=True)
    assert restored.role == "superadmin" and restored.is_active
    assert restored.hashed_password == original_hash
    assert db.scalar(select(func.count()).select_from(User)) == before
    event = db.scalar(select(SecurityEvent).where(SecurityEvent.event_type == "user_updated"))
    assert event.target_id == user.id and event.channel == "internal"
