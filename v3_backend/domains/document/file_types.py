"""Single source of truth for file-type handling.

Three layers used file_type / mime mappings independently before this
module existed (`service._EXT_TO_FILE_TYPE`,
`service._default_content_type`, `apps/http/documents.py` inline dict,
`workflow._MIME_MAP`). Drift was inevitable. We consolidate here.

Vocabulary:
- **extension** — lowercase suffix including the dot, e.g. ".pdf"
- **file_type** — short tag stored in `Document.file_type` (column is
  `String(20)`, so tags must be ≤ 20 chars). Examples: "pdf", "word",
  "image/jpeg".
- **mime** — IETF media-type string used for HTTP Content-Type and
  passed to extractors. Examples: "application/pdf",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document".

Whitelist policy (see Q1 from 2026-05-01 chat decision):
- Documents: PDF, Word (.docx + .doc), Excel (.xlsx + .xls), CSV, plain
  text, Markdown, JSON, XML, PowerPoint
- Images: JPEG, PNG, GIF, WebP, HEIC/HEIF, BMP, TIFF
- Excluded by design: video (any), audio, executables (.exe / .bat /
  .sh / .dll / .app), archives (.zip / .tar / .gz / .rar / .7z),
  databases (.sqlite / .db). These are out of the document-management
  scope and have higher security/cost concerns.
"""

from __future__ import annotations

# Extension → short file_type tag. Stored in DB column String(20) — keep
# tags ≤ 20 chars. Multiple extensions can map to the same tag (e.g.
# .docx and .doc both → "word").
EXTENSION_TO_FILE_TYPE: dict[str, str] = {
    # ─── Documents ───
    ".pdf": "pdf",
    ".docx": "word",
    ".doc": "word",
    ".xlsx": "excel",
    ".xls": "xls",
    ".csv": "csv",
    ".txt": "text",
    ".md": "markdown",
    ".markdown": "markdown",
    ".json": "json",
    ".xml": "xml",
    ".pptx": "powerpoint",
    ".ppt": "powerpoint",
    # ─── Images ───
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".heic": "image/heic",
    ".heif": "image/heic",
    ".bmp": "image/bmp",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
}


# file_type → IETF mime. Used by:
#   - service.upload_document → storage's content-type metadata
#   - HTTP /documents/{id}/file → response Content-Type header
#   - workflow.run_document_pipeline → mime passed to extract()
#
# Image file_types like "image/jpeg" already are mimes; they round-trip
# through identity.
FILE_TYPE_TO_MIME: dict[str, str] = {
    "pdf": "application/pdf",
    "word": (
        "application/vnd.openxmlformats-officedocument."
        "wordprocessingml.document"
    ),
    "excel": (
        "application/vnd.openxmlformats-officedocument."
        "spreadsheetml.sheet"
    ),
    "xls": "application/vnd.ms-excel",
    "csv": "text/csv",
    "text": "text/plain",
    "markdown": "text/markdown",
    "json": "application/json",
    "xml": "application/xml",
    "powerpoint": (
        "application/vnd.openxmlformats-officedocument."
        "presentationml.presentation"
    ),
    "image/jpeg": "image/jpeg",
    "image/png": "image/png",
    "image/gif": "image/gif",
    "image/webp": "image/webp",
    "image/heic": "image/heic",
    "image/bmp": "image/bmp",
    "image/tiff": "image/tiff",
    # Legacy: pre-2026-05-01 documents may have file_type="xlsx"
    # because that was an alias before consolidation. Keep so existing
    # rows still serve the right Content-Type.
    "xlsx": (
        "application/vnd.openxmlformats-officedocument."
        "spreadsheetml.sheet"
    ),
}


def detect_file_type(filename: str) -> str | None:
    """Map a filename to its short file_type tag.

    Returns None when the extension is missing or not in the whitelist —
    the upload endpoint should reject in that case.
    """
    if not filename:
        return None
    idx = filename.rfind(".")
    if idx < 0:
        return None
    ext = filename[idx:].lower()
    return EXTENSION_TO_FILE_TYPE.get(ext)


def default_content_type(file_type: str) -> str:
    """Return the IETF mime for a given file_type.

    Falls back to "application/octet-stream" for unknown tags so old
    DB rows with retired file_types still download (just as a generic
    binary).
    """
    return FILE_TYPE_TO_MIME.get(file_type, "application/octet-stream")


def supported_extensions() -> list[str]:
    """For UI / error messages."""
    return sorted(EXTENSION_TO_FILE_TYPE.keys())
