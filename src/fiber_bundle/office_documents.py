"""Offline OOXML source extraction, preserving stored values, formulas and locators.

No Office execution, conversion, links, macros or formula evaluation. ZIP/XML input
is bounded before parsing; unknown/encrypted/legacy types fail closed.
"""

import io
import posixpath
import re
import xml.etree.ElementTree as ET
import zipfile
from typing import Any

from .document_contract import MAX_SOURCE_BYTES, DocumentError
from .material_contract import attachment, result, segment

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
S = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"


class Package:
    def __init__(self, source: bytes):
        if not 0 < len(source) <= min(MAX_SOURCE_BYTES, 8 * 1024 * 1024):
            raise DocumentError("office_source_limit")
        try:
            self.zip = zipfile.ZipFile(io.BytesIO(source))
            members = self.zip.infolist()
            names = [m.filename for m in members]
            if (
                len(names) > 2000
                or len(set(names)) != len(names)
                or sum(m.file_size for m in members) > 32 * 1024 * 1024
            ):
                raise DocumentError("office_zip_limit")
            for m in members:
                if (
                    m.flag_bits & 1
                    or m.file_size > 8 * 1024 * 1024
                    or "\\" in m.filename
                    or any(p in {".", ".."} for p in m.filename.split("/"))
                    or m.filename.startswith("/")
                    or "vbaproject" in m.filename.lower()
                ):
                    raise DocumentError("office_package_scope")
            self.names = set(names)
            self.complete = True
        except (zipfile.BadZipFile, OSError) as exc:
            raise DocumentError("office_zip_invalid") from exc

    def xml(self, name: str) -> ET.Element:
        try:
            raw = self.zip.read(name)
            # OOXML XML is read as UTF-8; do not let UTF-16 bypass entity rejection.
            raw.decode("utf-8-sig")
            if b"\x00" in raw or b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
                raise DocumentError("office_xml_entities")
            return ET.fromstring(raw)
        except (KeyError, UnicodeError, ET.ParseError, zipfile.BadZipFile) as exc:
            raise DocumentError("office_xml_invalid") from exc

    def relations(self, name: str, base: str) -> dict[str, str]:
        out = {}
        for rel in self.xml(name):
            if rel.get("TargetMode") == "External":
                continue
            target = rel.get("Target", "")
            if ":" in target or "\\" in target:
                raise DocumentError("office_relationship")
            joined = target.lstrip("/") if target.startswith("/") else posixpath.join(base, target)
            if any(p in {".", ".."} for p in joined.split("/")):
                raise DocumentError("office_relationship")
            if rel.get("Id") in out:
                raise DocumentError("office_relationship_duplicate")
            out[rel.get("Id", "")] = joined
        return out

    def images(self) -> tuple[list[dict[str, Any]], bool]:
        images = []
        complete = True
        hashes = set()
        for name in sorted(self.names):
            if "/media/" not in name or name.endswith("/"):
                continue
            raw = self.zip.read(name)
            mime = (
                "image/png"
                if raw.startswith(b"\x89PNG\r\n\x1a\n")
                else "image/jpeg"
                if raw.startswith(b"\xff\xd8\xff")
                else None
            )
            if mime is None:
                complete = False
                continue
            item = attachment(raw, mime, "embedded")
            if item["sha256"] not in hashes:
                images.append(item)
                hashes.add(item["sha256"])
        return images, complete


def word_text(node: ET.Element) -> str:
    return "".join(
        n.text or ""
        if n.tag == W + "t"
        or n.tag == "{http://schemas.openxmlformats.org/officeDocument/2006/math}t"
        else "\n"
        if n.tag == W + "br"
        else "\t"
        if n.tag == W + "tab"
        else ""
        for n in node.iter()
    )


def docx(package: Package) -> list[dict[str, Any]]:
    body = package.xml("word/document.xml").find(W + "body")
    if body is None:
        raise DocumentError("docx_body_missing")
    units: list[dict[str, Any]] = []
    paragraph = table = 0
    for node in body:
        if node.tag == W + "p":
            paragraph += 1
            text = word_text(node)
            units.append(
                segment(
                    text,
                    {"paragraph": paragraph},
                    ["formula_review"]
                    if any(n.tag.endswith("}oMath") for n in node.iter())
                    else [],
                )
            )
        elif node.tag == W + "tbl":
            table += 1
            for row, tr in enumerate(node.findall(W + "tr"), 1):
                for col, tc in enumerate(tr.findall(W + "tc"), 1):
                    text = "\n".join(word_text(p) for p in tc.findall(W + "p"))
                    units.append(
                        segment(text, {"table": table, "row": row, "column": col}, ["table_review"])
                    )
        elif node.tag != W + "sectPr":
            package.complete = False
        if any(n.tag in {W + "altChunk", W + "sdt", W + "del", W + "ins"} for n in node.iter()):
            package.complete = False
        if node.tag == W + "tbl" and any(len(list(n.iter(W + "tbl"))) > 1 for n in [node]):
            package.complete = False
    for name in sorted(package.names):
        if re.fullmatch(r"word/(header[0-9]+|footer[0-9]+|footnotes|endnotes|comments)\.xml", name):
            for number, node in enumerate(package.xml(name).iter(W + "p"), 1):
                units.append(
                    segment(
                        word_text(node),
                        {"source_part": name, "paragraph": number},
                        ["visual_review"],
                    )
                )
    return units


