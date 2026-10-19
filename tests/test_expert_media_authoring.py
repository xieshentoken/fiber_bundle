"""Trusted figure links and actual native-PDF crops; no OCR or model inference."""

import json
import os

import pytest
from document_support import pdf_bytes
from test_expert_authoring import explicit_progression_only as explicit_progression_only
from test_expert_authoring import proposal

from fiber_bundle import database, expert_authoring, expert_proposals
from fiber_bundle.config import InvalidInput
from fiber_bundle.objects import digest

pytest_plugins = ["test_source_imports", "test_execution"]


def native_visual_pdf():
    import re

    raw = pdf_bytes(("page one", "page two"), image=True)
    objects = [body for _, body in re.findall(rb"(\d+) 0 obj\n(.*?)\nendobj", raw, re.S)]
    chinese = "聚合物网络示意图，数据仍须审核"
    mapping = "\n".join(f"<{ord(char):04X}> <{ord(char):04X}>" for char in sorted(set(chinese)))
    cmap = (
        "/CIDInit /ProcSet findresource begin 12 dict begin begincmap\n"
        "/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def\n"
        "/CMapName /Adobe-Identity-UCS def /CMapType 2 def\n"
        "1 begincodespacerange <0000> <FFFF> endcodespacerange\n"
        f"{len(set(chinese))} beginbfchar\n{mapping}\nendbfchar\n"
        "endcmap CMapName currentdict /CMap defineresource pop end end"
    ).encode()
    objects.extend(
        [
            (
                b"<< /Type /Font /Subtype /Type0 /BaseFont /STSong-Light "
                b"/Encoding /UniGB-UCS2-H /DescendantFonts [11 0 R] /ToUnicode 10 0 R >>"
            ),
            f"<< /Length {len(cmap)} >>\nstream\n".encode() + cmap + b"\nendstream",
            (
                b"<< /Type /Font /Subtype /CIDFontType0 /BaseFont /STSong-Light "
                b"/CIDSystemInfo << /Registry (Adobe) /Ordering (GB1) /Supplement 4 >> >>"
            ),
        ]
    )
    for number in (1, 2):
        label = (
            "Figure 1. Polymer network diagram" if number == 1 else "Table 1. Material conditions"
        )
        stream = (
            f"BT /F1 12 Tf 72 700 Td ({number} Technical reference) Tj ET\n"
            "BT /F1 12 Tf 72 670 Td (A polymer network contains connected "
            "chains under source conditions.) Tj ET\n"
            "q 100 0 0 100 72 500 cm /Im1 Do Q\n"
            f"BT /F1 12 Tf 72 480 Td ({label}) Tj ET\n"
            f"BT /F2 12 Tf 72 460 Td <{chinese.encode('utf-16-be').hex()}> Tj ET\n"
        ).encode()
        objects[3 + number * 2] = (
            f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream"
        )
        page = 2 + number * 2
        objects[page] = objects[page].replace(b"/F1 3 0 R", b"/F1 3 0 R /F2 9 0 R")
    result = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for number, obj in enumerate(objects, 1):
        offsets.append(len(result))
        result.extend(f"{number} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref = len(result)
    result.extend(f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]:
        result.extend(f"{offset:010d} 00000 n \n".encode())
    result.extend(
        f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    return bytes(result)


def test_visual_proposal_requires_trusted_task_membership():
    content = "Figure caption explicitly describes a polymer network."
    span = {
        "page": 1,
        "start": 0,
        "end": len(content),
        "text": content,
        "span_sha256": digest(content.encode()),
    }
    visual = "vo-" + "a" * 20
    unit = {
        "unit_id": "swu-" + "b" * 20,
        "focus_spans": [span],
        "visual_task_ids": ["task-1"],
        "visual_objects": {"task-1": [visual]},
    }
    draft = proposal(unit["unit_id"], span)
    draft["objects"][0].update(
        type="Figure", visual_task_ids=["task-1"], figure_object_ids=[visual]
    )
    assert expert_proposals.validate(draft, unit, "run")["objects"][0]["figure_object_ids"] == [
        visual
    ]
    draft["objects"][0]["figure_object_ids"] = ["vo-" + "c" * 20]
    with pytest.raises(InvalidInput, match="proposal_invalid"):
        expert_proposals.validate(draft, unit, "run")
    draft["objects"][0].update(type="DataPoint", figure_object_ids=[])
    # The JSON schema is optional here; the upstream semantic validator explicitly
    # requires a visual source for Figure/Table/DataPoint. Text numbers remain Claims.
    with pytest.raises(InvalidInput, match="proposal_invalid"):
        expert_proposals.validate(draft, unit, "run")


@pytest.mark.skipif(not os.getenv("FIBER_PROJECT_SKILL_SOURCE"), reason="fixed Docker required")
def test_native_pdf_creates_self_contained_crops(config, owner, policy, monkeypatch):
    import test_expert_authoring as original
    from test_project_skill_docker import install

    configured = original.configured

    def configure(config, runtime):
        result = configured(config, runtime)
        install(config)
        return result

    monkeypatch.setattr(original, "configured", configure)
    monkeypatch.setattr(original, "pdf_bytes", lambda texts: native_visual_pdf())
    original.test_real_native_compiler_pi_proposals_and_draft_end_to_end(
        config, owner, policy, monkeypatch, True, True, False, visual_review=True
    )
    with database.connect(config.state_root) as db:
        source = json.loads(
            db.execute(
                "SELECT payload FROM vault_operations WHERE kind='expert_authoring'"
            ).fetchone()[0]
        )["import_id"]
        row = expert_authoring.lookup(db, source)
        root = config.state_root / row["value"]["seed_path"] / "output/workpack"
        seed = json.loads((root.parent / "result.json").read_bytes())
        assert any(
            "聚合物" in span["text"] for unit in seed["units"] for span in unit["focus_spans"]
        )
        manifest = json.loads((root / "workpack.json").read_bytes())
        assert manifest["visual_semantics"]["figure_crops"]
        for item in manifest["visual_semantics"]["figure_crops"].values():
            assert (root / item["path"]).read_bytes().startswith(b"\x89PNG")
        assert row["value"]["knowledge_verified"] is False
