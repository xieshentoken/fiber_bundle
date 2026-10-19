"""macOS statfs64 local-volume check (Darwin sys/mount.h), without mounting anything."""

import ctypes
import os
import sys
from pathlib import Path

from .errors import RelocationError


class _StatFS(ctypes.Structure):
    _fields_ = [
        ("bsize", ctypes.c_uint32),
        ("iosize", ctypes.c_int32),
        ("blocks", ctypes.c_uint64),
        ("bfree", ctypes.c_uint64),
        ("bavail", ctypes.c_uint64),
        ("files", ctypes.c_uint64),
        ("ffree", ctypes.c_uint64),
        ("fsid", ctypes.c_int32 * 2),
        ("owner", ctypes.c_uint32),
        ("type", ctypes.c_uint32),
        ("flags", ctypes.c_uint32),
        ("subtype", ctypes.c_uint32),
        ("typename", ctypes.c_char * 16),
        ("mountpoint", ctypes.c_char * 1024),
        ("mountedfrom", ctypes.c_char * 1024),
        ("flags_ext", ctypes.c_uint32),
        ("reserved", ctypes.c_uint32 * 7),
    ]


def require_local(path: Path) -> None:
    if sys.platform != "darwin":
        raise RelocationError("relocation_platform_unsupported")
    result = _StatFS()
    libc = ctypes.CDLL(None, use_errno=True)
    call = getattr(libc, "statfs64", libc.statfs)
    call.argtypes = [ctypes.c_char_p, ctypes.POINTER(_StatFS)]
    call.restype = ctypes.c_int
    if call(os.fsencode(path), ctypes.byref(result)) != 0:
        raise OSError(ctypes.get_errno(), "local filesystem check failed")
    if not result.flags & 0x1000:  # MNT_LOCAL; statvfs f_flag omits this Darwin flag.
        raise RelocationError("relocation_local_folder_required")
