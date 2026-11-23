"""Native provider payloads retain actual image input and reject unusable crops."""

from types import SimpleNamespace

import pytest

from fiber_bundle.config import InvalidInput
from fiber_bundle.inputs import canonical
from fiber_bundle.model_configuration.protocols import prepare
from fiber_bundle.video_skill_model import validate_visual, vision_payload

pytest_plugins = ["test_execution"]


@pytest.mark.parametrize(
    "protocol", ["openai-completions", "openai-responses", "anthropic-messages"]
)
def test_registered_protocol_retains_actual_frame(protocol):
    policy = SimpleNamespace(
        protocol=protocol,
        model="synthetic",
        max_output_tokens=1000,
        max_request_bytes=100000,
        effort="default",
    )
    value = prepare(policy, vision_payload(policy, "inspect this frame", b"synthetic-image"))
    assert "c3ludGhldGljLWltYWdl" in canonical(value)
    assert "inspect this frame" in canonical(value)
    assert value["model"] == "synthetic"


@pytest.mark.parametrize("box", [[0, 0, 1, 1], [1, 0, 0, 1], [0, 0, float("nan"), 1]])
def test_invalid_or_full_frame_crop_cannot_be_treated_as_success(box):
    with pytest.raises(InvalidInput, match="visual_response_invalid"):
        validate_visual(
            {
                "analysis_status": "analyzed",
                "illustrations": [{"kind": "diagram", "caption": "diagram", "bbox_norm": box}],
            },
            [],
        )


def test_prompt_lists_the_exact_skip_contract_instead_of_an_undefined_reason():
    from fiber_bundle.video_visual_contract import SKIP_REASONS, prompt

    text = prompt(visual_primary=True)
    for reason in SKIP_REASONS:
        assert reason in text
    assert '"illustrations":[]' in text
    assert "Do not infer narration" in text


@pytest.mark.parametrize("reason", [None, "title_card", "", "unknown"])
def test_unsupported_skip_reason_stays_rejected(reason):
    from fiber_bundle.video_visual_contract import VisualContractError

    with pytest.raises(VisualContractError) as caught:
        validate_visual(
            {
                "analysis_status": "not_needed",
                "skip_reason_code": reason,
                "review_note": "Opening frame",
                "illustrations": [],
            },
            [],
        )
    assert caught.value.field == "skip_reason_code"


def test_documented_transition_and_informative_frame_without_crop_are_valid():
    validate_visual(
        {
            "analysis_status": "not_needed",
            "skip_reason_code": "transition",
            "review_note": "Blank transition",
            "illustrations": [],
        },
        [],
    )
    validate_visual({"analysis_status": "analyzed", "illustrations": []}, [])


@pytest.mark.parametrize("content", ["", '{"summary":', '{"analysis_status":"analyzed"}'])
def test_truncated_reply_is_never_accepted_even_when_json_looks_complete(content):
    from fiber_bundle.video_visual_contract import VisualContractError, json_reply

    with pytest.raises(VisualContractError, match="visual_output_truncated") as caught:
        json_reply(
            {
                "choices": [
                    {
                        "finish_reason": "length",
                        "message": {
                            "content": content,
                            "reasoning_content": "Must not become source evidence",
                        },
                    }
                ]
            }
        )
    assert caught.value.field == "finish_reason"


@pytest.mark.parametrize("content", [None, "", "   "])
def test_empty_final_answer_has_its_own_error(content):
    from fiber_bundle.video_visual_contract import VisualContractError, json_reply

    with pytest.raises(VisualContractError, match="visual_response_empty"):
        json_reply({"choices": [{"finish_reason": "stop", "message": {"content": content}}]})


def test_complete_frame_json_keeps_strict_parser_and_crop_checks():
    from fiber_bundle.video_visual_contract import VisualContractError, json_reply

    row = {"analysis_status": "analyzed", "illustrations": []}
    assert json_reply({"choices": [{"message": {"content": canonical(row)}}]}) == row
    with pytest.raises(VisualContractError, match="visual_response_invalid"):
        json_reply({"choices": [{"message": {"content": '{"partial":'}}]})


def test_frame_capacity_is_independent_of_saved_policy_and_other_models(policy):
    from dataclasses import asdict, replace

    from fiber_bundle.model_configuration.policy import RegisteredPolicy
    from fiber_bundle.video_model_policy import vision_policy

    values = asdict(policy)
    values.update(model="deepseek-flash", max_output_tokens=4096)
    saved = RegisteredPolicy(**values, provider="deepseek")
    actual = vision_policy(saved)
    assert actual.max_output_tokens == 16384
    assert replace(actual, max_output_tokens=4096) == saved
    assert vision_policy(actual) is actual
    other = replace(saved, provider="custom")
    assert vision_policy(other) is other
    larger = replace(saved, max_output_tokens=32768)
    assert vision_policy(larger) is larger
