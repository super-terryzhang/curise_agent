"""FileStorage protocol — the only thing business code depends on."""

from __future__ import annotations

from typing import Protocol


class StorageError(Exception):
    pass


class FileStorage(Protocol):
    """Persistent blob storage.

    `storage_key` is opaque to callers. For `LocalFileStorage` it is a relative
    path; for `SupabaseFileStorage` it's a storage bucket key.
    """

    def upload(
        self,
        folder: str,
        filename: str,
        content: bytes,
        content_type: str = "application/octet-stream",
    ) -> str:
        """Upload bytes, return a `storage_key` usable with `download` / `delete`."""
        ...

    def download(self, storage_key: str) -> bytes:
        """Read bytes for a key. Raises `StorageError` if missing."""
        ...

    def delete(self, storage_key: str) -> None:
        """Best-effort delete. Silent if the key doesn't exist."""
        ...

    def get_signed_url(self, storage_key: str, expires_in: int = 3600) -> str:
        """Short-lived, resource-scoped URL. Caller must authorize access before issuing."""
        ...
