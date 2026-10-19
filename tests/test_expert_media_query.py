"""Actual published image package with scoped text retrieval and lazy attachment reads."""

import json
import os
import shutil
import time
import tracemalloc
from pathlib import Path

import pytest
from test_expert_authoring import explicit_progression_only as explicit_progression_only

from fiber_bundle import (
    auth,
    database,
    evidence_answers,
    expert_evidence,
    expert_media,
    expert_publication,
    expert_query,
    expert_reference,
    publication_locations,
    published_expert,
)
from fiber_bundle.agent_connections import knowledge
from fiber_bundle.config import InvalidInput
from fiber_bundle.inputs import canonical
from fiber_bundle.objects import digest

pytest_plugins = ["test_source_imports", "test_execution"]
pytestmark = pytest.mark.skipif(
    not os.getenv("FIBER_PROJECT_SKILL_SOURCE"), reason="fixed Docker required"
)


@pytest.fixture
def media_package(config, owner, policy, monkeypatch):
    from test_expert_media_authoring import test_native_pdf_creates_self_contained_crops

    test_native_pdf_creates_self_contained_crops(config, owner, policy, monkeypatch)
    cid, _, ids = owner
    with database.connect(config.state_root) as db:
        source = json.loads(
            db.execute(
                "SELECT payload FROM vault_operations WHERE kind='expert_authoring'"
            ).fetchone()[0]
        )["import_id"]
        packet = expert_evidence.describe(config, db, cid, source)
        expert_publication.publish(config, db, cid, source, packet["manifest_sha256"], True, True)
        module = published_expert.load(config, db, cid, source)
    assert module["catalog"]["figures"]
    return cid, ids["personal"], source, module


