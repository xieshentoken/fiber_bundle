"""One metadata extraction only, using the existing public HTTPS boundary."""

import importlib
from pathlib import Path

from .media_files import canonical, decode
from .media_link_capture import download_options, public_sockets
from .media_link_contract import source_url
from .media_link_failure import AcquisitionBlocked
from .media_link_metadata import inspect
from .media_processing_files import read, write


def capture(inputs: Path, outputs: Path) -> None:
    spec = decode(read(inputs, "request.json", 4096))
    if (
        set(spec) != {"kind", "url", "mode"}
        or spec["kind"] != "metadata_preflight"
        or spec["url"] != source_url(spec["url"])
        or spec["mode"] not in ("audio_only", "full_evidence")
        or {p.name for p in inputs.iterdir()} != {"request.json"}
        or any(outputs.iterdir())
    ):
        raise AcquisitionBlocked("input_invalid")
    public_sockets()
    options = download_options(spec["mode"])
    options["skip_download"] = True
    try:
        module = importlib.import_module("yt_dlp")
    except ImportError as error:
        raise AcquisitionBlocked("dependency_unavailable") from error
    try:
        with module.YoutubeDL(options) as downloader:
            info = downloader.extract_info(spec["url"], download=False)
        result = inspect(info, spec)
    except AcquisitionBlocked:
        raise
    except Exception as error:
        raise AcquisitionBlocked("metadata_failed") from error
    write(outputs, "result.json", canonical(result))
