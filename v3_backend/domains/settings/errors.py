"""Settings domain errors."""

from __future__ import annotations


class SettingsError(Exception):
    pass


class NotFound(SettingsError):
    pass


class Conflict(SettingsError):
    pass


class BadRequest(SettingsError):
    pass
