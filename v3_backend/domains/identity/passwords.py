"""Policy for newly set passwords; existing passwords remain verifiable."""

# Small, local blocklist: no user password is sent to an external service.
COMMON = frozenset(
    {
        "passwordpassword",
        "password123456789",
        "123456789012345",
        "1234567890123456",
        "qwertyuiopasdfgh",
        "qwertyuiopasdfghjkl",
        "administrator123",
        "letmeinletmein123",
        "changemechangeme",
        "welcome123456789",
        "cruise1234567890",
    }
)


def password_error(password: str) -> str | None:
    if len(password) < 15:
        return "新密码至少需要 15 个字符，可使用长口令"
    if len(password.encode("utf-8")) > 4096:
        return "密码过长"
    if password.casefold() in COMMON or len(set(password)) < 3:
        return "请避免常见或重复的弱密码"
    return None
