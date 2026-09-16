"""Signed file delivery for browser PDF/image previews across storage backends."""

from __future__ import annotations

import mimetypes
import re
from pathlib import PurePosixPath
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import Response

from infrastructure.storage import get_storage
from infrastructure.storage.base import StorageError
from infrastructure.storage.urls import normalize_key, valid_download_signature

router = APIRouter(tags=["files"])


# Content-Type fallbacks for types `mimetypes` doesn't ship by default.
_EXTRA_MIME_TYPES = {
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xls": "application/vnd.ms-excel",
}


@router.api_route("/uploads/{file_path:path}", methods=["GET", "HEAD"])
def serve_uploaded_file(
    file_path: str, request: Request, expires: int = 0, signature: str = ""
) -> Response:
    safe_key = _validate_key(file_path)
    if not valid_download_signature(safe_key, expires, signature):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "文件链接无效或已过期")
    try:
        content = get_storage().download(safe_key)
    except StorageError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "文件不存在") from exc
    size = len(content)
    headers = {
        "Content-Disposition": "inline; filename*=UTF-8''" + quote(PurePosixPath(safe_key).name),
        "Cache-Control": "private, no-store",
        "Referrer-Policy": "no-referrer",
        "X-Content-Type-Options": "nosniff",
        "Accept-Ranges": "bytes",
    }
    code = 200
    byte_range = request.headers.get("range") if request.method == "GET" else None
    if byte_range and not request.headers.get("if-range"):
        match = re.fullmatch(r"bytes=(\d*)-(\d*)", byte_range) if len(byte_range) < 128 else None
        if match and any(match.groups()):
            first, last = match.groups()
            start = int(first) if first else max(0, size - int(last))
            end = min(int(last), size - 1) if first and last else size - 1
            if start > end or start >= size or (not first and int(last) == 0):
                return Response(
                    status_code=416, headers={**headers, "Content-Range": f"bytes */{size}"}
                )
            headers["Content-Range"] = f"bytes {start}-{end}/{size}"
            content = content[start : end + 1]
            code = 206
        # Unsupported/multiple ranges are ignored and receive the full entity.
    headers["Content-Length"] = str(len(content))
    return Response(
        content=b"" if request.method == "HEAD" else content,
        status_code=code,
        media_type=_guess_media_type(safe_key),
        headers=headers,
    )


def _validate_key(file_path: str) -> str:
    try:
        return normalize_key(file_path)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid path") from exc


def _guess_media_type(file_path: str) -> str:
    suffix = PurePosixPath(file_path).suffix.lower()
    if suffix in _EXTRA_MIME_TYPES:
        return _EXTRA_MIME_TYPES[suffix]
    guessed, _ = mimetypes.guess_type(file_path)
    return guessed or "application/octet-stream"
