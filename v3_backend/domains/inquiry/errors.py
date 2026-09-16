"""Inquiry domain errors."""

from __future__ import annotations


class InquiryError(Exception):
    pass


class NotFound(InquiryError):
    pass


class BadRequest(InquiryError):
    pass


class Conflict(InquiryError):
    pass
