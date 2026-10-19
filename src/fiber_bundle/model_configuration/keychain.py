"""macOS Security framework access; never put secret data in process arguments."""

import ctypes as C
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from .errors import ModelError

SERVICE = "local.fiber-bundle.model-vault.v1"
_interactive: ContextVar[bool] = ContextVar("keychain_interactive", default=True)


@contextmanager
def noninteractive() -> Iterator[None]:
    """Background callers fail when interaction is required; no UI while holding a lease."""
    token = _interactive.set(False)
    try:
        yield
    finally:
        _interactive.reset(token)


def _operation(account: str, secret: bytes | None = None, *, remove: bool = False) -> bytes:
    if sys.platform != "darwin":
        raise ModelError("model_keychain_unavailable")
    cf = C.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
    sec = C.CDLL("/System/Library/Frameworks/Security.framework/Security")
    pointer = C.c_void_p
    cf.CFStringCreateWithCString.argtypes = [pointer, C.c_char_p, C.c_uint32]
    cf.CFStringCreateWithCString.restype = pointer
    cf.CFDataCreate.argtypes = [pointer, pointer, C.c_long]
    cf.CFDataCreate.restype = pointer
    cf.CFDictionaryCreate.argtypes = [pointer, pointer, pointer, C.c_long, pointer, pointer]
    cf.CFDictionaryCreate.restype = pointer
    cf.CFDataGetLength.argtypes = [pointer]
    cf.CFDataGetLength.restype = C.c_long
    cf.CFDataGetBytePtr.argtypes = [pointer]
    cf.CFDataGetBytePtr.restype = pointer
    cf.CFRelease.argtypes = [pointer]
    sec.SecItemCopyMatching.argtypes = [pointer, C.POINTER(pointer)]
    sec.SecItemAdd.argtypes = [pointer, pointer]
    sec.SecItemDelete.argtypes = [pointer]
    owned: list[int] = []

    def constant(lib: C.CDLL, name: str) -> int:
        return int(pointer.in_dll(lib, name).value or 0)

    def string(value: str) -> int:
        obj = cf.CFStringCreateWithCString(None, value.encode(), 0x08000100)
        owned.append(obj)
        return int(obj)

    values = {
        constant(sec, "kSecClass"): constant(sec, "kSecClassGenericPassword"),
        constant(sec, "kSecAttrService"): string(SERVICE),
        constant(sec, "kSecAttrAccount"): string(account),
    }
    if secret is None and not remove:
        values[constant(sec, "kSecReturnData")] = constant(cf, "kCFBooleanTrue")
        if not _interactive.get():
            values[constant(sec, "kSecUseAuthenticationUI")] = constant(
                sec, "kSecUseAuthenticationUIFail"
            )
    elif secret is not None:
        buffer = C.create_string_buffer(secret)
        data = cf.CFDataCreate(None, buffer, len(secret))
        owned.append(data)
        values[constant(sec, "kSecValueData")] = data
    keys = (pointer * len(values))(*values.keys())
    vals = (pointer * len(values))(*values.values())
    query = cf.CFDictionaryCreate(None, keys, vals, len(values), None, None)
    owned.append(query)
    result = pointer()
    try:
        status = (
            sec.SecItemDelete(query)
            if remove
            else (
                sec.SecItemCopyMatching(query, C.byref(result))
                if secret is None
                else sec.SecItemAdd(query, None)
            )
        )
        if status != 0:
            raise ModelError("model_keychain_unavailable")
        if remove:
            return b""
        if secret is not None:
            return secret
        assert result.value is not None
        owned.append(result.value)
        size = cf.CFDataGetLength(result)
        if size != 32:
            raise ModelError("model_keychain_unavailable")
        return C.string_at(cf.CFDataGetBytePtr(result), size)
    finally:
        for obj in reversed(owned):
            if obj:
                cf.CFRelease(obj)


def get(account: str) -> bytes:
    return _operation(account)


def put(account: str, value: bytes) -> None:
    _operation(account, value)


def delete(account: str) -> None:
    """Remove one exact owned account, e.g. a synthetic acceptance record."""
    _operation(account, remove=True)
