"""Pinned compiler creates source-bound units; model proposals never author reviewer records."""

import json
import sys
from pathlib import Path

from .config import InvalidInput
from .expert_worker_entry import validate_runtime
from .inputs import canonical
from .objects import digest
from .query_policy_store import write_private


def seed(root: Path) -> None:
    spec = json.loads((root / "input/request.json").read_bytes())
    validate_runtime(spec["skill_sha256"])
    sys.path.insert(0, "/opt/skill/scripts")
    source = root / "input/source.pdf"
    if digest(source.read_bytes()) != spec["source_sha256"]:
        raise InvalidInput("expert_authoring_source_changed")
    from pypdf import PdfReader  # type: ignore[import-not-found]

    reader = PdfReader(source, strict=True)
    if reader.is_encrypted or not 1 <= len(reader.pages) <= spec["limits"]["pdf_pages"]:
        raise InvalidInput("expert_authoring_page_capacity")
    if (root / "output/workpack/workpack.json").is_file():
        pass
    elif spec["profile"] == "native-text":
        from pdf_structure import (  # type: ignore[import-not-found]
            compile_pdf_intermediate_representation,
            write_intermediate_representation,
        )
        from semantic_promotion import build_semantic_workpack  # type: ignore[import-not-found]

        ir = root / "output/ir"
        result = compile_pdf_intermediate_representation(source, 1, len(reader.pages))
        if not result["document_map"].get("segments"):
            raise InvalidInput("expert_authoring_headings_unresolved")
        write_intermediate_representation(ir, result)
        build_semantic_workpack(
            source, ir, root / "output/workpack", max_focus_characters=spec["limits"]["text_bytes"]
        )
    else:
        # Scan units require the canonical operator/runtime and accepted structure evidence.
        # A supplied workpack is copied by the host; the compiler validates its lineage later.
        if not (root / "output/workpack/workpack.json").is_file():
            raise InvalidInput("expert_authoring_scan_workpack_required")
    pack = root / "output/workpack"
    manifest = json.loads((pack / "workpack.json").read_bytes())
    if manifest["source"]["sha256"] != spec["source_sha256"]:
        raise InvalidInput("expert_authoring_source_changed")
    schema = json.loads(Path("/opt/skill/assets/schemas/semantic-draft.schema.json").read_bytes())
    units = []
    for entry in manifest["units"]:
        relative = entry["path"]
        if Path(relative).is_absolute() or ".." in Path(relative).parts:
            raise InvalidInput("expert_authoring_unit_invalid")
        unit = json.loads((pack / relative).read_bytes())
        units.append(unit)
    if not units or len(units) > 2000:
        raise InvalidInput("expert_authoring_unit_invalid")
    write_private(
        root / "output/result.json",
        canonical(
            {
                "schema": "fiber-expert-authoring-seed/v1",
                "source_sha256": spec["source_sha256"],
                "skill_sha256": spec["skill_sha256"],
                "units": units,
                "draft_schema": schema,
                "page_count": len(reader.pages),
                "known_gaps": [
                    f"page_not_in_focus:{page}"
                    for page in range(1, len(reader.pages) + 1)
                    if page not in {span["page"] for unit in units for span in unit["focus_spans"]}
                ],
                "visual_tasks": (pack / manifest["visual_tasks"]).read_text(),
            }
        ).encode(),
    )


def validate(root: Path) -> None:
    spec = json.loads((root / "input/request.json").read_bytes())
    validate_runtime(spec["skill_sha256"])
    sys.path.insert(0, "/opt/skill/scripts")
    from semantic_promotion import validate_semantic_workpack

    issues, summary = validate_semantic_workpack(
        root / "input/source.pdf", root / "output/workpack", require_complete_drafts=True
    )
    if any(i.severity == "error" for i in issues):
        raise InvalidInput("expert_authoring_proposal_invalid")
    write_private(
        root / "output/validation.json",
        canonical(
            {
                "schema": "fiber-expert-authoring-validation/v1",
                "summary": summary,
                "warnings": [i.code for i in issues],
                "knowledge_verified": False,
            }
        ).encode(),
    )


def main() -> int:
    try:
        root = Path("/task")
        spec = json.loads((root / "input/request.json").read_bytes())
        if spec["action"] == "seed":
            seed(root)
        elif spec["action"] == "validate":
            validate(root)
        else:
            raise InvalidInput("expert_authoring_action_invalid")
        return 0
    except InvalidInput as error:
        from .processing_errors import MESSAGES

        print(str(error) if str(error) in MESSAGES else "expert_authoring_tool_failed")
        return 1
    except Exception:
        print("expert_authoring_tool_failed")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
