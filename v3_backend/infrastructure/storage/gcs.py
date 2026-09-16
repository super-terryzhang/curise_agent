"""Google Cloud Storage backend — used in prod.

Storage key shape matches the Supabase backend so v2 → GCS migration
preserves all DB-stored `file_url` paths exactly (e.g. `documents/abc.pdf`).

Lazy-imports `google.cloud.storage` so dev environments without the SDK
don't break.
"""

from __future__ import annotations

import logging
import os
import re

from infrastructure.storage.base import StorageError

logger = logging.getLogger(__name__)


def _safe_filename(name: str) -> str:
    """Same sanitization as Supabase backend — preserves key shape parity."""
    base, ext = os.path.splitext(name)
    base = re.sub(r"[^a-zA-Z0-9_\-.]", "_", base)
    base = re.sub(r"_+", "_", base).strip("_")
    return f"{base}{ext}" if base else f"file{ext}"


class GCSFileStorage:
    """Google Cloud Storage implementation of `FileStorage`."""

    def __init__(self, bucket: str, project: str | None = None) -> None:
        self.bucket_name = bucket
        self.project = project
        self._bucket = None  # lazy

    @property
    def bucket(self):  # type: ignore[no-untyped-def]
        if self._bucket is None:
            try:
                from google.cloud import storage as gcs_storage
            except ImportError as exc:  # pragma: no cover
                raise StorageError(
                    "google-cloud-storage SDK is not installed; add to deps."
                ) from exc
            client = gcs_storage.Client(project=self.project) if self.project else gcs_storage.Client()
            self._bucket = client.bucket(self.bucket_name)
        return self._bucket

    def upload(
        self,
        folder: str,
        filename: str,
        content: bytes,
        content_type: str = "application/octet-stream",
    ) -> str:
        safe = _safe_filename(filename)
        key = f"{folder}/{safe}"
        blob = self.bucket.blob(key)
        blob.upload_from_string(content, content_type=content_type)
        logger.info("gcs storage: uploaded %s (%d bytes)", key, len(content))
        return key

    def download(self, storage_key: str) -> bytes:
        try:
            blob = self.bucket.blob(storage_key)
            return blob.download_as_bytes()
        except Exception as exc:
            raise StorageError(f"download {storage_key}: {exc}") from exc

    def delete(self, storage_key: str) -> None:
        try:
            self.bucket.blob(storage_key).delete()
        except Exception as exc:
            # Best-effort delete; missing object is not an error.
            logger.warning("gcs storage: delete %s failed: %s", storage_key, exc)

    def get_signed_url(self, storage_key: str, expires_in: int = 3600) -> str:
        """Issue a short-lived proxy URL after caller authorization."""
        from infrastructure.storage.urls import signed_download_url

        return signed_download_url(storage_key, expires_in)
