"""Fixed public downloader: global HTTPS sockets only, no browser/session export or live feeds."""

import hashlib
import importlib
import ipaddress
import json
import math
import socket
import subprocess
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from .media_files import canonical, decode
from .media_link_contract import source_url
from .media_link_failure import AcquisitionBlocked
from .media_link_metadata import checked_info
from .media_link_parts import leaf, parts, reported_size
from .media_processing_files import read, write


def converted_duration(path: Path, audio: bool, *, maximum_ms: int = 60000) -> int:
    """Measure the complete conversion without padding, trimming or platform-time tolerance."""
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-protocol_whitelist",
            "file",
            "-format_whitelist",
            "wav" if audio else "mov",
            "-f",
            "wav" if audio else "mov",
            "-i",
            str(path),
            "-show_entries",
            "format=duration",
            "-of",
            "json",
        ],
        check=True,
        timeout=20,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    if len(result.stdout) > 1024:
        raise AcquisitionBlocked("output_invalid")
    try:
        raw = json.loads(result.stdout)["format"]["duration"]
        if not isinstance(raw, str) or len(raw) > 32:
            raise ValueError()
        seconds = Decimal(raw)
        if not seconds.is_finite() or not 0 < seconds <= maximum_ms / 1000:
            raise ValueError()
        milliseconds = int((seconds * 1000).quantize(Decimal(1), rounding=ROUND_HALF_UP))
        if not 1 <= milliseconds <= maximum_ms:
            raise ValueError()
    except (ValueError, KeyError, TypeError, InvalidOperation, OverflowError) as error:
        raise AcquisitionBlocked("output_invalid") from error
    return milliseconds


def public_sockets() -> None:
    resolve = socket.getaddrinfo
    connect = socket.socket.connect

    def addresses(host: Any, port: Any, *args: Any, **kw: Any) -> Any:
        if str(port) != "443":
            raise OSError("public HTTPS only")
        result = resolve(host, port, *args, **kw)
        if not result or any(not ipaddress.ip_address(r[4][0]).is_global for r in result):
            raise OSError("public addresses only")
        return result

    def pinned(s: socket.socket, address: Any) -> Any:
        if s.family not in (socket.AF_INET, socket.AF_INET6) or address[1] != 443:
            raise OSError("public HTTPS only")
        target = addresses(address[0], address[1], s.family, s.type)[0][4]
        return connect(s, target)

    socket.getaddrinfo = addresses
    transport: Any = socket.socket
    transport.connect = pinned

    def pinned_ex(s: socket.socket, address: Any) -> int:
        try:
            pinned(s, address)
        except OSError as error:
            return error.errno or 1
        return 0

    transport.connect_ex = pinned_ex


def bounded_progress(
    value: dict[str, Any], *, consumed: int = 0, maximum: int = 8 * 1024 * 1024
) -> None:
    if any(
        value.get(key, 0) + consumed > maximum
        for key in ("downloaded_bytes", "total_bytes", "total_bytes_estimate")
        if value.get(key) is not None
    ):
        raise AcquisitionBlocked("download_size_exceeded")


def download_options(mode: str, *, maximum: int = 8 * 1024 * 1024) -> dict[str, Any]:
    options = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "cookiefile": None,
        "cachedir": False,
        "proxy": "",
        "socket_timeout": 15,
        "retries": 0,
        "fragment_retries": 0,
        "extractor_retries": 0,
        "file_access_retries": 0,
        "http_chunk_size": 0,
        "progress_hooks": [bounded_progress],
        "skip_download": False,
        "extract_flat": False,
        "writethumbnail": False,
        "writeinfojson": False,
        "getcomments": False,
        "writesubtitles": False,
        "writeautomaticsub": False,
        "outtmpl": "/tmp/download.%(ext)s",
        "max_filesize": maximum,
        "format": "bestaudio[protocol=https]/best[protocol=https]"
        if mode == "audio_only"
        else (
            "best[ext=mp4][height<=1080][protocol=https]/"
            "bestvideo[ext=mp4][height<=720][width<=1920][protocol=https]+"
            "bestaudio[ext=m4a][protocol=https]"
        ),
    }
    if mode == "full_evidence":
        options.update(fixup="never", postprocessors=[])
    return options