def xlsx(package: Package) -> list[dict[str, Any]]:
    shared = []
    if "xl/sharedStrings.xml" in package.names:
        shared = [
            "".join(n.text or "" for n in si.iter(S + "t"))
            for si in package.xml("xl/sharedStrings.xml")
        ]
    relations = package.relations("xl/_rels/workbook.xml.rels", "xl")
    units: list[dict[str, Any]] = []
    for sheet in package.xml("xl/workbook.xml").iter(S + "sheet"):
        name, rid = sheet.get("name"), sheet.get(R + "id")
        if not name or rid not in relations:
            raise DocumentError("xlsx_sheet_missing")
        data = package.xml(relations[rid])
        merged = [n.get("ref", "") for n in data.iter(S + "mergeCell")]
        if any(
            n.tag in {S + "drawing", S + "legacyDrawing", S + "oleObjects"} for n in data.iter()
        ):
            package.complete = False
        for cell in data.iter(S + "c"):
            address, kind = cell.get("r"), cell.get("t", "n")
            if not address or not re.fullmatch("[A-Z]+[1-9][0-9]*", address):
                raise DocumentError("xlsx_cell_locator")
            stored = cell.find(S + "v")
            text = stored.text or "" if stored is not None else ""
            if kind == "s":
                if not text.isdigit() or int(text) >= len(shared):
                    raise DocumentError("xlsx_shared_string")
                text = shared[int(text)]
            elif kind == "inlineStr":
                text = "".join(n.text or "" for n in cell.iter(S + "t"))
            formula = cell.find(S + "f")
            if formula is not None and formula.attrib:
                package.complete = (
                    False  # shared/array formula expansion is deliberately not invented.
                )
            units.append(
                segment(
                    text,
                    {
                        "sheet": name,
                        "cell": address,
                        "cell_type": kind,
                        "formula": None if formula is None else formula.text or "",
                        "merged_ranges": merged,
                    },
                    ["table_review"] + (["formula_review"] if formula is not None else []),
                )
            )
    return units


def pptx(package: Package) -> list[dict[str, Any]]:
    relations = package.relations("ppt/_rels/presentation.xml.rels", "ppt")
    units: list[dict[str, Any]] = []
    for number, reference in enumerate(package.xml("ppt/presentation.xml").iter(P + "sldId"), 1):
        rid = reference.get(R + "id")
        if rid not in relations:
            raise DocumentError("pptx_slide_missing")
        slide = package.xml(relations[rid])
        table_nodes = {id(p) for tc in slide.iter(A + "tc") for p in tc.iter(A + "p")}
        before = len(units)
        for paragraph, node in enumerate(slide.iter(A + "p"), 1):
            if id(node) not in table_nodes:
                text = "".join(n.text or "" for n in node.iter(A + "t"))
                units.append(
                    segment(text, {"slide": number, "paragraph": paragraph}, ["visual_review"])
                )
        for table, tbl in enumerate(slide.iter(A + "tbl"), 1):
            for row, tr in enumerate(tbl.findall(A + "tr"), 1):
                for column, tc in enumerate(tr.findall(A + "tc"), 1):
                    text = "\n".join(
                        "".join(n.text or "" for n in p.iter(A + "t")) for p in tc.iter(A + "p")
                    )
                    units.append(
                        segment(
                            text,
                            {"slide": number, "table": table, "row": row, "column": column},
                            ["table_review", "visual_review"],
                        )
                    )
        if len(units) == before:
            units.append(segment("", {"slide": number}, ["visual_review", "missing_text"]))
        if any(n.tag.endswith("}oleObj") or n.tag.endswith("}chart") for n in slide.iter()):
            package.complete = False
    for name in sorted(package.names):
        if re.fullmatch(r"ppt/notesSlides/notesSlide[0-9]+\.xml", name):
            for paragraph, node in enumerate(package.xml(name).iter(A + "p"), 1):
                units.append(
                    segment(
                        "".join(n.text or "" for n in node.iter(A + "t")),
                        {"source_part": name, "paragraph": paragraph},
                        ["visual_review"],
                    )
                )
    return units


def extract(source: bytes, format: str) -> dict[str, Any]:
    package = Package(source)
    try:
        units = {"docx": docx, "xlsx": xlsx, "pptx": pptx}[format](package)
        images, complete = package.images()
        complete = (
            complete
            and package.complete
            and not any(
                "/embeddings/" in n or "/charts/" in n or "vbaProject" in n for n in package.names
            )
        )
        for name in sorted(package.names):
            if not name.endswith(".rels"):
                continue
            for rel in package.xml(name):
                if rel.get("TargetMode") == "External":
                    target = rel.get("Target", "")
                    units.append(
                        segment(
                            target,
                            {"source_part": name, "external_link": target},
                            ["visual_review"],
                        )
                    )
                    if rel.get("Type", "").endswith("/image"):
                        complete = False
        return result(source, format, units, attachments=images, complete=complete)
    except (KeyError, ValueError, RuntimeError) as exc:
        if isinstance(exc, DocumentError):
            raise
        raise DocumentError("office_extract_failed") from exc
    finally:
        package.zip.close()
