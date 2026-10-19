"""Strict OCR offsets and top-left pixel boxes; images never imply factual verification."""

import math
from typing import Any

from .document_contract import DocumentError


def validate_boxes(unit: dict[str, Any]) -> None:
    locator = unit["locator"]
    if not {"physical_page", "render_sha256", "width", "height", "boxes"} <= set(locator):
        raise DocumentError("ocr_locator_missing")
    import re

    if not isinstance(locator["render_sha256"], str) or not re.fullmatch(
        "[0-9a-f]{64}", locator["render_sha256"]
    ):
        raise DocumentError("ocr_render_hash")
    if (
        locator["width"] > 4096
        or locator["height"] > 4096
        or locator["width"] * locator["height"] > 16000000
    ):
        raise DocumentError("ocr_image_dimensions")
    boxes = locator["boxes"]
    if not isinstance(boxes, list) or len(boxes) > 1000:
        raise DocumentError("ocr_boxes_limit")
    previous = 0
    for box in boxes:
        if not isinstance(box, dict) or set(box) != {
            "start_char",
            "end_char",
            "bbox",
            "confidence",
        }:
            raise DocumentError("ocr_box_shape")
        start, end = box["start_char"], box["end_char"]
        if (
            type(start) is not int
            or type(end) is not int
            or not previous <= start < end <= len(unit["text"])
        ):
            raise DocumentError("ocr_offset")
        previous = end
        coords = box["bbox"]
        if (
            not isinstance(coords, list)
            or len(coords) != 4
            or any(type(n) not in (int, float) or not math.isfinite(n) for n in coords)
        ):
            raise DocumentError("ocr_bbox")
        if not (
            0 <= coords[0] < coords[2] <= locator["width"]
            and 0 <= coords[1] < coords[3] <= locator["height"]
        ):
            raise DocumentError("ocr_bbox_bounds")
        confidence = box["confidence"]
        if (
            type(confidence) not in (int, float)
            or not math.isfinite(confidence)
            or not 0 <= confidence <= 1
        ):
            raise DocumentError("ocr_confidence")
