"""Container-only full raster decoding. No resizing, partial frames, or host libraries."""

import hashlib
import io
import json
import warnings
from collections.abc import Iterator
from pathlib import Path

from .document_contract import DocumentError

MANIFEST = Path("/opt/fiber/decoder-files.json")
FORMATS = {"PNG", "JPEG", "WEBP", "TIFF", "BMP", "GIF", "HEIF"}


def inventory() -> dict[str, str]:
    if not MANIFEST.exists():
        return {}
    return {
        "decoder": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "decoder_packages": hashlib.sha256(MANIFEST.read_bytes()).hexdigest(),
    }


def verify_dependencies() -> None:
    files = json.loads(MANIFEST.read_bytes())
    if not isinstance(files, dict) or not files:
        raise DocumentError("image_decoder_runtime")
    for relative, expected in files.items():
        path = Path("/opt/fiber/decoder") / relative
        if path.resolve() != path or not path.is_file() or path.is_symlink():
            raise DocumentError("image_decoder_drift")
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise DocumentError("image_decoder_drift")


def frames(source: bytes) -> Iterator[bytes]:
    """Validate all frame sizes before any decode, then yield full physical frames."""
    verify_dependencies()
    import pillow_heif  # type: ignore[import-not-found]
    from PIL import Image  # type: ignore[import-not-found]

    if Image.__version__ != "12.3.0" or pillow_heif.__version__ != "1.8.0":
        raise DocumentError("image_decoder_version")
    pillow_heif.register_heif_opener()
    Image.MAX_IMAGE_PIXELS = 16_000_000
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            with Image.open(io.BytesIO(source)) as image:
                format = image.format
                count = getattr(image, "n_frames", 1)
                if format not in FORMATS or not 1 <= count <= 100:
                    raise DocumentError("image_decoder_format_or_frames")
                pixels = 0
                sizes = []
                for ordinal in range(count):
                    image.seek(ordinal)
                    width, height = image.size
                    if not 0 < width <= 4096 or not 0 < height <= 4096:
                        raise DocumentError("image_decoder_dimensions")
                    pixels += width * height
                    if pixels > 16_000_000:
                        raise DocumentError("image_decoder_total_pixels")
                    sizes.append(image.size)
                for ordinal in range(count):
                    image.seek(ordinal)
                    image.load()
                    if image.size != sizes[ordinal]:
                        raise DocumentError("image_decoder_frame_changed")
                    if count == 1 and format in {"PNG", "JPEG"}:
                        yield source
                    else:
                        output = io.BytesIO()
                        image.convert("RGBA").save(output, format="PNG")
                        data = output.getvalue()
                        if len(data) > 8 * 1024 * 1024:
                            raise DocumentError("image_decoder_output_limit")
                        yield data
    except (OSError, ValueError, Warning) as exc:
        raise DocumentError("image_decoder_failed") from exc
