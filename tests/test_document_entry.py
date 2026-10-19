import io
from pathlib import Path
from types import SimpleNamespace

import pytest

from fiber_bundle import document_worker_entry as entry
from fiber_bundle.document_contract import DocumentError


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"{",
        b'{"schema":true}',
        b'{"schema":2}',
        b'{"schema":1,"path":"/other"}',
        b'{"schema":1,"schema":1}',
        b'{"schema":1}' + b" " * 256,
    ],
)
def test_worker_rejects_other_requests_without_opening_files(monkeypatch, capsys, raw):
    monkeypatch.setattr(entry.sys, "stdin", SimpleNamespace(buffer=io.BytesIO(raw)))
    monkeypatch.setattr(entry, "capture", lambda *args: pytest.fail("must not capture"))
    assert entry.main() == 1
    assert capsys.readouterr().err == "document_capture_failed\n"


def test_worker_uses_fixed_paths_and_redacts_failure(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(entry.sys, "stdin", SimpleNamespace(buffer=io.BytesIO(b'{"schema":1}')))
    monkeypatch.setattr(entry, "capture", lambda *args: calls.append(args))
    assert entry.main() == 0
    assert calls == [(Path("/document/input"), Path("/document/output"))]

    def fail(*args):
        raise DocumentError("synthetic sensitive original text")

    monkeypatch.setattr(entry, "capture", fail)
    monkeypatch.setattr(entry.sys, "stdin", SimpleNamespace(buffer=io.BytesIO(b'{"schema":1}')))
    assert entry.main() == 1
    assert capsys.readouterr().err == "document_capture_failed\n"
