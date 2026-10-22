"""The Atlas UI remains local and shares the existing authentication boundary."""


def test_root_admits_one_renderer_and_packages_atlas(http):
    response = http.get("/")
    assert response.status_code == 200
    assert "/static/ui_entry.js" in response.text
    assert "/static/app.js" not in response.text
    for path in [
        "ui_entry.js",
        "theme_entry.js",
        "atlas/index.html",
        "atlas/main.js",
        "atlas/vault_index.js",
        "atlas/mark.svg",
    ]:
        asset = http.get("/static/" + path)
        assert asset.status_code == 200, path
        assert "script-src 'self'" in asset.headers["content-security-policy"]
    assert http.get("/static/atlas/../api.py").status_code == 404
    assert http.get("/static/atlas/secrets.json").status_code == 404
