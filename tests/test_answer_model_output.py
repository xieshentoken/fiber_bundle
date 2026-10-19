import copy
import json
import sys
from unittest.mock import patch

import pytest
from test_answer_tasks import task as task
from test_execution import policy as policy
from test_session_search import owner as owner

from fiber_bundle import answer_tasks, database, execution, model_gateway
from fiber_bundle.answer_model_output import AnswerOutputRejected, validate_response

EVIDENCE = [{"citation": "[1]", "snippet": "Pressure 100 psi."}]
VALID = {"mode": "extractive", "quotations": [{"citation": "[1]", "quote": "Pressure 100 psi."}]}


def envelope(content):
    return {
        "choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": content}}]
    }


@pytest.mark.parametrize(
    "body,reason",
    [
        (None, "envelope"),
        ({"choices": {}}, "envelope"),
        ({"choices": [None]}, "envelope"),
        ({"choices": [{"finish_reason": "stop", "message": None}]}, "envelope"),
        ({"choices": [{"finish_reason": "length"}]}, "unfinished"),
        ({"choices": [{"finish_reason": "stop", "message": {"role": "user"}}]}, "role"),
        (
            {
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "tool_calls": ["SECRET"]},
                    }
                ]
            },
            "tools",
        ),
        (envelope(None), "content"),
        (envelope("x" * 65537), "content"),
        (envelope("PRIVATE-RAW-RESPONSE"), "json"),
        (envelope('{"mode":"extractive","mode":"extractive","quotations":[]}'), "json"),
        (envelope('{"mode":"extractive","quotations":NaN}'), "json"),
        (envelope("[" * 3000 + "]" * 3000), "schema"),
        (envelope('{"mode":"unexpected","quotations":[]}'), "schema"),
    ],
)
def test_invalid_responses_have_content_free_diagnostics(body, reason):
    with pytest.raises(AnswerOutputRejected) as caught:
        validate_response(body, EVIDENCE)
    assert str(caught.value) == caught.value.code == "answer_output_rejected_" + reason
    assert "SECRET" not in str(caught.value) and "PRIVATE" not in str(caught.value)


@pytest.mark.parametrize(
    "mutation,reason",
    [
        ("unknown", "unknown_citation"),
        ("duplicate", "duplicate_citation"),
        ("changed", "quote_mismatch"),
        ("omit", "omitted_evidence"),
        ("extra", "schema"),
    ],
)
def test_selection_failures_are_distinct(mutation, reason):
    data = copy.deepcopy(VALID)
    if mutation == "unknown":
        data["quotations"][0]["citation"] = "[99]"
    elif mutation == "duplicate":
        data["quotations"].append(data["quotations"][0])
    elif mutation == "changed":
        data["quotations"][0]["quote"] = "SECRET incorrect content"
    elif mutation == "omit":
        data["quotations"] = []
    else:
        data["secret"] = "SECRET"
    with pytest.raises(AnswerOutputRejected) as caught:
        validate_response(envelope(json.dumps(data)), EVIDENCE)
    assert caught.value.code == "answer_output_rejected_" + reason
    assert caught.value.__suppress_context__ is True


def test_valid_exact_quotation_and_empty_evidence_remain_supported():
    assert validate_response(envelope(json.dumps(VALID)), EVIDENCE) == VALID["quotations"]
    assert validate_response(envelope('{"mode":"extractive","quotations":[]}'), []) == []


@pytest.mark.parametrize("schema_failure", [False, True])
def test_rejection_code_persists_without_provider_content(
    config, policy, task, tmp_path, http, owner, schema_failure
):
    rid, _ = task
    runtime = tmp_path / "synthetic_runtime.py"
    runtime.write_text(
        "import json,sys\njson.loads(sys.stdin.readline())\n"
        'print(json.dumps({"type":"model","id":"1","body":{}}),flush=True)\n'
        "sys.stdin.readline()\n"
    )
    body = envelope('{"mode":"extractive","quotations":[{"citation":"[1]","quote":"PRIVATE"}]}')
    if schema_failure:
        body = envelope('{"mode":"extractive","quotations":[],"PRIVATE_FIELD":"PRIVATE"}')
    body["usage"] = {"prompt_tokens": 10, "completion_tokens": 10}
    with (
        patch.object(execution, "command", return_value=[sys.executable, str(runtime)]),
        patch.object(execution, "stop"),
        patch.object(model_gateway, "complete", return_value=body),
    ):
        result = execution.execute(config, policy, rid, "synthetic task")
    assert result["state"] == "failed"
    assert result["error_code"] == "answer_output_rejected_" + (
        "schema" if schema_failure else "quote_mismatch"
    )
    assert result["answer_rejection"] == {
        "code": result["error_code"],
        "schema_issues": [{"path": "$", "kind": "extra_forbidden", "input_type": "string"}]
        if schema_failure
        else [],
    }
    assert "PRIVATE" not in json.dumps(result)
    db = database.connect(config.state_root)
    assert (
        db.execute("SELECT error_code FROM execution_runs WHERE id=?", (rid,)).fetchone()[0]
        == result["error_code"]
    )
    assert answer_tasks.get(db, rid)["result_json"] is None
    assert (
        db.execute("SELECT state FROM model_calls WHERE run_id=?", (rid,)).fetchone()[0]
        == "settled"
    )
    db.close()
    reply = http.get("/v1/answers/" + rid, headers=owner[1])
    assert reply.status_code == 422 and "PRIVATE" not in reply.text
