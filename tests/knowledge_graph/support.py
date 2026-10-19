import hashlib
import json
from dataclasses import asdict
from pathlib import Path

from fiber_bundle import auth, database, note_identities
from fiber_bundle.knowledge_graph.contracts import SCHEMA, Request
from fiber_bundle.knowledge_graph.intake import Selection
from fiber_bundle.knowledge_graph.results import Span, entity_id, line_at, relation_id

FIXTURE = Path(__file__).parent / "fixtures/s1_corpus.json"


def build_corpus(config):
    data = json.loads(FIXTURE.read_text())
    database.initialize(config)
    db = database.connect(config.state_root)
    client = auth.create_client(db, "synthetic graph corpus", False)
    vaults = {row["name"]: row["id"] for row in db.execute("SELECT * FROM vaults")}
    auth.set_grant(db, client["id"], vaults["personal"], query=True, original=False, add=True)
    db.close()
    root = config.project_root / "test vault/temp_vault1"
    authorization = "Bearer " + client["token"]
    selected = []
    for sample in data["notes"]:
        path = root / sample["path"]
        assert not path.exists()
        path.write_text(sample["text"])
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        tracked = note_identities.run(
            config, authorization, vaults["personal"], "track", sample["path"], sha
        )
        selected.append(Selection(tracked["note_identity"], sha))
    return authorization, vaults, root, tuple(selected), data


def span(note, quote):
    start = note.text.index(quote)
    end = start + len(quote)
    return Span(
        note.note_id,
        note.sha256,
        start,
        end,
        line_at(note.text, start),
        line_at(note.text, end - 1),
        quote,
    )


def result(request: Request, *, with_entities=True):
    notes = [note for note in request.notes if "青松供应商的额定压力为" in note.text]
    first = notes[0] if notes else request.notes[0]
    label = "青松" if "青松" in first.text else first.title
    mention = span(first, label)
    entity = {"id": entity_id(label, mention), "label": label, "evidence": asdict(mention)}
    return {
        "schema": SCHEMA,
        "task_id": request.task_id,
        "input_sha256": request.input_sha256,
        "configuration_sha256": request.configuration_sha256,
        "engine": {
            "name": request.engine.name,
            "version": request.engine.version,
            "revision": request.engine.revision,
        },
        "entities": [entity] if with_entities else [],
        "relations": [],
        "mocs": [],
    }


def related_result(request):
    value = result(request)
    note = next(note for note in request.notes if "青松供应商的额定压力为" in note.text)
    evidence = span(note, "2.4 MPa")
    second = {
        "id": entity_id("2.4 MPa", evidence),
        "label": "2.4 MPa",
        "evidence": asdict(evidence),
    }
    value["entities"].append(second)
    statement = span(note, "青松供应商的额定压力为2.4 MPa，材料型号AB-12。")
    source = value["entities"][0]["id"]
    value["relations"] = [
        {
            "id": relation_id(source, second["id"], "source_explicit", (statement,)),
            "source": source,
            "target": second["id"],
            "kind": "source_explicit",
            "derivation": "source_statement",
            "method": request.engine.algorithm,
            "score": None,
            "score_kind": "unscored",
            "evidence": [asdict(statement)],
        }
    ]
    return value
