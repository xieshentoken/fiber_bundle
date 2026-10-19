from fiber_bundle import article_dates

URL = "https://mp.weixin.qq.com/s/synthetic"


def test_named_wechat_timestamp_with_local_timezone_and_meta_agreement():
    value = article_dates.extract(
        '<script>var ct = "1789903860";\nvar create_time = "1789903860" * 1;</script>'
        '<meta property="article:published_time" content="2026-09-20T19:31:00+08:00">',
        URL,
    )
    assert value["state"] == "known"
    assert value["value"] == "2026-09-20T19:31:00+08:00"
    assert len(value["evidence"]) == 3
    assert value["evidence"][0]["locator"] == "script[1].var.ct"


def test_missing_conflicting_invalid_and_other_host_are_unknown():
    for html, url in [
        ("<p>2026-09-20 19:31</p>", URL),
        ('<script>var ct = "1789903860";\nvar create_time = "1789903861";</script>', URL),
        ('<meta name="date" content="2026-09-20">', URL),
        ('<meta name="date" content="invalid">', URL),
        ('<script>var ct = "1789903860";</script>', "https://example.com/a"),
    ]:
        value = article_dates.extract(html, url)
        assert value["state"] == "unknown" and value["value"] == ""
