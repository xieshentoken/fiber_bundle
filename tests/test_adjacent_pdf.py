"""A candidate query preserves physical page and literal adjacent-line offsets."""

from unittest.mock import patch

import test_document_evidence as fixture
from test_document_evidence import document_scope as document_scope
from test_document_evidence import policy as policy
from test_document_tasks import call, start

from fiber_bundle import auth, database, document_evidence, document_worker


def test_pdf_adjacent_line_pressure_and_value(config, document_scope, policy):
    text = (
        "测试压力\r\n2.4 MPa\r\n"
        "其他 contextual native candidate evidence long enough for usable text"
    )
    rid = start(config, policy, document_scope)
    with (
        patch.object(fixture, "TEXT", text),
        patch.object(document_worker, "execute_document", side_effect=fixture.multipage_worker),
    ):
        call(config, policy, rid)
    db = database.connect(config.state_root)
    auth.set_grant(db, document_scope[0], document_scope[1], query=True, original=True, add=True)
    db.execute("UPDATE execution_runs SET state='succeeded' WHERE id=?", (rid,))
    found = document_evidence.search(config, db, document_scope[0], rid, "测试压力 2.4")
    db.close()
    hit = found["hits"][0]
    assert hit["physical_page"] == 1
    assert (hit["line"], hit["line_end"]) == (1, 2)
    assert hit["snippet"] == "测试压力\r\n2.4 MPa"
    assert text[hit["start_char"] : hit["end_char"]] == hit["snippet"]
    assert len(hit["snippet"]) <= 400
