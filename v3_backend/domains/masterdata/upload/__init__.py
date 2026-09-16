"""Master-data upload pipeline.

Public surface (consumed by `agent/runtime/tools/data_upload.py` and the
HTTP `/api/data-upload/*` endpoints):

- `parse_excel(...)`         create a batch + staging rows from an Excel file
- `resolve_and_score(...)`   score every staging row against the live products table
- `preview_changes(...)`     return the diff the user is about to commit
- `commit_batch(...)`        apply changes atomically + write changelog
- `cancel_batch(...)`        discard a resolved-but-uncommitted batch
- `rollback_batch(...)`      undo a committed batch via the changelog
- `list_batches(...)`        user-scoped list

The pipeline is the only place in v3 that does bulk writes to the
`products` table. Single-row updates go through `domains.masterdata.service`
as before. RULE-2 status: this module is whitelisted in
`scripts/check_arch.py` as a future addition (the upload subpackage
also writes via `db.add` / `db.commit`).
"""

from domains.masterdata.upload.errors import UploadError
from domains.masterdata.upload.service import (
    cancel_batch,
    commit_validated_batch,
    commit_batch,
    get_batch,
    get_workflow_batch,
    get_workflow_file_key,
    get_workflow_rows,
    inspect_row,
    list_batches,
    list_workflow_batches,
    parse_excel,
    preview_changes,
    resolve_and_score,
    rollback_batch,
    search_batches,
    validate_workflow_batch,
)

__all__ = [
    "UploadError",
    "cancel_batch",
    "commit_validated_batch",
    "commit_batch",
    "get_batch",
    "get_workflow_batch",
    "get_workflow_file_key",
    "get_workflow_rows",
    "inspect_row",
    "list_batches",
    "list_workflow_batches",
    "parse_excel",
    "preview_changes",
    "resolve_and_score",
    "rollback_batch",
    "search_batches",
    "validate_workflow_batch",
]
