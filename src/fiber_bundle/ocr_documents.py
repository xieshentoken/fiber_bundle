"""Container-only canonical page rendering and explicit Tesseract candidate observations."""

import hashlib
import struct
import subprocess
from pathlib import Path
from typing import Any

from .document_contract import DocumentError
from .material_contract import attachment, result, segment, sha

PATHS = {
    "pdftoppm": "/usr/bin/pdftoppm",
    "tesseract": "/usr/bin/tesseract",
    "eng": "/usr/share/tesseract-ocr/5/tessdata/eng.traineddata",
    "chi_sim": "/usr/share/tesseract-ocr/5/tessdata/chi_sim.traineddata",
    "chi_tra": "/usr/share/tesseract-ocr/5/tessdata/chi_tra.traineddata",
    "worker": __file__,
}


def inventory() -> dict[str, str]:
    from .image_decoding import inventory as decoder_inventory

    return {
        **{
            name: hashlib.sha256(Path(path).read_bytes()).hexdigest()
            for name, path in PATHS.items()
        },
        **decoder_inventory(),
    }


def command(argv: list[str]) -> None:
    try:
        subprocess.run(
            argv,
            check=True,
            timeout=45,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env={
                "PATH": "/usr/bin:/bin",
                "HOME": "/tmp",
                "OMP_THREAD_LIMIT": "1",
                "LANG": "C.UTF-8",
            },
        )
    except (subprocess.SubprocessError, OSError) as exc:
        raise DocumentError("ocr_backend_failed") from exc


def dimensions(data: bytes) -> tuple[int, int]:
    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24 and data[12:16] == b"IHDR":
        width, height = struct.unpack(">II", data[16:24])
    elif data.startswith(b"\xff\xd8\xff"):
        index = 2
        width = height = 0
        while index + 4 <= len(data):
            if data[index] != 255:
                raise DocumentError("ocr_image_header")
            marker = data[index + 1]
            index += 2
            if marker in (0xD8, 0xD9):
                continue
            size = int.from_bytes(data[index : index + 2], "big")
            if size < 2 or index + size > len(data):
                raise DocumentError("ocr_image_header")
            if marker in (0xC0, 0xC1, 0xC2):
                height = int.from_bytes(data[index + 3 : index + 5], "big")
                width = int.from_bytes(data[index + 5 : index + 7], "big")
                break
            index += size
    else:
        raise DocumentError("ocr_image_type")
    if not 0 < width <= 4096 or not 0 < height <= 4096 or width * height > 16000000:
        raise DocumentError("ocr_image_dimensions")
    return width, height


def transcript(path: Path) -> tuple[str, list[dict[str, Any]]]:
    raw = path.read_bytes()
    if len(raw) > 4 * 1024 * 1024:
        raise DocumentError("ocr_transcript_limit")
    lines = raw.decode("utf-8").splitlines()
    if (
        not lines
        or lines[0]
        != (
            "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\t"
            "left\ttop\twidth\theight\tconf\ttext"
        )
        or len(lines) > 10000
    ):
        raise DocumentError("ocr_tsv_shape")
    text = ""
    boxes: list[dict[str, Any]] = []
    previous = None
    for line in lines[1:]:
        parts = line.split("\t", 11)
        if len(parts) != 12:
            raise DocumentError("ocr_tsv_shape")
        if parts[0] != "5" or not parts[11]:
            continue
        if len(boxes) >= 1000:
            raise DocumentError("ocr_words_limit")
        group = tuple(parts[1:5])
        if text:
            text += " " if group == previous else "\n"
        previous = group
        start = len(text)
        text += parts[11]
        left, top, width, height = (int(x) for x in parts[6:10])
        boxes.append(
            {
                "start_char": start,
                "end_char": len(text),
                "bbox": [left, top, left + width, top + height],
                "confidence": float(parts[10]) / 100,
            }
        )
    return text, boxes


def extract(
    source: bytes, format: str, runtime: dict[str, Any], input_path: Path, work: Path
) -> dict[str, Any]:
    if inventory() != runtime["runtime_files_sha256"]:
        raise DocumentError("ocr_runtime_drift")
    native = None
    if format == "pdf":
        from .pdf_documents import extract as native_extract

        native = native_extract(source)
        count = native["page_count"]
    elif format == "image":
        from .image_decoding import MANIFEST, frames

        if MANIFEST.exists():
            images = frames(source)
        else:
            dimensions(source)
            images = iter([source])
        count = 100
    else:
        raise DocumentError("ocr_format")
    units = []
    assets = []
    seen = set()
    for number in range(1, count + 1):
        image = work / f"page-{number}.png"
        if native:
            command(
                [
                    "/usr/bin/pdftoppm",
                    "-f",
                    str(number),
                    "-l",
                    str(number),
                    "-singlefile",
                    "-scale-to",
                    "2400",
                    "-png",
                    str(input_path),
                    str(image.with_suffix("")),
                ]
            )
            data = image.read_bytes()
        else:
            frame = next(images, None)
            if frame is None:
                break
            data = frame
            image.write_bytes(data)
        width, height = dimensions(data)
        target = work / f"page-{number}"
        command(
            [
                "/usr/bin/tesseract",
                str(image),
                str(target),
                "-l",
                runtime["languages"],
                "--psm",
                "3",
                "tsv",
            ]
        )
        text, boxes = transcript(target.with_suffix(".tsv"))
        locator = {
            "physical_page": number,
            "render_sha256": sha(data),
            "width": width,
            "height": height,
            "boxes": boxes,
        }
        if native:
            locator.update(
                pdf_page_label=native["pages"][number - 1]["pdf_page_label"],
                native_text_sha256=native["pages"][number - 1]["text_sha256"],
            )
        units.append(
            segment(
                text,
                locator,
                ["ocr_review", "visual_review"] + (["missing_text"] if not text else []),
            )
        )
        item = attachment(
            data, "image/png" if data.startswith(b"\x89PNG") else "image/jpeg", "page_render"
        )
        if item["sha256"] not in seen:
            assets.append(item)
            seen.add(item["sha256"])
    return result(
        source, "pdf-ocr" if native else "image", units, attachments=assets, runtime=runtime
    )
