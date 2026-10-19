import json
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from test_articles import X_URL

from fiber_bundle import article_worker as worker
from fiber_bundle.articles import ArticleError

POLICY = worker.CapturePolicy("sha256:" + "a" * 64)


@pytest.mark.parametrize("image", ["latest", "python:3.13", "sha256:abc"])
def test_mutable_or_invalid_image_rejected(image):
    with pytest.raises(ArticleError):
        worker.CapturePolicy(image).validate()


def test_command_has_one_private_mount_and_no_credentials(tmp_path):
    argv = worker.command(POLICY, tmp_path, "fiber-capture-" + str(uuid4()))
    assert argv.count("--mount") == 1
    assert argv[argv.index("--mount") + 1] == f"type=bind,source={tmp_path},target=/capture"
    assert "--env" not in argv and "--publish" not in argv and "--privileged" not in argv
    assert "--read-only" in argv and "no-new-privileges" in argv
    assert argv[argv.index("--user") + 1] == "1000:1000"
    assert argv[-1] == POLICY.image


def test_initial_denial_does_not_create_a_task(config):
    def denied():
        raise PermissionError("synthetic denial")

    with pytest.raises(PermissionError):
        worker.execute_capture(config, POLICY, X_URL, guard=denied)
    assert not config.state_root.exists()


def test_revoked_after_create_never_starts_worker(config, monkeypatch):
    created, removed = [], []
    monkeypatch.setattr(worker, "create_container", lambda p, output, name: created.append(name))
    monkeypatch.setattr(worker, "remove_container", lambda name: removed.append(name))

    def guard():
        if created:
            raise PermissionError("revoked before start")

    def forbidden(name):
        pytest.fail("revoked task must never start")

    monkeypatch.setattr(worker, "start_command", forbidden)
    with pytest.raises(PermissionError):
        worker.execute_capture(config, POLICY, X_URL, guard=guard)
    assert created == removed and len(created) == 1


def child_command(monkeypatch, *, sleep=False):
    tests = Path(__file__).parent
    code = (
        "import sys,time,json; from pathlib import Path; "
        f"sys.path.insert(0,{str(tests)!r}); "
        "from test_article_capture import fixtures; "
        "from fiber_bundle.article_capture import capture; "
        "request=json.load(sys.stdin); "
        "capture(request['url'],Path(sys.argv[1]),fixtures); " + ("time.sleep(10)" if sleep else "")
    )
    scope = []
    monkeypatch.setattr(worker, "create_container", lambda p, output, name: scope.append(output))
    monkeypatch.setattr(worker, "remove_container", lambda name: None)
    monkeypatch.setattr(
        worker,
        "start_command",
        lambda name: [
            sys.executable,
            "-c",
            code,
            str(scope[0]),
        ],
    )


def test_host_revalidates_after_writer_exit(config, monkeypatch):
    child_command(monkeypatch)
    checks = []
    result = worker.execute_capture(config, POLICY, X_URL, guard=lambda: checks.append(1))
    assert len(checks) >= 2
    assert len(result.verified.draft.images) == 1
    assert json.loads((result.directory.parent / "request.json").read_text()) == {
        "url": X_URL,
        "image": POLICY.image,
    }


def test_final_guard_denial_withholds_verified_result(config, monkeypatch):
    child_command(monkeypatch)
    original = worker.verify_capture
    done = []

    def verify(*args):
        result = original(*args)
        done.append(True)
        return result

    monkeypatch.setattr(worker, "verify_capture", verify)

    def guard():
        if done:
            raise PermissionError("revoked while completing")

    with pytest.raises(PermissionError):
        worker.execute_capture(config, POLICY, X_URL, guard=guard)
    assert list(config.state_root.glob("article-captures/*/output/source.html"))


@pytest.mark.parametrize("mode", ["timeout", "revocation"])
def test_running_failure_stops_writer_and_keeps_partial_files(config, monkeypatch, mode):
    child_command(monkeypatch, sleep=True)
    cleanup = []
    monkeypatch.setattr(worker, "remove_container", lambda name: cleanup.append(name))

    def guard():
        if mode == "revocation" and list(
            config.state_root.glob("article-captures/*/output/source.html")
        ):
            raise PermissionError("revoked")

    with pytest.raises((ArticleError, PermissionError)):
        worker.execute_capture(config, worker.CapturePolicy(POLICY.image, 1), X_URL, guard=guard)
    assert len(cleanup) == 1 and cleanup[0].startswith("fiber-capture-")
    assert list(config.state_root.glob("article-captures/*/output/source.html"))
