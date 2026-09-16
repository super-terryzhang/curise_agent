"""POST /api/line/bind/{token} — consume a LINE bind token.

Flow (kicked off by the LINE bot itself):
1. User sends any message to the bot.
2. Bot replies with `LINE_BIND_BASE_URL/line/bind?token=<plaintext>`.
3. User opens the link in a browser, frontend collects email + password.
4. Frontend POSTs (token, email, password) here.
5. Backend verifies password (delegates to `domains.identity.service.login`),
   consumes the token (single-use, expiring), and writes the LineUser row.

Why password instead of LINE Login OAuth: zero new OAuth client to manage,
zero new SSO surface. The price is the user types their password — but
they only do this once per LINE-account-to-internal-user binding. If a
future phase wants OAuth, it slots in here as a second route variant.

Security:
- Token is one-shot (consume_bind_token raises on second use).
- Token is short-lived (default 30 min via LINE_BIND_TOKEN_TTL_MINUTES).
- Password failures count toward `users.failed_login_attempts` exactly
  like a normal login — five wrong tries lock the account, same as web.
- Any error path returns a generic message; we never reveal which of
  email / password / token was wrong.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, status

from apps.http._deps import DbDep
from domains.identity import service as identity_service
from domains.line import service as line_service
from domains.line.schemas import BindRequest, BindResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/line", tags=["line"])


@router.post("/bind/{token}", response_model=BindResponse)
def bind(token: str, body: BindRequest, db: DbDep) -> BindResponse:
    # 1. Verify the user's identity. We deliberately call `login` (not just
    # `verify_password`) so failed attempts feed the existing lockout
    # mechanism. login() will raise on bad creds — we translate to a
    # generic 401.
    try:
        token_response = identity_service.login(
            db, email=body.email, password=body.password
        )
    except identity_service.AuthError:
        # InvalidCredentials, AccountLocked, AccountInactive — all 401.
        # We don't differentiate because doing so leaks which accounts exist.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="邮箱或密码错误",
        )

    user_id = token_response.user.id
    user_email = token_response.user.email
    user_role = token_response.user.role

    # 2. Consume the bind token. From this point on the token can never
    # be reused, even if the bind itself fails downstream.
    try:
        line_user_id, channel_id = line_service.consume_bind_token(
            db, plaintext=token
        )
    except (line_service.BindTokenInvalid, line_service.BindTokenAlreadyUsed):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="绑定令牌无效，请重新发起绑定",
        )
    except line_service.BindTokenExpired:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="绑定令牌已过期，请发送任意消息重新获取",
        )

    # 3. Bind. If this LINE userId is already mapped to a *different*
    # internal user, we refuse — re-binding to the same user is fine.
    try:
        line_service.bind_user(
            db,
            line_user_id=line_user_id,
            channel_id=channel_id,
            internal_user_id=user_id,
            display_name=token_response.user.full_name,
        )
    except line_service.LineUserAlreadyBound:
        # Already bound to someone else — superadmin needs to unbind first.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="该 LINE 账号已绑定其他公司账号，请联系管理员解绑后重试",
        )

    logger.info(
        "LINE bind: line_user=%s... → internal_user=%d (%s)",
        line_user_id[:8],
        user_id,
        user_email,
    )

    return BindResponse(
        line_user_id=line_user_id,
        user_id=user_id,
        user_email=user_email,
        user_role=user_role,
    )
