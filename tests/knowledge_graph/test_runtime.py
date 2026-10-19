import hashlib
import importlib.util
import json
import os
from dataclasses import asdict
from pathlib import Path

import pytest

from fiber_bundle.config import InvalidInput
from fiber_bundle.knowledge_graph.contracts import SEMANTICA_REVISION, Model, fingerprint
from fiber_bundle.knowledge_graph.runtime_context import ALGORITHM, file_hashes, verify
from fiber_bundle.knowledge_graph.worker import Policy, command, run


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    sdk = tmp_path / "semantica"
    sdk.mkdir()
    (sdk / "__init__.py").write_text('__version__="0.7.0"')
    monkeypatch.setattr(
        importlib.util,
        "find_spec",
        lambda _: importlib.util.spec_from_file_location("semantica", sdk / "__init__.py"),
    )
    model_root = tmp_path / "model"
    model_root.mkdir()
    (model_root / "weights").write_bytes(b"test artifact, not actual weights")
    files = file_hashes(model_root)
    model = Model("test-model", "fixed-revision", fingerprint(files), 3)
    context = {
        "schema": 1,
        "engine": {"name": "semantica", "version": "0.7.0", "revision": SEMANTICA_REVISION},
        "algorithm": ALGORITHM,
        "model": asdict(model),
        "model_files": files,
        "sdk_files_sha256": fingerprint(file_hashes(sdk, python_only=True)),
    }
    path = tmp_path / "context.json"
    path.write_text(json.dumps(context))
    return path, model_root, model, sdk


def test_artifact_checks_without_importing_sdk(runtime):
    path, root, model, _ = runtime
    assert verify(path, root, model)["model"] == asdict(model)


@pytest.mark.parametrize(
    "change", ["model", "sdk", "extra", "symlink", "hardlink", "model_binding"]
)
def test_artifact_changes_fail_closed(runtime, change):
    path, root, model, sdk = runtime
    if change == "model":
        (root / "weights").write_bytes(b"changed")
    elif change == "sdk":
        (sdk / "__init__.py").write_text('print("changed")')
    elif change == "extra":
        (root / "unexpected").touch()
    elif change == "symlink":
        (root / "link").symlink_to(root / "weights")
    elif change == "hardlink":
        os.link(root / "weights", root / "link")
    else:
        model = Model("different", model.revision, model.files_sha256, model.dimensions)
    with pytest.raises(InvalidInput):
        verify(path, root, model)


@pytest.mark.parametrize(
    "image,seconds", [("latest", 180), ("sha256:" + "a" * 64, True), ("sha256:" + "a" * 64, 301)]
)
def test_worker_policy_requires_finite_pinned_runtime(image, seconds):
    with pytest.raises(InvalidInput):
        Policy(image, seconds).validate()


def test_worker_command_has_no_host_capabilities():
    args = command(Policy("sha256:" + "a" * 64), "fiber-graph-" + "a" * 36)
    assert args[args.index("--network") + 1] == "none"
    assert "--read-only" in args and "--mount" not in args and "--volume" not in args
    assert args[args.index("--user") + 1] == "1000:1000"
    assert args[args.index("--cap-drop") + 1] == "ALL"
    assert "--memory" in args and "--pids-limit" in args


def test_default_off_does_not_create_worker(corpus_config, corpus, prepared, monkeypatch):
    import subprocess

    monkeypatch.setattr(subprocess, "run", lambda *_args, **_kwargs: pytest.fail("worker created"))
    with pytest.raises(InvalidInput, match="knowledge_graph_disabled"):
        run(corpus_config, corpus[0], prepared, Policy("sha256:" + "a" * 64))


def test_prestart_cancel_does_not_create_worker(corpus_config, corpus, prepared, monkeypatch):
    import subprocess

    monkeypatch.setattr(subprocess, "run", lambda *_args, **_kwargs: pytest.fail("worker created"))
    with pytest.raises(InvalidInput, match="graph_cancelled"):
        run(
            corpus_config,
            corpus[0],
            prepared,
            Policy("sha256:" + "a" * 64),
            enabled=True,
            cancelled=lambda: True,
        )


@pytest.mark.parametrize("owned", [True, False])
def test_create_timeout_is_bounded_and_only_its_owned_container_is_removed(
    corpus_config, corpus, prepared, monkeypatch, owned
):
    import subprocess

    image = "sha256:" + "a" * 64
    calls = []

    def docker(args, **kwargs):
        calls.append((args, kwargs))
        if "context" in args:
            return subprocess.CompletedProcess(args, 0, b"unix:///synthetic.sock\n")
        if "create" in args:
            raise subprocess.TimeoutExpired(args, kwargs["timeout"])
        if "inspect" in args:
            binding = {
                "Image": image,
                "Config": {
                    "Labels": {"fiber-bundle.graph": prepared.request.task_id if owned else "other"}
                },
            }
            return subprocess.CompletedProcess(args, 0, json.dumps([binding]).encode())
        if "rm" in args:
            return subprocess.CompletedProcess(args, 0, b"")
        assert "ps" in args
        return subprocess.CompletedProcess(args, 0, b"container\n" if len(calls) == 3 else b"")

    monkeypatch.setattr(subprocess, "run", docker)
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: pytest.fail("must not infer"))
    with pytest.raises(
        InvalidInput, match="graph_worker_timeout" if owned else "graph_cleanup_binding_invalid"
    ):
        run(corpus_config, corpus[0], prepared, Policy(image, 1), enabled=True)
    assert calls[0][1]["timeout"] <= 1 and calls[1][1]["timeout"] <= 1
    assert any("rm" in args for args, _ in calls) is owned
    assert all(
        "fiber-graph-" + prepared.request.task_id in args for args, _ in calls if "rm" in args
    )


def test_evaluation_manifest_is_frozen_and_uses_existing_sample_categories():
    root = Path(__file__).parent / "fixtures"
    corpus = json.loads((root / "s1_corpus.json").read_text())
    labels = json.loads((root / "s1_evaluation.json").read_text())
    assert (
        hashlib.sha256((root / "s1_corpus.json").read_bytes()).hexdigest()
        == labels["corpus_sha256"]
    )
    categories = {note["category"] for note in corpus["notes"]}
    assert set(c for group in labels["related_categories"] for c in group) == categories
    assert all(set(q["categories"]) <= categories for q in labels["paraphrase_questions"])
    assert labels["independent_human_review"] == "NOT RUN"
