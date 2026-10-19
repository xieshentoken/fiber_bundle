"""Role controls exercise real admin routes, worker launches and legacy scope preservation."""

import time

from test_message_channels import bind
from test_message_channels import channel as channel

from fiber_bundle.channels import store
from fiber_bundle.channels.services import control, policy


def test_activation_and_role_pause_preserve_binding_and_history(config, channel, http):
    entry, root = channel
    identity = entry["id"]
    bind(config, entry)
    before = (root / "a.md").read_bytes()
    url = f"/admin/channels/{identity}/services"
    response = http.post(url + "/activate", json={"expected_channel_revision": 1})
    assert response.status_code == 200, response.text
    try:
        service = response.json()["query"]
        assert service["managed"] and service["enabled"] and service["revision"] == 2
        overview = http.get("/admin/channels/overview").json()
        value = next(x for x in overview["channels"] if x["id"] == identity)
        assert value["services"]["roles"]["query"]["enabled"]
        assert value["revision"] == 1 and value["state"] == "running"
        assert http.post(url + "/query/pause", json={"expected_revision": 1}).status_code == 422
        paused = http.post(url + "/query/pause", json={"expected_revision": 2})
        assert paused.status_code == 200, paused.text
        assert not paused.json()["enabled"]
        with store.readonly(config) as db:
            assert store.get(db, identity)["state"] == "running"
        assert (root / "a.md").read_bytes() == before
    finally:
        value = policy.get(config, identity, "query")
        if value and value["enabled"]:
            control.pause(config, identity, "query", value["revision"])
        deadline = time.monotonic() + 8
        while (
            control.status(config, identity, "query").get("process_running")
            and time.monotonic() < deadline
        ):
            time.sleep(0.05)
        assert not control.status(config, identity, "query").get("process_running")


def test_service_controls_require_admin_and_csrf(config, channel, http):
    entry, _ = channel
    identity = entry["id"]
    bind(config, entry)
    url = f"/admin/channels/{identity}/services/activate"
    response = http.post(
        url, json={"expected_channel_revision": 1}, headers={"x-csrf-token": "invalid"}
    )
    assert response.status_code == 403
    assert not policy.managed(config, identity)
    cookies = dict(http.cookies)
    http.cookies.clear()
    try:
        assert http.post(url, json={"expected_channel_revision": 1}).status_code == 401
    finally:
        http.cookies.update(cookies)
    assert not policy.managed(config, identity)
