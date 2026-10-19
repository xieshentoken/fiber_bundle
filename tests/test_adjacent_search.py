"""Exact bounded line windows through session, Vault and document query surfaces."""

from test_session_search import find, intake
from test_session_search import owner as owner

from fiber_bundle.search_matching import best_passage, terms


def test_exact_crlf_line_window_and_far_or_long_negative():
    content = "无关\r\n青松\r\n测试压力：2.4 MPa\r\n其他"
    result = best_passage("material.txt", content, terms("青松 测试压力"))
    assert result is not None
    assert result[1:] == (2, "青松\r\n测试压力：2.4 MPa")
    assert result[2] in content and len(result[2]) <= 400
    assert best_passage("material.txt", "青松\n\n测试压力", terms("青松 测试压力")) is None
    assert (
        best_passage("material.txt", "青松\n一\n二\n三\n测试压力", terms("青松 测试压力")) is None
    )
    assert (
        best_passage("material.txt", "青松" + "一" * 401 + "\n测试压力", terms("青松 测试压力"))
        is None
    )


def test_session_and_vault_share_window_and_scope_revalidation(http, owner, config):
    text = "青松\n测试压力\n2.4 MPa"
    intake(http, owner, body=text.encode())
    hit = find(http, owner, "青松 测试压力").json()["results"][0]
    assert hit["line"] == 1 and hit["line_end"] == 2
    assert hit["snippet"] == "青松\n测试压力"
    root = config.project_root / "test vault/temp_vault1"
    (root / "window.md").write_text("# 正式测试\n" + text)
    result = find(
        http, owner, "测试压力 2.4", session=None, vault_ids=[owner[2]["personal"]]
    ).json()
    hit = result["results"][0]
    assert hit["line"] == 3 and hit["line_end"] == 4
    assert hit["snippet"] == "测试压力\n2.4 MPa"
    assert len(hit["snippet"]) <= 400
    (root / "window.md").write_text("# 已变更\n无压力数据")
    assert (
        find(http, owner, "测试压力 2.4", session=None, vault_ids=[owner[2]["personal"]]).json()[
            "results"
        ]
        == []
    )
