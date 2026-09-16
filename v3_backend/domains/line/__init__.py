"""LINE integration domain — line-user mapping, bind tokens, webhook event log.

Public API (use these from other layers):
- `service`: business-level flows (find_user_by_line_id, bind_user, generate_bind_token,
             consume_bind_token, record_event_id, ...)
- `schemas`: Pydantic DTOs for wire format
- `models`: ORM models (for Alembic migration discovery only;
            other domains should NOT import these)

This domain is consumed exclusively by `apps.line.*` (the LINE webhook /
handlers) and `apps.http.line_bind` (the binding endpoint). It does not
depend on any other domain except `identity` (for the User row to bind to)
— and even that goes through `domains.identity` public re-exports.
"""

from domains.line import schemas, service
from domains.line.models import LineBindToken, LineEventLog, LineUser

__all__ = ["service", "schemas", "LineUser", "LineBindToken", "LineEventLog"]
