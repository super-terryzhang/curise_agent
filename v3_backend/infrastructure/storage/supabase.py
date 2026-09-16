"""Supabase Storage client — used in production.

Imports the supabase SDK lazily so dev environments without credentials
don't need the package installed.
"""

from __future__ import annotations

import logging
import os
import re

from infrastructure.storage.base import StorageError

logger = logging.getLogger(__name__)


def _safe_filename(name: str) -> str:
    base, ext = os.path.splitext(name)
    base = re.sub(r"[^a-zA-Z0-9_\-.]", "_", base)
    base = re.sub(r"_+", "_", base).strip("_")
    return f"{base}{ext}" if base else f"file{ext}"


class SupabaseFileStorage:
    def __init__(self, url: str, service_key: str, bucket: str) -> None:
        self.url = url
        self.service_key = service_key
        self.bucket = bucket
        self._client = None

    @property
    def client(self):  # type: ignore[no-untyped-def]
        if self._client is None:
            try:
                from supabase import create_client
            except ImportError as exc:  # pragma: no cover
                raise StorageError(
                    "Supabase SDK is not installed; add `supabase` to deps."
                ) from exc
            self._client = create_client(self.url, self.service_key)
        return self._client

    def upload(
        self,
        folder: str,
        filename: str,
        content: bytes,
        content_type: str = "application/octet-stream",
    ) -> str:
        safe = _safe_filename(filename)
        key = f"{folder}/{safe}"
        self.client.storage.from_(self.bucket).upload(
            key, content, {"content-type": content_type, "upsert": "true"}
        )
        logger.info("supabase storage: uploaded %s (%d bytes)", key, len(content))
        return key

    def download(self, storage_key: str) -> bytes:
        try:
            data: bytes = self.client.storage.from_(self.bucket).download(storage_key)
            return data
        except Exception as exc:
            raise StorageError(f"download {storage_key}: {exc}") from exc

    def delete(self, storage_key: str) -> None:
        try:
            self.client.storage.from_(self.bucket).remove([storage_key])
        except Exception as exc:
            logger.warning("supabase storage: delete %s failed: %s", storage_key, exc)

    def get_signed_url(self, storage_key: str, expires_in: int = 3600) -> str:
        """Issue a short-lived proxy URL after caller authorization."""
        from infrastructure.storage.urls import signed_download_url

        return signed_download_url(storage_key, expires_in)
