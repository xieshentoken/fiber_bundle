"""Bounded kernel argv inspection for one leased PID; never return its environment."""

import ctypes
import os
import struct
import sys
from pathlib import Path

from ..config import InvalidInput

MAX_BYTES = 262144


def darwin_arguments(raw: bytes) -> list[str]:
    if len(raw) < 8 or len(raw) > MAX_BYTES:
        raise ValueError("invalid process arguments")
    count = struct.unpack_from("=i", raw)[0]
    if not 1 <= count <= 128:
        raise ValueError("invalid argument count")
    offset = raw.index(b"\0", 4) + 1  # executable path, followed by alignment padding
    while offset < len(raw) and raw[offset] == 0:
        offset += 1
    values = []
    for _ in range(count):
        end = raw.index(b"\0", offset)
        values.append(os.fsdecode(raw[offset:end]))
        offset = end + 1
    return values


def arguments(pid: int) -> list[str]:
    if type(pid) is not int or pid <= 1:
        raise InvalidInput("channel_legacy_process_unverified")
    try:
        if sys.platform == "darwin":
            libc = ctypes.CDLL(None, use_errno=True)
            call = libc.sysctl
            call.argtypes = [
                ctypes.POINTER(ctypes.c_int),
                ctypes.c_uint,
                ctypes.c_void_p,
                ctypes.POINTER(ctypes.c_size_t),
                ctypes.c_void_p,
                ctypes.c_size_t,
            ]
            call.restype = ctypes.c_int
            mib = (ctypes.c_int * 3)(1, 49, pid)  # CTL_KERN / KERN_PROCARGS2
            buffer = ctypes.create_string_buffer(MAX_BYTES)
            size = ctypes.c_size_t(MAX_BYTES)
            try:
                if call(mib, 3, buffer, ctypes.byref(size), None, 0) != 0:
                    raise OSError(ctypes.get_errno(), "process arguments unavailable")
                return darwin_arguments(buffer.raw[: size.value])
            finally:
                ctypes.memset(buffer, 0, MAX_BYTES)
        if sys.platform.startswith("linux"):
            with (Path("/proc") / str(pid) / "cmdline").open("rb") as stream:
                raw = stream.read(MAX_BYTES + 1)
            if not raw.endswith(b"\0") or len(raw) > MAX_BYTES:
                raise ValueError("invalid process arguments")
            values = raw[:-1].split(b"\0")
            if not 1 <= len(values) <= 128:
                raise ValueError("invalid argument count")
            return [os.fsdecode(value) for value in values]
        raise ValueError("unsupported process inspection")
    except (OSError, ValueError, AttributeError):
        raise InvalidInput("channel_legacy_process_unverified") from None
