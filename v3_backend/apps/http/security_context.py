"""Non-secret request context for security events, including background execution."""

import uuid

from domains.identity.audit import fingerprint, request_context


class SecurityContextMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        client = scope.get("client") or ("unknown", 0)
        context = {
            "request_id": uuid.uuid4().hex,
            "source_hash": fingerprint(client[0]),
            "channel": "line"
            if scope.get("path", "").startswith(("/line", "/api/line"))
            else "http",
        }
        token = request_context.set(context)
        try:
            await self.app(scope, receive, send)
        finally:
            request_context.reset(token)
