import json

from fiber_bundle import database, note_index
from fiber_bundle.organization import catalog, text_features


def test_complete_body_offsets_skip_scaffolding_without_tail_truncation():
    raw = (
        "---\ntags: []\n---\n# 标题\n\n![图](private.png)\n"
        + "无关开场。" * 500
        + "\n末尾研究热流密度与导热系数。\n"
    )
    parts = text_features.passages(raw)
    assert all(p["text"] == raw[p["start"] : p["end"]] for p in parts)
    assert parts[-1]["text"] == "末尾研究热流密度与导热系数。"
    assert not any("private.png" in p["text"] or "tags:" in p["text"] for p in parts)


def test_expert_projection_keeps_bilingual_titles_statements_and_domain():
    module = {
        "files": {"manifest.json": json.dumps({"package": {"domain": "heat-conduction"}}).encode()},
        "objects": [
            {
                "id": "o1",
                "title": "Heat flux 热流密度",
                "statement": "热流沿温度梯度传递。",
                "terms": {"concept_heat_flux": 2, "热流": 1},
                "path": "objects/o1.json",
            }
        ],
    }
    view = text_features.expert(module)
    assert view["domain"] == "heat-conduction" and view["concepts"] == ["concept_heat_flux"]
    assert "Heat flux 热流密度" in view["text"] and "热流沿温度梯度传递。" in view["text"]
    assert view["sections"][0]["object_id"] == "o1"


def test_moc_profile_keeps_human_scope_and_ignores_generated_members():
    moc = {
        "id": "m",
        "title": "热管理 MOC",
        "path": "Map/热管理 MOC.md",
        "text": (
            "# 热管理 MOC\n## 传热基础与仿真\n导热、热流与边界条件。\n"
            "<!-- fiber-moc:m begin -->\n[[noise]]\n<!-- fiber-moc:m end -->\n"
        ),
    }
    profile = text_features.moc_profile(moc)
    assert "传热基础与仿真" in profile["text"] and "noise" not in profile["text"]
    assert not profile["navigation"]
    assert text_features.moc_profile({**moc, "title": "知识库总览"})["navigation"]


def test_templates_and_attachments_are_pruned_before_reading(config, scope, monkeypatch):
    cid, vid, root = scope
    for name in ["05-Templates/empty.md", "Templates/Daily.md", "Attachments/original.md"]:
        p = root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("# Empty template")
    note = root / "Inbox/actual.md"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text("# Actual\n正文。")
    seen = []
    original = note_index.read_note

    def read(root, relative, limit):
        seen.append(relative)
        return original(root, relative, limit)

    monkeypatch.setattr(note_index, "read_note", read)
    with database.connect(config.state_root) as db:
        entities, _, _ = catalog.snapshot(config, db, cid, vid)
    assert [e["path"] for e in entities] == ["Inbox/actual.md"]
    assert seen == ["Inbox/actual.md"]


def test_body_condition_and_strong_rule_cover_the_end_of_a_long_note():
    from fiber_bundle.organization import classifier

    entity = {
        "id": "note:one",
        "kind": "note",
        "path": "Inbox/long.md",
        "title": "Generic",
        "text": "开场普通内容。" * 400 + "\n末尾讨论导热边界条件。",
        "tags": [],
        "sha256": "a" * 64,
    }
    from fiber_bundle.organization.contracts import Policy

    policy = Policy(
        conditions=[{"field": "body", "values": ["导热边界条件"]}],
        rules=[{"moc_id": "heat", "conditions": [{"field": "body", "values": ["导热边界条件"]}]}],
    ).model_dump()
    assert classifier.eligible(entity, policy)
    moc = {
        "id": "heat",
        "title": "热管理 MOC",
        "path": "Map/热管理 MOC.md",
        "text": "# 热管理 MOC",
        "members": {},
    }
    result = classifier.plan(entity, [moc], policy, [], {"scope": {}})
    assert result["selected"] == ["heat"] and result["model_called"] is False


def test_quote_maps_paragraph_whitespace_back_to_original_but_never_edits_words():
    body = "# Title\n所以,Codex 会先处理继承材料。\n\n对于常规历史记录,保留用户消息。\n"
    span = text_features.source_span(
        body, "Codex 会先处理继承材料。对于常规历史记录,保留用户消息。"
    )
    assert span and span["quote"] == body[span["start"] : span["end"]]
    assert "\n\n" in span["quote"]
    assert (
        text_features.source_span(
            body, span["quote"].replace("\n", ""), windows=text_features.passages(body)
        )
        == span
    )
    assert text_features.source_span(body, "Codex 会先删除继承材料。") is None
    assert text_features.source_span("not heat transfer", "notheat transfer") is None
    assert (
        text_features.source_span(
            "正文讨论传热。\n# 中间标题\n其他正文。", "正文讨论传热。# 中间标题其他正文。"
        )
        is None
    )


def test_joined_projection_quote_keeps_separate_original_fragments():
    body = "Codex 会先处理继承材料。\n\n### 执行过程\n\n对于常规历史记录,保留用户消息。\n"
    quote = "Codex 会先处理继承材料。对于常规历史记录,保留用户消息。"
    assert text_features.source_span(body, quote) is None
    pieces = text_features.source_spans(body, quote, windows=text_features.passages(body))
    assert len(pieces) == 2 and all(body[p["start"] : p["end"]] == p["quote"] for p in pieces)
    assert all("###" not in p["quote"] for p in pieces)
    assert not text_features.source_spans(body, quote.replace("保留", "删除"))


def test_admin_listing_does_not_expand_expert_body_projection():
    public = catalog.public_entity(
        {
            "id": "expert:test",
            "title": "Book",
            "text": "private body",
            "sections": [{"text": "private statement"}],
            "concepts": ["private concept"],
            "file_key": [1, 2, "birth"],
            "domain": "heat-conduction",
        }
    )
    assert public == {"id": "expert:test", "title": "Book", "domain": "heat-conduction"}
