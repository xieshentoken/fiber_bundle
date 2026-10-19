"""Container-only conversion of explicit legacy Word sources, never host Office execution."""

import hashlib
import io
import subprocess
from pathlib import Path
from typing import Any

from .document_contract import DocumentError
from .image_decoding import MANIFEST, verify_dependencies
from .material_contract import result, sha


def inventory() -> dict[str, str]:
    return {
        name: hashlib.sha256(path.read_bytes()).hexdigest()
        for name, path in {
            "soffice": Path("/usr/lib/libreoffice/program/soffice.bin"),
            "converter": Path(__file__),
            "decoder_packages": MANIFEST,
        }.items()
    }


def preflight(source: bytes) -> None:
    verify_dependencies()
    import olefile  # type: ignore[import-untyped]

    if olefile.__version__ != "0.47" or not source.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        raise DocumentError("doc_legacy_format")
    try:
        with olefile.OleFileIO(io.BytesIO(source), raise_defects=olefile.DEFECT_INCORRECT) as file:
            names = file.listdir()
            if len(names) > 256 or any(
                any(
                    part.casefold()
                    in {"vba", "macros", "encryptioninfo", "encryptedpackage", "objectpool"}
                    for part in name
                )
                for name in names
            ):
                raise DocumentError("doc_active_or_encrypted_content")
            header = file.openstream("WordDocument").read(32)
            if len(header) != 32 or header[:2] != b"\xec\xa5":
                raise DocumentError("doc_legacy_header")
            flags = int.from_bytes(header[10:12], "little")
            if flags & (0x0100 | 0x8000):
                raise DocumentError("doc_encrypted")
            if not file.exists("1Table" if flags & 0x0200 else "0Table"):
                raise DocumentError("doc_legacy_table")
    except (OSError, ValueError, KeyError) as exc:
        raise DocumentError("doc_legacy_invalid") from exc


def convert(source: bytes, runtime: dict[str, Any], work: Path) -> bytes:
    if inventory() != runtime["runtime_files_sha256"]:
        raise DocumentError("doc_conversion_runtime_drift")
    preflight(source)
    root = work / "doc-conversion"
    root.mkdir(mode=0o700)
    original = root / "source.doc"
    original.write_bytes(source)
    profile = root / "profile"
    profile.mkdir()
    (profile / "user").mkdir()
    (profile / "user/registrymodifications.xcu").write_text(
        '<oor:items xmlns:oor="http://openoffice.org/2001/registry">'
        '<item oor:path="/org.openoffice.Office.Common/Security/Scripting">'
        '<prop oor:name="MacroSecurityLevel" oor:op="fuse"><value>3</value></prop>'
        "</item></oor:items>"
    )
    try:
        subprocess.run(
            [
                "/usr/bin/soffice",
                "-env:UserInstallation=" + profile.as_uri(),
                "--headless",
                "--nologo",
                "--nodefault",
                "--nofirststartwizard",
                "--convert-to",
                "docx:Office Open XML Text",
                "--outdir",
                str(root),
                str(original),
            ],
            timeout=90,
            check=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env={
                "PATH": "/usr/bin:/bin",
                "HOME": str(root),
                "LANG": "C.UTF-8",
                "SAL_USE_VCLPLUGIN": "svp",
            },
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise DocumentError("doc_conversion_failed") from exc
    target = root / "source.docx"
    if (
        target.is_symlink()
        or not target.is_file()
        or not 0 < target.stat().st_size <= 8 * 1024 * 1024
    ):
        raise DocumentError("doc_conversion_output")
    return target.read_bytes()


def extract(source: bytes, runtime: dict[str, Any], work: Path) -> tuple[dict[str, Any], bytes]:
    converted = convert(source, runtime, work)
    from .office_documents import extract as parse

    candidate = parse(converted, "docx")
    for segment in candidate["segments"]:
        segment["review_reasons"] = sorted(set(segment["review_reasons"] + ["visual_review"]))
    bound_runtime = {
        **runtime,
        "conversion": {
            "source_sha256": sha(source),
            "derived_sha256": sha(converted),
            "derived_bytes": len(converted),
            "derived_format": "docx",
        },
    }
    return result(
        source,
        "doc",
        candidate["segments"],
        attachments=candidate["attachments"],
        runtime=bound_runtime,
        complete=candidate["complete"],
    ), converted
