"""File inputs keep exact bytes, reject replacement and avoid whole-source host reads."""

import hashlib
import os
from unittest.mock import patch

import pytest
from document_support import candidate, pdf_bytes

from fiber_bundle import document_capture, document_contract, document_source
from fiber_bundle.config import IntegrityError
from fiber_bundle.document_source import DocumentFile


def original(tmp_path, data=None):
    data = data or pdf_bytes()
    path = tmp_path / "original.pdf"
    path.write_bytes(data)
    return DocumentFile(path, hashlib.sha256(data).hexdigest(), len(data)), data


def test_file_contract_and_copy_preserve_source_without_whole_read(tmp_path):
    file, data = original(tmp_path)
    target = tmp_path / "copy.pdf"
    with patch.object(DocumentFile, "read", side_effect=AssertionError("whole input read")):
        file.copy_new(target)
        assert document_contract.validate(candidate(data), file)["source_sha256"] == file.sha256
    assert target.read_bytes() == data
    assert target.stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        file.copy_new(target)


@pytest.mark.parametrize("alteration", ["digest", "symlink", "hardlink", "size"])
def test_file_rejects_tampered_and_aliased_input(tmp_path, alteration):
    file, data = original(tmp_path)
    if alteration == "digest":
        file.path.write_bytes(b"X" + data[1:])
    elif alteration == "size":
        file.path.write_bytes(data + b"X")
    elif alteration == "symlink":
        other = tmp_path / "other"
        file.path.rename(other)
        file.path.symlink_to(other)
    else:
        os.link(file.path, tmp_path / "alias")
    with pytest.raises((IntegrityError, OSError)):
        file.verify()


def test_mutation_during_open_is_rejected(tmp_path):
    file, _ = original(tmp_path)
    with pytest.raises(IntegrityError, match="changed"):
        with file.open():
            file.path.write_bytes(b"changed")


def test_capture_verifies_file_binding_and_exact_canonical_result(tmp_path):
    file, data = original(tmp_path)
    output = tmp_path / "output"
    output.mkdir()
    from fiber_bundle.inputs import canonical

    document_capture.write_new(output, "result.json", canonical(candidate(data)).encode())
    with patch.object(DocumentFile, "read", side_effect=AssertionError("whole source read")):
        assert document_capture.verify(output, file)["source_bytes"] == len(data)
    file.path.write_bytes(b"X" + data[1:])
    with pytest.raises(document_contract.DocumentError):
        document_capture.verify(output, file)


def test_copy_uses_bounded_read_requests(tmp_path, monkeypatch):
    file, data = original(tmp_path, b"S" * (document_source.CHUNK_BYTES * 5 + 1))
    sizes = []
    real_fdopen = document_source.os.fdopen

    class Tracked:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            self.stream.__enter__()
            return self

        def __exit__(self, *args):
            return self.stream.__exit__(*args)

        def __getattr__(self, name):
            return getattr(self.stream, name)

        def read(self, size=-1):
            sizes.append(size)
            return self.stream.read(size)

    def tracked(fd, mode):
        stream = real_fdopen(fd, mode)
        return Tracked(stream) if mode == "rb" else stream

    monkeypatch.setattr(document_source.os, "fdopen", tracked)
    file.copy_new(tmp_path / "copy")
    assert sizes and set(sizes) == {document_source.CHUNK_BYTES}
    assert (tmp_path / "copy").read_bytes() == data


def test_batch_files_are_required_and_bound_to_original_pages(tmp_path):
    from fiber_bundle.document_capacity import batches
    from fiber_bundle.inputs import canonical

    file, data = original(tmp_path)
    value = candidate(data)
    value["batches"] = batches(value["pages"])
    output = tmp_path / "output"
    output.mkdir()
    document_capture.write_new(output, "result.json", canonical(value).encode())
    with pytest.raises(OSError):
        document_capture.verify(output, file)
    batch = {
        "schema": "pdf-page-batch/v1",
        "source_sha256": file.sha256,
        "source_bytes": file.byte_count,
        "first_page": 1,
        "last_page": 1,
        "pages": value["pages"],
    }
    document_capture.write_new(output, "batch-0001.json", canonical(batch).encode())
    assert document_capture.verify(output, file)["batches"] == value["batches"]
    changed = {**batch, "first_page": 2}
    (output / "batch-0001.json").write_bytes(canonical(changed).encode())
    with pytest.raises(document_contract.DocumentError, match="document_batch_binding"):
        document_capture.verify(output, file)
