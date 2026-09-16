"""Standalone release maintenance entrypoint; imports no database or business routes.

Run explicitly as ``uvicorn apps.http.maintenance:app`` during the controlled
migration window. The normal application retains its schema startup guard.
"""

import json


async def app(scope, receive, send):
    if scope["type"] == "lifespan":
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                await send({"type": "lifespan.shutdown.complete"})
                return
    elif scope["type"] == "websocket":
        await send({"type": "websocket.close", "code": 1013})
    elif scope["type"] == "http":
        health = scope["path"] == "/health" and scope["method"] in ("GET", "HEAD")
        payload = {"status": "maintenance"} if health else {
            "detail": "系统正在升级维护，请稍后重试。"
        }
        body = json.dumps(payload, ensure_ascii=False).encode()
        await send({"type": "http.response.start", "status": 200 if health else 503,
                    "headers": [(b"content-type", b"application/json; charset=utf-8"),
                                (b"content-length", str(len(body)).encode()),
                                (b"cache-control", b"no-store"), (b"retry-after", b"120")]})
        await send({"type": "http.response.body",
                    "body": b"" if scope["method"] == "HEAD" else body})
