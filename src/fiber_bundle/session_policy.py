"""Admin session duration contract; zero means no server-side expiry."""

MAX_TTL = 30 * 24 * 60 * 60
# Browsers cap persistent cookies at 400 days. Restore renews only this cookie,
# never the server deadline of finite sessions.
PERMANENT_COOKIE_TTL = 400 * 24 * 60 * 60


def valid_ttl(value: object) -> bool:
    return type(value) is int and 0 <= value <= MAX_TTL


def cookie_ttl(ttl: int) -> int:
    return ttl if ttl else PERMANENT_COOKIE_TTL
