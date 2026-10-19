"""Font metadata requires local admin access and never changes stored settings."""

import sys

import pytest

from fiber_bundle import system_fonts

FONT = {"family": "Example Sans", "postscriptName": "ExampleSans-Regular", "style": "Regular"}


def test_font_metadata_is_read_only_and_authenticated(http, monkeypatch):
    calls = []

    def read():
        calls.append(True)
        return {"supported": True, "fonts": [FONT]}

    monkeypatch.setattr(system_fonts, "installed_fonts", read)
    before = http.get("/admin/system/settings").json()
    response = http.get("/admin/system/fonts")
    assert response.status_code == 200
    assert response.json() == {"supported": True, "fonts": [FONT]}
    assert http.get("/admin/system/settings").json() == before
    assert calls == [True]
    http.cookies.clear()
    assert http.get("/admin/system/fonts").status_code == 401
    assert calls == [True]


def test_font_metadata_rejects_other_origins(http, monkeypatch):
    def forbidden():
        pytest.fail("must authenticate before reading OS metadata")

    monkeypatch.setattr(system_fonts, "installed_fonts", forbidden)
    assert (
        http.get("/admin/system/fonts", headers={"Origin": "https://evil.example"}).status_code
        == 403
    )


def test_font_enumeration_failure_is_retryable(http, monkeypatch):
    def failed():
        raise system_fonts.FontCatalogError("private OS diagnostic")

    monkeypatch.setattr(system_fonts, "installed_fonts", failed)
    response = http.get("/admin/system/fonts")
    assert response.status_code == 503
    assert "private OS diagnostic" not in response.text
    monkeypatch.setattr(system_fonts, "installed_fonts", lambda: {"supported": False, "fonts": []})
    assert http.get("/admin/system/fonts").json() == {"supported": False, "fonts": []}


def test_non_macos_does_not_load_native_libraries(monkeypatch):
    monkeypatch.setattr(system_fonts.sys, "platform", "linux")
    monkeypatch.setattr(system_fonts.ctypes, "CDLL", lambda _: pytest.fail("unsupported platform"))
    assert system_fonts.installed_fonts() == {"supported": False, "fonts": []}


def test_missing_native_library_has_explicit_error(monkeypatch):
    monkeypatch.setattr(system_fonts.sys, "platform", "darwin")

    def missing(_):
        raise OSError("missing framework")

    monkeypatch.setattr(system_fonts.ctypes, "CDLL", missing)
    with pytest.raises(system_fonts.FontCatalogError, match="CoreText font enumeration failed"):
        system_fonts.installed_fonts()


@pytest.mark.skipif(sys.platform != "darwin", reason="requires real CoreText")
def test_real_macos_catalog_contains_only_valid_metadata():
    result = system_fonts.installed_fonts()
    assert result["supported"] is True
    assert 0 < len(result["fonts"]) <= 16384
    for font in result["fonts"]:
        assert set(font) == {"family", "postscriptName", "style"}
        assert all(isinstance(value, str) and 0 < len(value) <= 256 for value in font.values())
        assert all(
            not any(ord(char) < 32 or ord(char) == 127 for char in value) for value in font.values()
        )
