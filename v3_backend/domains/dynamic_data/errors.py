"""Safe, structured domain failures, translated by the HTTP adapter."""

from .schemas import ErrorIssue


class DataTableError(Exception):
    def __init__(self, code: str, message: str, issues: list[ErrorIssue] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.issues = issues or []


class ValidationError(DataTableError):
    pass


class Conflict(DataTableError):
    pass


class NotFound(DataTableError):
    pass


class Forbidden(DataTableError):
    pass
