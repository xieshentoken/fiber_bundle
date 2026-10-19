"""Schema rejection exposes only known paths and categories, never model content."""

import json

import pytest

from fiber_bundle.answer_model_output import AnswerOutputRejected, validate_response


def rejected(value):
    body = {
        "choices": [
            {
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": json.dumps(value)},
            }
        ]
    }
    with pytest.raises(AnswerOutputRejected) as caught:
        validate_response(body, [])
    assert caught.value.code == "answer_output_rejected_schema"
    assert str(caught.value) == caught.value.code
    assert "PRIVATE" not in json.dumps(caught.value.schema_issues)
    return caught.value.schema_issues


@pytest.mark.parametrize(
    "value,path,kind,input_type",
    [
        ([], "$", "model_type", "array"),
        (None, "$", "model_type", "null"),
        ({"quotations": []}, "$.mode", "missing", "absent"),
        ({"mode": "PRIVATE"}, "$.mode", "literal_error", "string"),
        ({"mode": "extractive"}, "$.quotations", "missing", "absent"),
        ({"mode": "extractive", "quotations": "PRIVATE"}, "$.quotations", "list_type", "string"),
        (
            {"mode": "extractive", "quotations": ["PRIVATE"]},
            "$.quotations[]",
            "model_type",
            "string",
        ),
        ({"mode": "extractive", "quotations": [{}]}, "$.quotations[].quote", "missing", "absent"),
        (
            {"mode": "extractive", "quotations": [{"citation": 123, "quote": True}]},
            "$.quotations[].citation",
            "string_type",
            "number",
        ),
        (
            {"mode": "extractive", "quotations": [{"citation": "[1]", "quote": True}]},
            "$.quotations[].quote",
            "string_type",
            "boolean",
        ),
        (
            {"mode": "extractive", "quotations": [{"citation": "x", "quote": ""}]},
            "$.quotations[].quote",
            "string_too_short",
            "string",
        ),
        (
            {"mode": "extractive", "quotations": [{"citation": "[1]", "quote": "PRIVATE" * 60}]},
            "$.quotations[].quote",
            "string_too_long",
            "string",
        ),
        (
            {"mode": "extractive", "quotations": [], "PRIVATE_FIELD": "PRIVATE"},
            "$",
            "extra_forbidden",
            "string",
        ),
        (
            {
                "mode": "extractive",
                "quotations": [
                    {"citation": "[1]", "quote": "q", "PRIVATE_FIELD": {"PRIVATE": "PRIVATE"}}
                ],
            },
            "$.quotations[]",
            "extra_forbidden",
            "object",
        ),
        ({"mode": "extractive", "quotations": [{}] * 21}, "$.quotations", "too_long", "array"),
    ],
)
def test_whitelisted_schema_issues(value, path, kind, input_type):
    assert {"path": path, "kind": kind, "input_type": input_type} in rejected(value)


def test_duplicate_issue_locations_do_not_export_indices_or_unbounded_extras():
    value = {
        "mode": "extractive",
        "quotations": [{"PRIVATE_FIELD_" + str(i): "PRIVATE"} for i in range(20)],
    }
    issues = rejected(value)
    assert issues == [
        {"path": "$.quotations[].citation", "kind": "missing", "input_type": "absent"},
        {"path": "$.quotations[].quote", "kind": "missing", "input_type": "absent"},
        {"path": "$.quotations[]", "kind": "extra_forbidden", "input_type": "string"},
    ]


def test_arbitrary_json_keys_cannot_masquerade_as_contract_paths():
    issues = rejected({"mode": "extractive", "quotations": [], "$.quotations[].quote": "PRIVATE"})
    assert issues == [{"path": "$", "kind": "extra_forbidden", "input_type": "string"}]


@pytest.mark.parametrize(
    "content,kind",
    [
        ("PRIVATE invalid JSON", "syntax"),
        ('  ```json\n{"PRIVATE":"PRIVATE"}\n```', "markdown_fence"),
        ('{"PRIVATE":"PRIVATE","PRIVATE":"PRIVATE"}', "duplicate_member"),
        ('{"PRIVATE":NaN}', "nonstandard_constant"),
        ('{"PRIVATE":Infinity}', "nonstandard_constant"),
        ('{"PRIVATE":' + "1" * 5000 + "}", "syntax"),
        ("[" * 10000 + "]" * 10000, "nesting_limit"),
    ],
)
def test_json_failure_categories_are_content_free(content, kind):
    body = {
        "choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": content}}]
    }
    with pytest.raises(AnswerOutputRejected) as caught:
        validate_response(body, [])
    assert caught.value.code == "answer_output_rejected_json"
    assert caught.value.json_issue == kind
    assert caught.value.schema_issues == []
    assert "PRIVATE" not in str(caught.value) + json.dumps(caught.value.__dict__)


def test_extractive_prompt_defines_valid_shape_without_changing_evidence():
    from fiber_bundle.answer_generation import model_request

    quotes = [{"citation": "[1]", "quote": "supplied original"}]
    value = model_request({"mode": "extractive"}, {"question": "question", "quotations": quotes})
    system = value["messages"][0]["content"]
    example = system.split("The object must match this example: ", 1)[1].split(". ", 1)[0]
    from fiber_bundle.answer_contract import validate_selection

    assert validate_selection(
        json.loads(example), [{"citation": "[1]", "snippet": "supplied exact quote"}]
    )
    assert json.loads(value["messages"][1]["content"]) == {
        "question": "question",
        "quotations": quotes,
    }