def test_text_catalog_query_and_attachment_read_counts(
    config, media_package, monkeypatch, tmp_path
):
    cid, vid, source, module = media_package
    reads = []
    read_events = []
    original = published_expert.read
    package = tmp_path / "compiled-package"
    for name, raw in module["files"].items():
        path = package / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    script_result = expert_reference.run_package(package, module["runtime"]["image"], "Figure 1", 5)
    changed_runtime = {
        **module,
        "files": {**module["files"], "scripts/query_reference.py": b"changed"},
    }
    with pytest.raises(InvalidInput, match="knowledge_module_invalid"):
        expert_query.execute(changed_runtime, "Figure 1", 5)

    def no_runtime(*args):
        raise AssertionError("New text queries must not launch a container")

    monkeypatch.setattr(expert_reference, "run_package", no_runtime)

    def track(root, name):
        reads.append(name)
        value = original(root, name)
        read_events.append((name, len(value[0])))
        return value

    monkeypatch.setattr(published_expert, "read", track)
    with database.connect(config.state_root) as db:
        catalog = knowledge.catalog(config, db, cid, [vid])
        assert len([m for m in catalog["items"] if m["kind"] == "expert"]) == 1
        text = published_expert.text(config, db, cid, source, module["version"])
        assert text["catalog"]["figures"]
    with pytest.raises(InvalidInput, match="expert_query_scope"):
        knowledge.query_expert(config, cid, module["module_id"], module["version"], " " * 20, 5)
    catalog_reads = len(reads)
    tracemalloc.start()
    started = time.perf_counter()
    query = knowledge.query_expert(
        config, cid, module["module_id"], module["version"], "Figure 1", 5
    )
    elapsed = time.perf_counter() - started
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert query["query_result"]["figures"]
    assert query["query_result"] == script_result
    assert not any(Path(n).suffix in {".png", ".jpeg", ".jpg"} for n in reads)
    reads.clear()
    figure = query["query_result"]["figures"][0]
    with database.connect(config.state_root) as db:
        fetched = expert_media.read(
            config, db, cid, module["module_id"], module["version"], figure["id"]
        )
        assert fetched["status"] == "available", fetched
        assert fetched["data_base64"]
        binary = [n for n in reads if Path(n).suffix == ".png"]
        if output := os.getenv("FIBER_MEDIA_EVIDENCE"):
            target = Path(output)
            target.mkdir(parents=True, exist_ok=True)
            (target / "query.json").write_text(json.dumps(query, ensure_ascii=False, indent=2))
            (target / "media.json").write_text(json.dumps(fetched, ensure_ascii=False, indent=2))
            (target / "read-metrics.json").write_text(
                json.dumps(
                    {
                        "catalog_and_text_read_count": catalog_reads,
                        "text_read_bytes": sum(
                            size for name, size in read_events if Path(name).suffix != ".png"
                        ),
                        "catalog_and_text_binary_reads": 0,
                        "explicit_attachment_binary_reads": len(binary),
                        "host_query_elapsed_seconds": elapsed,
                        "host_query_peak_traced_bytes": peak,
                        "new_expert_runtime_processes": 0,
                    },
                    indent=2,
                )
            )
            for name, raw in module["files"].items():
                path = target / "package" / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(raw)
        assert len(binary) == 1
        db.execute("UPDATE client_vault_grants SET can_original=0 WHERE client_id=?", (cid,))
        reads.clear()
        assert (
            expert_media.read(
                config, db, cid, module["module_id"], module["version"], figure["id"]
            )["status"]
            == "unauthorized"
        )
        assert not any(Path(n).suffix == ".png" for n in reads)
        db.execute("UPDATE client_vault_grants SET can_original=1 WHERE client_id=?", (cid,))
        assert (
            expert_media.read(config, db, cid, module["module_id"], "f" * 64, figure["id"])[
                "status"
            ]
            == "stale"
        )
        assert (
            expert_media.read(
                config, db, cid, module["module_id"], module["version"], "unknown-resource"
            )["status"]
            == "missing"
        )
        location = publication_locations.get(db, "expert", source)
        reader = auth.create_client(db, "source-only reader", False)
        auth.set_grant(db, reader["id"], vid, query=True, original=True, add=False)
        target = db.execute("SELECT * FROM vaults WHERE name='supplier'").fetchone()
        auth.set_grant(db, cid, target["id"], query=True, original=True, add=True)
        source_root = config.project_root / "test vault/temp_vault1"
        target_root = config.project_root / target["relative_path"]
        for relative in location["plan"]["mapping"].values():
            path = target_root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source_root / relative, path)
        db.execute(
            "UPDATE publication_locations SET vault_id=?,revision=revision+1 "
            "WHERE kind='expert' AND publication_id=?",
            (target["id"], source),
        )
        moved = published_expert.text(config, db, cid, source, module["version"])
        assert moved["vault_id"] == target["id"]
        reads.clear()
        assert (
            expert_media.read(
                config, db, reader["id"], module["module_id"], module["version"], figure["id"]
            )["status"]
            == "unauthorized"
        )
        assert not any(Path(n).suffix == ".png" for n in reads)
        fetched = expert_media.read(
            config, db, cid, module["module_id"], module["version"], figure["id"]
        )
        assert fetched["status"] == "available"


def test_unified_hits_deduplicate_and_ordinary_notes_do_not_read_images(
    config, media_package, monkeypatch
):
    cid, vid, source, module = media_package
    root = config.project_root / "test vault/temp_vault1"
    (root / "Book").mkdir(exist_ok=True)
    (root / "Book/ordinary.md").write_text(
        "# Ordinary keyword\nOrdinary needle ![photo](secret.png)\n"
    )
    reads = []
    original = published_expert.read
    monkeypatch.setattr(
        published_expert, "read", lambda root, name: (reads.append(name), original(root, name))[1]
    )
    with database.connect(config.state_root) as db:
        response = evidence_answers.answer(config, db, cid, "needle", "needle", [vid])
        assert "expert_blocks" not in response
        assert reads == []
        (root / "Book/ordinary.md").write_text(
            "# Ordinary keyword\nFigure is ordinary text here.\n"
        )
        response = evidence_answers.answer(config, db, cid, "Figure 1", "Figure", [vid])
        assert len(response["sources"]) >= 2
        assert len(response["expert_blocks"]) == 1
        long_question = " " * 300 + "Figure 1"
        long_response = evidence_answers.answer(config, db, cid, long_question, "Figure", [vid])
        assert long_response["expert_blocks"][0]["media"]
        assert response["expert_blocks"][0]["module_id"] == module["module_id"]
        assert response["expert_blocks"][0]["media"]

        assert sum(s.get("expert_projection", False) for s in response["sources"]) == 1
        assert not any(Path(n).suffix == ".png" for n in reads)
        from fiber_bundle.answer_generation import deliver

        deliver(
            {"mode": "cited_draft"},
            {"mode": "cited_draft", "claims": [], "quotations": response["quotations"]},
            response,
        )
        assert response["expert_blocks"][0]["media"]


