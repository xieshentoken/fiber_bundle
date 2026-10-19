from fiber_bundle.organization import engine


def test_taxonomy_is_read_only_and_groups_unclassified():
    entities = [
        {
            "id": "note:" + str(i),
            "path": f"Inbox/{i}.md",
            "title": f"记录{i}",
            "text": "# 新主题\n#超导材料\n测试文本",
            "tags": ["超导材料"],
        }
        for i in range(4)
    ]
    report = engine.taxonomy(entities, [], [])
    assert report["requires_review"]
    assert report["create_candidates"][0]["action"] == "propose_create"
