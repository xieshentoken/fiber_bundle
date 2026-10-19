"""Bounded HTTPS transport and strict AES decoding for Weixin attachment recovery."""

import asyncio
import base64
import ipaddress
import re
import socket
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote, urlsplit

from .config import InvalidInput
from .weixin_events import MAX_BYTES

HOSTS = frozenset(
    {
        "novac2c.cdn.weixin.qq.com",
        "ilinkai.weixin.qq.com",
        "wx.qlogo.cn",
        "thirdwx.qlogo.cn",
        "res.wx.qq.com",
        "mmbiz.qpic.cn",
        "mmbiz.qlogo.cn",
    }
)
WIRE_LIMIT = MAX_BYTES + 16  # AES PKCS#7 padding, not extra plaintext allowance.


class DownloadError(InvalidInput):
    pass


@dataclass(frozen=True)
class Source:
    url: str = field(repr=False)
    key: bytes | None = field(repr=False)


def validate_url(url: object) -> str:
    if not isinstance(url, str) or not url or len(url) > 8192:
        raise DownloadError("invalid_media_url")
    try:
        parsed = urlsplit(url)
        valid = (
            parsed.scheme == "https"
            and parsed.hostname in HOSTS
            and parsed.port in (None, 443)
            and parsed.username is None
            and parsed.password is None
            and not parsed.fragment
            and all(32 < ord(c) < 127 for c in url)
            and "\\" not in url
        )
    except ValueError:
        valid = False
    if not valid:
        raise DownloadError("media_source_rejected")
    return url


def source(item: dict[str, Any]) -> Source:
    """Normalize only original platform media fields; no URL from filenames or text."""
    names = {2: "image_item", 3: "voice_item", 4: "file_item", 5: "video_item"}
    kind = item.get("type")
    if type(kind) is not int or kind not in names:
        raise DownloadError("unsupported_media_type")
    payload = item.get(names[kind])
    if not isinstance(payload, dict) or not isinstance(payload.get("media"), dict):
        raise DownloadError("media_descriptor_missing")
    media = payload["media"]
    param = media.get("encrypt_query_param")
    if param is not None:
        if not isinstance(param, str) or not param or len(param) > 4096:
            raise DownloadError("invalid_media_parameter")
        url = "https://novac2c.cdn.weixin.qq.com/c2c/download?encrypted_query_param=" + quote(
            param, safe=""
        )
    else:
        url = media.get("full_url")
    key = None
    try:
        if item.get("type") == 2 and payload.get("aeskey") is not None:
            value = payload["aeskey"]
            if not isinstance(value, str) or not re.fullmatch(r"[a-fA-F0-9]{32}", value):
                raise ValueError
            key = bytes.fromhex(value)
        elif media.get("aes_key") is not None:
            value = media["aes_key"]
            if not isinstance(value, str) or len(value) > 128:
                raise ValueError
            key = base64.b64decode(value, validate=True)
            if len(key) == 32:
                key = bytes.fromhex(key.decode("ascii"))
            if len(key) != 16:
                raise ValueError
    except (ValueError, UnicodeError):
        raise DownloadError("invalid_media_key") from None
    if param is not None and key is None:
        raise DownloadError("media_key_missing")
    return Source(validate_url(url), key)


def decode(raw: bytes, key: bytes | None) -> bytes:
    if not isinstance(raw, bytes) or not raw or len(raw) > WIRE_LIMIT:
        raise DownloadError("media_size_rejected")
    if key is not None:
        try:
            from cryptography.hazmat.primitives import padding
            from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

            decryptor = Cipher(algorithms.AES(key), modes.ECB()).decryptor()
            padded = decryptor.update(raw) + decryptor.finalize()
            unpadder = padding.PKCS7(128).unpadder()
            raw = unpadder.update(padded) + unpadder.finalize()
        except ImportError:
            raise DownloadError("weixin_dependencies_missing") from None
        except ValueError:
            raise DownloadError("media_decryption_failed") from None
    if not raw or len(raw) > MAX_BYTES:
        raise DownloadError("media_size_rejected")
    return raw


def public_address(address: str) -> bool:
    return ipaddress.ip_address(address).is_global


async def fetch(url: str) -> bytes:
    """One request, fresh credential-free session; no proxies, redirects or retries."""
    validate_url(url)
    try:
        import aiohttp
        from aiohttp.resolver import ThreadedResolver
    except ImportError:
        raise DownloadError("weixin_dependencies_missing") from None

    class PublicResolver(ThreadedResolver):
        async def resolve(
            self, host: str, port: int = 0, family: socket.AddressFamily = socket.AF_INET
        ) -> Any:
            records = await super().resolve(host, port, family)
            if not records or any(not public_address(r["host"]) for r in records):
                raise DownloadError("media_address_rejected")
            return records

    try:
        async with asyncio.timeout(30):
            async with aiohttp.ClientSession(
                connector=aiohttp.TCPConnector(
                    resolver=PublicResolver(), use_dns_cache=False, limit=1
                ),
                trust_env=False,
                cookie_jar=aiohttp.DummyCookieJar(),
                auto_decompress=False,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as session:
                async with session.get(
                    url, allow_redirects=False, headers={"Accept-Encoding": "identity"}
                ) as response:
                    if response.status != 200:
                        raise DownloadError("media_http_rejected")
                    if response.headers.get("Content-Encoding", "identity").lower() != "identity":
                        raise DownloadError("media_encoding_rejected")
                    if response.content_length is not None and response.content_length > WIRE_LIMIT:
                        raise DownloadError("media_size_rejected")
                    chunks = bytearray()
                    async for part in response.content.iter_chunked(65536):
                        if len(chunks) + len(part) > WIRE_LIMIT:
                            raise DownloadError("media_size_rejected")
                        chunks.extend(part)
                    if not chunks:
                        raise DownloadError("media_size_rejected")
                    return bytes(chunks)
    except DownloadError:
        raise
    except TimeoutError:
        raise DownloadError("media_download_timeout") from None
    except Exception:
        raise DownloadError("media_transport_failed") from None
