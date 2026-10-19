"""Synthetic PDF bytes and candidate JSON; no private documents or external library required."""

import hashlib
import re
import unicodedata


def pdf_bytes(
    texts=("Synthetic source evidence has enough ordinary alphabetic text for extraction.",),
    *,
    image=False,
    font="Helvetica",
):
    objects = []
    count = len(texts)
    page_ids = [5 + n * 2 for n in range(count)]
    objects.append(b"<< /Type /Catalog /Pages 2 0 R /PageLabels << /Nums [0 << /S /r >>] >> >>")
    objects.append(
        (
            f"<< /Type /Pages /Count {count} /Kids "
            + "["
            + " ".join(f"{n} 0 R" for n in page_ids)
            + "] >>"
        ).encode()
    )
    objects.append(f"<< /Type /Font /Subtype /Type1 /BaseFont /{font} >>".encode())
    objects.append(
        b"<< /Type /XObject /Subtype /Image /Width 1 /Height 1 "
        b"/ColorSpace /DeviceRGB /BitsPerComponent 8 /Length 3 >>\n"
        b"stream\n\xff\x00\x00\nendstream"
    )
    for index, text in enumerate(texts):
        content_id = page_ids[index] + 1
        image_resource = "/XObject << /Im1 4 0 R >>" if image else ""
        objects.append(
            (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                f"/Resources << /Font << /F1 3 0 R >> {image_resource} >> "
                f"/Contents {content_id} 0 R >>"
            ).encode()
        )
        escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        stream = (f"BT /F1 12 Tf 72 700 Td ({escaped}) Tj ET").encode("ascii") if text else b""
        if image:
            stream += b"\nq 100 0 0 100 72 500 cm /Im1 Do Q"
        objects.append(f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream")
    result = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for index, obj in enumerate(objects, 1):
        offsets.append(len(result))
        result.extend(f"{index} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref = len(result)
    result.extend(f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]:
        result.extend(f"{offset:010d} 00000 n \n".encode())
    result.extend(
        f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    return bytes(result)


def candidate(
    source,
    text="Synthetic original text with enough alphabetic characters for reliable extraction.",
):
    normalized = re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text)).strip()
    status = (
        "missing"
        if not normalized
        else "low"
        if sum(c.isalnum() for c in normalized) < 40
        else "usable"
    )
    ocr = (
        []
        if status == "usable"
        else ["native_text_missing" if status == "missing" else "native_text_below_threshold"]
    )
    review = [] if status == "usable" else ["native_text_not_usable"]
    if any(c.isnumeric() for c in text):
        review.append("numeric_or_unit_review")
    return dict(
        schema=1,
        format="pdf",
        source_sha256=hashlib.sha256(source).hexdigest(),
        source_bytes=len(source),
        parser={"id": "fake-expert-native-page", "pypdf": "6.10.0"},
        page_count=1,
        pages=[
            dict(
                physical_page=1,
                pdf_page_label="i",
                printed_page_label_candidate=None,
                dimensions={"width": 612.0, "height": 792.0},
                coordinate_status="unavailable",
                text=text,
                text_sha256=hashlib.sha256(text.encode()).hexdigest(),
                normalized_sha256=hashlib.sha256(normalized.encode()).hexdigest(),
                native_status=status,
                ocr_reasons=ocr,
                review_reasons=sorted(review),
            )
        ],
        pages_needing_ocr=[1] if ocr else [],
        pages_needing_review=[1] if review else [],
        publication_ready=False,
        knowledge_verified=False,
        coverage={
            "native_text": "extracted",
            "images_tables_formulas": "not_extracted",
            "ocr": "not_run",
            "visual_review": "not_run",
        },
    )
