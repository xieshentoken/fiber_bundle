"""Explicit read-only boundaries used by the cooperative organization writer."""

from collections.abc import Callable
from contextvars import ContextVar
from functools import wraps
from typing import Any, cast

current: ContextVar[Callable[[Callable[[], Any]], Any] | None] = ContextVar(
    "storage_read_scope", default=None
)


def read[T](operation: Callable[[], T]) -> T:
    owner = current.get()
    return cast(T, owner(operation)) if owner else operation()


def audited[**P, T](function: Callable[P, T]) -> Callable[P, T]:
    @wraps(function)
    def wrapped(*args: P.args, **kwargs: P.kwargs) -> T:
        return read(lambda: function(*args, **kwargs))

    return wrapped
