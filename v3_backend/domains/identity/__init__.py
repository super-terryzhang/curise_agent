"""Identity domain — users, authentication, authorization.

Public API (use these from other layers):
- `service`: business-level flows (login, refresh, logout, change_password, ...)
- `schemas`: Pydantic DTOs for wire format
- `models`: ORM models (for Alembic migration discovery only;
            other domains should NOT import these)
"""

from domains.identity import schemas, service
from domains.identity.models import RefreshToken, User

__all__ = ["service", "schemas", "User", "RefreshToken"]
