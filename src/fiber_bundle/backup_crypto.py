"""Fixed, bounded authenticated records; no plaintext temporary archive or stored password."""

import hashlib
import hmac
import os
from typing import TYPE_CHECKING, BinaryIO

from .config import IntegrityError, InvalidInput

MAGIC = b"FIBERB1\n"
MAX_RECORD = 16 * 1024 * 1024
CHUNK = 1024 * 1024
if TYPE_CHECKING:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def _cipher(password: str, salt: bytes) -> "AESGCM":
    if not isinstance(password, str) or not 16 <= len(password.encode()) <= 1024:
        raise InvalidInput("backup_password_length")
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
    except ImportError:
        raise InvalidInput("backup_crypto_dependency_missing") from None
    return AESGCM(Scrypt(salt=salt, length=32, n=32768, r=8, p=1).derive(password.encode()))


class Records:
    def __init__(
        self, stream: BinaryIO, password: str, *, write: bool, master: bytes | None = None
    ):
        self.stream, self.index, self.write = stream, 0, write
        magic = MAGIC if master is None else b"FIBERB2\n"
        self.header = magic + os.urandom(20) if write else stream.read(len(magic) + 20)
        if len(self.header) != len(magic) + 20 or not self.header.startswith(magic):
            raise IntegrityError("backup_format_invalid")
        if master is None:
            self.cipher = _cipher(password, self.header[len(magic) : len(magic) + 16])
        else:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM

            if len(master) != 32:
                raise InvalidInput("backup_key_invalid")
            self.cipher = AESGCM(hmac.digest(master, self.header, hashlib.sha256))
        if write:
            stream.write(self.header)

    def put(self, value: bytes) -> None:
        if not self.write or not 1 <= len(value) <= MAX_RECORD:
            raise InvalidInput("backup_record_limit")
        number = self.index.to_bytes(8, "big")
        size = (len(value) + 16).to_bytes(4, "big")
        encrypted = self.cipher.encrypt(
            self.header[-4:] + number, value, self.header + number + size
        )
        self.stream.write(size + encrypted)
        self.index += 1

    def get(self) -> bytes:
        from cryptography.exceptions import InvalidTag

        size = self.stream.read(4)
        if len(size) != 4 or not 17 <= int.from_bytes(size, "big") <= MAX_RECORD + 16:
            raise IntegrityError("backup_record_invalid_or_truncated")
        wire = self.stream.read(int.from_bytes(size, "big"))
        if len(wire) != int.from_bytes(size, "big"):
            raise IntegrityError("backup_record_invalid_or_truncated")
        number = self.index.to_bytes(8, "big")
        try:
            value = self.cipher.decrypt(
                self.header[-4:] + number, wire, self.header + number + size
            )
        except InvalidTag:
            raise IntegrityError("backup_authentication_failed") from None
        self.index += 1
        return bytes(value)

    def finish(self, digest: str) -> None:
        end = b"END:" + digest.encode("ascii")
        if self.write:
            self.put(end)
        elif self.get() != end or self.stream.read(1):
            raise IntegrityError("backup_final_record_invalid")
