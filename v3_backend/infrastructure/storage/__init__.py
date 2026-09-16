"""File storage — Supabase in production, local filesystem for dev.

Public API:
- `FileStorage` Protocol
- `get_storage()` — returns the configured storage (Supabase if URL+key set, else Local)
- `LocalFileStorage` / `SupabaseFileStorage` concrete implementations (for tests)
"""

from infrastructure.storage.base import FileStorage, StorageError
from infrastructure.storage.local import LocalFileStorage
from infrastructure.storage.selector import get_storage, set_storage_for_tests
from infrastructure.storage.supabase import SupabaseFileStorage

__all__ = [
    "FileStorage",
    "StorageError",
    "LocalFileStorage",
    "SupabaseFileStorage",
    "get_storage",
    "set_storage_for_tests",
]
