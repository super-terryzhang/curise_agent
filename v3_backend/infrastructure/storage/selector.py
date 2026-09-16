"""Pick a storage backend based on settings.

Priority:
    1. STORAGE_BACKEND env var, when set, picks explicitly:
       - "gcs":      Google Cloud Storage (prod on GCP)
       - "supabase": legacy Supabase Storage
       - "local":    LocalFileStorage (./uploads)
    2. If unset, fall back to legacy behavior:
       SUPABASE_URL + SUPABASE_SERVICE_KEY → Supabase
       otherwise → Local
"""

from __future__ import annotations

import logging

from infrastructure.config import settings
from infrastructure.storage.base import FileStorage
from infrastructure.storage.local import LocalFileStorage
from infrastructure.storage.supabase import SupabaseFileStorage

logger = logging.getLogger(__name__)

_storage: FileStorage | None = None


def get_storage() -> FileStorage:
    """Return the process-wide storage instance (lazy init)."""
    global _storage
    if _storage is None:
        _storage = _build()
    return _storage


def set_storage_for_tests(storage: FileStorage) -> None:
    """Override the process-wide storage — tests only."""
    global _storage
    _storage = storage


def _build() -> FileStorage:
    backend = getattr(settings, "STORAGE_BACKEND", "") or ""
    backend = backend.lower().strip()

    if backend == "gcs":
        from infrastructure.storage.gcs import GCSFileStorage

        logger.info("storage: GCS (bucket=%s)", settings.STORAGE_BUCKET)
        return GCSFileStorage(bucket=settings.STORAGE_BUCKET)

    if backend == "supabase" or (
        not backend and settings.SUPABASE_URL and settings.SUPABASE_SERVICE_KEY
    ):
        logger.info("storage: Supabase (bucket=%s)", settings.STORAGE_BUCKET)
        return SupabaseFileStorage(
            url=settings.SUPABASE_URL,
            service_key=settings.SUPABASE_SERVICE_KEY,
            bucket=settings.STORAGE_BUCKET,
        )

    root = "./uploads"
    logger.info("storage: Local (root=%s)", root)
    return LocalFileStorage(root)
