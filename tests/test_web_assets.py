"""UI resources stay explicitly hosted and packaged; data routes keep their guards."""

import pytest

from fiber_bundle.web_routes import STATIC_ASSETS, WEB_ROOT


@pytest.mark.parametrize(
    "name",
    [
        "home.css",
        "organization_members.js",
        "home.js",
        "home_api.js",
        "home_data.js",
        "home_status.js",
        "home_render.js",
        "home_flow.js",
        "page_context.js",
        "tokens.css",
        "records.css",
        "shell.css",
        "controls.css",
        "agents.css",
        "vaults.css",
        "settings.css",
        "unified_workflow.css",
        "usage.css",
        "settings.js",
        "system_api.js",
        "appearance.js",
        "desktop_appearance.js",
        "appearance_preferences.js",
        "appearance_bootstrap.js",
        "themes.js",
        "theme_picker.js",
        "themes.css",
        "theme_picker.css",
        "appearance_overrides.css",
        "local_fonts.js",
        "font_picker.js",
        "session_expiry.js",
        "usage.js",
        "usage_chart.js",
        "agent_layout.js",
        "agent_labels.js",
        "expert_results.js",
        "fonts.css",
        "dm-sans.ttf",
        "noto-sans-sc.ttf",
        "logo-original.png",
        "sidebar.js",
        "select_controls.js",
        "select_state.js",
        "record_ui.js",
        "execution.js",
        "icons.js",
        "navigation.js",
        "batch_countdown.js",
        "page_loading.js",
        "unsaved_changes.js",
        "operation_feedback.js",
        "source_selection.js",
        "articles.js",
        "knowledge_graph_selection.js",
        "knowledge_graph_setup.js",
        "knowledge_graph_loading.js",
        "vault_moc_registration.js",
        "knowledge_graph_cards.js",
        "knowledge_graph_observer.js",
        "reading_layout.css",
        "help_tip.js",
        "inventory_summary.js",
        "form_sections.js",
        "vault_scope.js",
        *(name for name in STATIC_ASSETS if name.startswith("i18n/")),
    ],
)
def test_visual_assets_are_explicitly_served(http, name):
    response = http.get("/static/" + name)
    assert response.status_code == 200
    assert response.content == (WEB_ROOT / name).read_bytes()
    assert response.headers["content-type"].startswith(STATIC_ASSETS[name])


def test_visual_resource_hosting_does_not_expose_data_or_source(http):
    for path in ("/static/config.test.json", "/static/metadata.sqlite3", "/static/auth.py"):
        assert http.get(path).status_code == 404
    for path in (
        "/static/i18n/config.test.json",
        "/static/i18n/%2e%2e/auth.py",
        "/static/i18n/not-packaged.js",
    ):
        assert http.get(path).status_code == 404
    http.cookies.clear()
    assert http.get("/admin/backups").status_code == 401
    assert http.get("/admin/clients").status_code == 401