def test_unused_24mib_of_images_do_not_increase_text_reads(config, media_package, monkeypatch):
    cid, _, source, module = media_package
    reads = []
    original = published_expert.read

    def track(root, name):
        value = original(root, name)
        reads.append((name, len(value[0])))
        return value

    monkeypatch.setattr(published_expert, "read", track)
    knowledge.query_expert(config, cid, module["module_id"], module["version"], "Figure 1", 5)
    baseline_reads = list(reads)
    root = config.project_root / "test vault/temp_vault1"
    # Extra unrelated publication members simulate a larger valid binary payload.
    # The queried manifest/catalog/script bytes remain exactly unchanged.
    png = next(raw for name, raw in module["files"].items() if name.endswith(".png"))
    raw = png + b"\0" * (4 * 1024 * 1024 - len(png))
    with database.connect(config.state_root) as db:
        location = publication_locations.get(db, "expert", source)
        row = db.execute(
            "SELECT manifest_json FROM source_expert_publications WHERE task_id=?", (source,)
        ).fetchone()
        manifest = json.loads(row[0])
        for number in range(6):
            name = f".fiber-expert-canonical/assets/unused-{number}.png"
            relative = "Attachments/unused-expert/" + str(number) + ".png"
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
            manifest["files"][name] = {"sha256": digest(raw), "byte_count": len(raw)}
            location["plan"]["mapping"][name] = relative
        db.execute(
            "UPDATE source_expert_publications SET manifest_json=? WHERE task_id=?",
            (canonical(manifest), source),
        )

        db.execute(
            "UPDATE publication_locations SET value=? WHERE kind='expert' AND publication_id=?",
            (canonical(location["plan"]), source),
        )
    reads.clear()
    knowledge.query_expert(config, cid, module["module_id"], module["version"], "Figure 1", 5)
    assert reads == baseline_reads
    assert not any(name.endswith(".png") for name, _ in reads)
    if output := os.getenv("FIBER_MEDIA_EVIDENCE"):
        (Path(output) / "large-package-read-metrics.json").write_text(
            json.dumps(
                {
                    "unused_binary_bytes": 24 * 1024 * 1024,
                    "unused_binary_files": 6,
                    "text_read_count_before": len(baseline_reads),
                    "text_read_count_after": len(reads),
                    "text_read_bytes_before": sum(size for _, size in baseline_reads),
                    "text_read_bytes_after": sum(size for _, size in reads),
                    "binary_reads": 0,
                    "scope": "Extra publication members; queried canonical text unchanged",
                },
                indent=2,
            )
        )


def test_figure_only_hit_abstains_and_missing_image_is_isolated(config, media_package):
    cid, _, source, module = media_package
    first, second = module["catalog"]["figures"][:2]
    response = knowledge.query_expert(
        config, cid, module["module_id"], module["version"], first["id"], 5
    )
    assert response["query_result"]["behavior"] == "abstain"
    assert response["query_result"]["matched_object_ids"] == []
    assert response["expert_blocks"][0]["media"][0]["resource_id"] == first["id"]
    with database.connect(config.state_root) as db:
        location = publication_locations.get(db, "expert", source)
        relative = location["plan"]["mapping"][".fiber-expert-canonical/" + first["image"]]
        (config.project_root / "test vault/temp_vault1" / relative).unlink()
        missing = expert_media.read(
            config, db, cid, module["module_id"], module["version"], first["id"]
        )
        assert missing["status"] == "missing"
        present = expert_media.read(
            config, db, cid, module["module_id"], module["version"], second["id"]
        )
        assert present["status"] == "available"
