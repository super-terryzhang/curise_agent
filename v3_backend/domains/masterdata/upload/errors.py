"""Exception types for the upload pipeline."""

from __future__ import annotations


class UploadError(Exception):
    """Base for any upload-pipeline failure."""


class BatchNotFound(UploadError):
    pass


class BatchOwnedByOther(UploadError):
    """Cross-user access attempt."""


class BatchInWrongState(UploadError):
    """e.g. tried to commit a batch that's still parsing or already committed."""


class BatchValidationFailed(UploadError):
    """The deterministic workflow found blocking header/row issues."""


class ParseError(UploadError):
    """The Excel file couldn't be read or has no usable rows."""
