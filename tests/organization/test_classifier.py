from fiber_bundle.organization.classifier import eligible, plan
from fiber_bundle.organization.contracts import Policy


def sample():
    e = {
        "id": "note:x",
        "path": "Inbox/a.md",
        "kind": "note",
        "title": "热管理记录",
        "sha256": "a" * 64,
        "text": "# 热管理记录\n#thermal",
        "tags": ["thermal"],
        "source": "note",
    }
    m = [
        {
            "id": "m1",
            "path": "Map/热管理 MOC.md",
            "title": "热管理 MOC",
            "members": {},
            "text": "# 热管理 MOC",
        }
    ]
    return e, m


def test_title_only_is_not_auto_linked_and_rule_is():
    e, m = sample()
    p = Policy().model_dump()
    assert plan(e, m, p, [], {})["status"] == "review"
    p["rules"] = [
        {"moc_id": "m1", "match": "all", "conditions": [{"field": "tag", "values": ["thermal"]}]}
    ]
    assert plan(e, m, p, [], {})["selected"] == ["m1"]


def test_manual_rejection_wins_over_rules():
    e, m = sample()
    e["tags"] = ["热管理"]
    p = Policy().model_dump()
    decision = [{"entity": e["id"], "moc": "m1", "state": "rejected"}]
    assert not plan(e, m, p, decision, {})["selected"]


def test_body_policy_catalog_and_manual_changes_invalidate():
    e, m = sample()
    p = Policy().model_dump()
    first = plan(e, m, p, [], {})["fingerprint"]
    e["text"] += "\nnew content"
    assert first != plan(e, m, p, [], {})["fingerprint"]
    assert first != plan(sample()[0], m, p, [], {"catalog": "new"})["fingerprint"]


def test_scope_all_any_and_exclusion():
    e, _ = sample()
    p = Policy(
        conditions=[
            {"field": "tag", "values": ["thermal"]},
            {"field": "source", "values": ["other"]},
        ]
    ).model_dump()
    assert not eligible(e, p)
    p["match"] = "any"
    assert eligible(e, p)
    p["exclude"] = ["Inbox"]
    assert not eligible(e, p)


def test_package_is_one_entity_with_manual_membership():
    e, m = sample()
    e.update(id="expert:x", kind="expert", text="[[热管理 MOC]]")
    assert plan(e, m, Policy().model_dump(), [], {})["selected"] == ["m1"]
