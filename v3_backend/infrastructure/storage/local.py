"""Local filesystem storage — fallback for dev and tests."""

from __future__ import annotations

import logging
import os
import re
import uuid
from pathlib import Path

from infrastructure.storage.base import StorageError

logger = logging.getLogger(__name__)


def _safe_segment(name: str) -> str:
    """Replace unsafe characters. Keeps ASCII alphanumerics, hyphens, underscores, dots."""
    base, ext = os.path.splitext(name)
    base = re.sub(r"[^a-zA-Z0-9_\-.]", "_", base)
    base = re.sub(r"_+", "_", base).strip("_")
    return f"{base}{ext}" if base else f"file{ext}"


class LocalFileStorage:
    """Writes files into a root directory. `storage_key` is the relative path."""

    def __init__(self, root: str | os.PathLike[str]) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def upload(
        self,
        folder: str,
        filename: str,
        content: bytes,
        content_type: str = "application/octet-stream",
    ) -> str:
        safe_name = _safe_segment(filename)
        # Prefix with a short uuid to avoid collisions without breaking the filename.
        key = f"{folder}/{uuid.uuid4().hex[:8]}_{safe_name}"
        abs_path = self.root / key
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        abs_path.write_bytes(content)
        logger.info("local storage: wrote %s (%d bytes)", key, len(content))
        return key

    def download(self, storage_key: str) -> bytes:
        path = self.root / storage_key
        if not path.exists():
            raise StorageError(f"not found: {storage_key}")
        return path.read_bytes()

    def delete(self, storage_key: str) -> None:
        path = self.root / storage_key
        if path.exists():
            try:
                path.unlink()
            except OSError as exc:
                logger.warning("local storage: delete %s failed: %s", storage_key, exc)

    def get_signed_url(self, storage_key: str, expires_in: int = 3600) -> str:
        """Issue a short-lived proxy URL after caller authorization."""
        from infrastructure.storage.urls import signed_download_url

        return signed_download_url(storage_key, expires_in)
