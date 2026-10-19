import hashlib
import json
from pathlib import Path

import pytest

from fiber_bundle.document_contract import DocumentError
from fiber_bundle.document_image import MODULES, prepare


def dependency(tmp_path):
    root = tmp_path / "site"
    (root / "pypdf").mkdir(parents=True)
    (root / "pypdf/__init__.py").write_text("from ._version import __version__\n")
    (root / "pypdf/_version.py").write_text('__version__ = "6.10.0"\n')
    (root / "pypdf/__pycache__").mkdir()
    (root / "pypdf/__pycache__/private.pyc").write_bytes(b"not copied")
    license_dir = root / "pypdf-6.10.0.dist-info/licenses"
    license_dir.mkdir(parents=True)
    (license_dir / "LICENSE").write_text("synthetic-license")
    (root / "secret-config.json").write_text("not copied")
    return root


def test_context_copies_fixed_modules_pinned_dependency_and_license_only(tmp_path):
    root = dependency(tmp_path)
    target = tmp_path / "context"
    manifest = prepare(root, target)
    assert manifest["pypdf"] == "6.10.0" and manifest["dependency_downloaded"] is False
    assert set(manifest["files_sha256"]) == {"fiber_bundle/" + n for n in MODULES} | {
        "Dockerfile",
        "PYPDF-LICENSE",
        "pypdf/__init__.py",
        "pypdf/_version.py",
    }
    for name, digest in manifest["files_sha256"].items():
        assert hashlib.sha256((target / name).read_bytes()).hexdigest() == digest
    assert json.loads((target / "context.json").read_text()) == manifest
    assert not list(target.rglob("*.pyc")) and not list(target.rglob("secret-config.json"))
    with pytest.raises(DocumentError, match="document_context_exists"):
        prepare(root, target)
    assert (target / "PYPDF-LICENSE").read_text() == "synthetic-license"


@pytest.mark.parametrize("kind", ["version", "source_link", "parent_link", "license_missing"])
def test_dependency_pin_or_unsafe_source_rejected_before_output(tmp_path, kind):
    root = dependency(tmp_path)
    target = tmp_path / "context"
    if kind == "version":
        (root / "pypdf/_version.py").write_text('__version__ = "unfixed"\n')
    elif kind == "source_link":
        (root / "pypdf/bad.py").symlink_to(root / "pypdf/__init__.py")
    elif kind == "parent_link":
        (root / "linked").symlink_to(root / "pypdf", target_is_directory=True)
        root = root / "linked/.."
    else:
        (root / "pypdf-6.10.0.dist-info/licenses/LICENSE").unlink()
    with pytest.raises(DocumentError):
        prepare(root, target)
    assert not target.exists()


def test_dockerfile_is_offline_fixed_base_nonroot():
    from fiber_bundle import document_image

    text = (Path(document_image.__file__).parent / "document_executor/Dockerfile").read_text()
    assert "@sha256:" in text and "USER 1000:1000" in text
    assert "pip install" not in text and "apt-get" not in text
    assert "fiber_bundle.document_worker_entry" in text
