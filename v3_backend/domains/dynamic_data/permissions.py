"""Trusted actor policy shared by structure and record services."""

from .errors import Forbidden
from .schemas import Actor

ADMINS = frozenset({"superadmin", "admin"})
WRITERS = ADMINS | {"employee", "finance"}


def require_admin(actor: Actor) -> None:
    if actor.role not in ADMINS:
        raise Forbidden("STRUCTURE_FORBIDDEN", "只有管理员可以配置表和字段")


def require_writer(actor: Actor) -> None:
    if actor.role not in WRITERS:
        raise Forbidden("RECORD_FORBIDDEN", "当前角色没有数据表管理访问权限")