def capture(inputs: Path, outputs: Path, *, long_video: bool = False) -> None:
    maximum = 16 * 1024 * 1024 if long_video else 8 * 1024 * 1024
    maximum_duration = 7_200_000 if long_video else 60000
    spec = decode(read(inputs, "request.json", 4096))
    if (
        set(spec) != {"url", "mode", "declared_duration_ms"}
        or spec["url"] != source_url(spec["url"])
        or (
            spec["mode"] not in ("audio_only", "full_evidence")
            or type(spec["declared_duration_ms"]) is not int
            or not 1 <= spec["declared_duration_ms"] <= maximum_duration
        )
        or {p.name for p in inputs.iterdir()} != {"request.json"}
        or any(outputs.iterdir())
    ):
        raise AcquisitionBlocked("input_invalid")
    public_sockets()
    try:
        module = importlib.import_module("yt_dlp")
    except ImportError as e:
        raise AcquisitionBlocked("dependency_unavailable") from e
    options = download_options(spec["mode"], maximum=maximum)
    consumed = 0

    def progress(value: dict[str, Any]) -> None:
        bounded_progress(value, consumed=consumed, maximum=maximum)

    options["progress_hooks"] = [progress]
    phase = "metadata_failed"
    try:
        with module.YoutubeDL(options) as ydl:
            info = checked_info(
                ydl.extract_info(spec["url"], download=False), spec["url"], mode=spec["mode"]
            )
            seconds = info.get("duration")
            if (
                not isinstance(seconds, (int, float))
                or isinstance(seconds, bool)
                or not math.isfinite(seconds)
                or round(seconds * 1000) != spec["declared_duration_ms"]
            ):
                raise AcquisitionBlocked("duration_mismatch")
            size, _ = reported_size(info, spec["mode"])
            if size is None or not 0 < size <= maximum:
                raise AcquisitionBlocked("size_unverified")
            # Each selected direct file is downloaded once from this exact metadata.
            # The combined result never goes through yt-dlp's automatic merge route.
            phase = "download_failed"
            filenames = []
            selected = parts(info, spec["mode"])
            for p in selected:
                download = leaf(info, p) if len(selected) == 2 else info
                ydl.process_info(download)
                filename = Path(ydl.prepare_filename(download))
                if (
                    filename.parent != Path("/tmp")
                    or filename in filenames
                    or filename.is_symlink()
                    or not filename.is_file()
                    or filename.stat().st_nlink != 1
                    or not 0 < filename.stat().st_size <= maximum - consumed
                ):
                    raise AcquisitionBlocked("output_invalid")
                consumed += filename.stat().st_size
                filenames.append(filename)
        audio = spec["mode"] == "audio_only"
        out = Path("/tmp") / ("source.wav" if audio else "source.mp4")
        args = [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-xerror",
            "-threads",
            "1",
        ]
        for filename in filenames:
            args += ["-protocol_whitelist", "file", "-i", str(filename)]
        if len(filenames) == 2:
            args += ["-map", "0:v:0", "-map", "1:a:0"]
        args += (
            ["-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", "-f", "wav"]
            if audio
            else [
                "-vf",
                "scale=640:-2",
                "-c:v",
                "mpeg4",
                "-q:v",
                "5",
                "-c:a",
                "aac",
                "-ar",
                "16000",
                "-ac",
                "1",
                "-f",
                "mp4",
            ]
        )
        phase = "conversion_failed"
        subprocess.run(
            [*args, str(out)],
            check=True,
            timeout=900 if long_video else 60,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if not 0 < out.stat().st_size <= maximum:
            raise AcquisitionBlocked("output_invalid")
        phase = "output_invalid"
        measured = converted_duration(out, audio, maximum_ms=maximum_duration)
        data = out.read_bytes()
        write(outputs, "source", data, maximum=maximum)
        write(
            outputs,
            "result.json",
            canonical(
                {
                    "schema": "fiber-media-link-capture/v3"
                    if long_video
                    else "fiber-media-link-capture/v2",
                    "url": spec["url"],
                    "mode": spec["mode"],
                    "declared_duration_ms": spec["declared_duration_ms"],
                    "converted_duration_ms": measured,
                    "source_sha256": hashlib.sha256(data).hexdigest(),
                    "source_bytes": len(data),
                    "media_type": "audio/wav" if audio else "video/mp4",
                    "access_state": "public_acquired",
                    "source_coverage_verified": False,
                    "cookies_exported": False,
                }
            ),
        )
    except AcquisitionBlocked:
        raise
    except Exception as e:
        # Logs never echo remote errors, URLs, tokens or response bodies.
        raise AcquisitionBlocked(phase) from e
