"""Configurable ASR/vision through the existing admin and frozen task boundaries."""

import json
import sys

import pytest
from pydantic import ValidationError
from test_video_skill import configured

from fiber_bundle import video_skill


def test_legacy_settings_keep_local_defaults_and_freeze_model_choice(config, tmp_path):
    configured(config, tmp_path)
    before = video_skill.freeze(config)
    value = video_skill.settings(config)
    assert value["asr_model"] == "Qwen/Qwen3-ASR-0.6B"
    assert value["asr_backend"] == "local-qwen3"
    value.update(asr_model="/absolute/model with spaces", asr_language="zh", asr_device="cpu")
    (config.state_root / "configs/video-skill.json").write_text(json.dumps(value))
    after = video_skill.freeze(config)
    assert before["settings"]["asr_model"] == "Qwen/Qwen3-ASR-0.6B"
    assert after["settings"]["asr_model"] == "/absolute/model with spaces"
    assert video_skill.verify(config, before) == video_skill.verify(config, after)


@pytest.mark.parametrize(
    "options",
    [
        {"asr_model": ""},
        {"asr_model": "x\nleak"},
        {"asr_backend": "shell"},
        {"asr_backend": "transcription-api"},
        {"asr_api_reserve_tokens": 0},
        {"vision_model_configuration_id": "bad"},
    ],
)
def test_invalid_model_settings_rejected(options):
    with pytest.raises(ValidationError):
        video_skill.Settings(**options)


def test_local_subprocess_receives_model_language_and_device_without_shell(config, tmp_path):
    skill = configured(config, tmp_path)
    # A fixed synthetic script records argv, with no imports or model dependency.
    (skill / "scripts/run_asr.py").write_text(
        "import sys,json,pathlib\n"
        "pathlib.Path(sys.argv[1]+'/arguments.json').write_text(json.dumps(sys.argv[2:]))\n"
        "raise SystemExit(0)\n"
    )
    value = video_skill.settings(config)
    value.update(
        asr_model="/model/path with spaces",
        asr_device="cpu",
        asr_dtype="float32",
        python_path=sys.executable,
    )
    (config.state_root / "configs/video-skill.json").write_text(json.dumps(value))
    frozen = video_skill.freeze(config)
    job = tmp_path / "job"
    job.mkdir()
    video_skill.run(config, frozen, job, "run_asr", [str(job), "--language", "zh"], lambda: None)
    args = json.loads((job / "arguments.json").read_text())
    assert args == [
        "--language",
        "zh",
        "--model",
        "/model/path with spaces",
        "--device-map",
        "cpu",
        "--dtype",
        "float32",
    ]


def test_admin_config_is_cas_and_read_never_calls_model(config, http, tmp_path, monkeypatch):
    configured(config, tmp_path)
    monkeypatch.setattr(
        "fiber_bundle.model_gateway.complete", lambda *a: pytest.fail("read called model")
    )
    path = "/admin/video-processing/skill"
    snapshot = http.get(path).json()
    assert snapshot["settings"]["asr_model"] == "Qwen/Qwen3-ASR-0.6B"
    request = {
        "expected_sha256": snapshot["sha256"],
        "settings": {
            **snapshot["settings"],
            "asr_model": "/existing/custom/model",
            "asr_language": "zh",
        },
    }
    reply = http.put(path, json=request)
    assert reply.status_code == 200, reply.text
    assert reply.json()["settings"]["asr_model"] == "/existing/custom/model"
    assert http.put(path, json=request).status_code == 422
    assert "api_key" not in reply.text


def test_selected_extraction_models_cannot_be_disabled_until_replaced(
    config, store, tmp_path, monkeypatch
):
    from fiber_bundle import database
    from fiber_bundle.model_configuration import keychain
    from fiber_bundle.model_configuration import store as catalog
    from fiber_bundle.model_configuration.contracts import Change, Save
    from fiber_bundle.model_configuration.errors import ModelError

    keys = {}
    monkeypatch.setattr(keychain, "put", lambda a, k: keys.__setitem__(a, k))
    monkeypatch.setattr(keychain, "get", lambda a: keys[a])
    configured(config, tmp_path)
    with database.connect(config.state_root) as db:
        model = catalog.save(
            config,
            db,
            Save(
                provider="custom",
                name="Synthetic",
                base_url="https://asr.example.org/v1",
                model="custom-transcribe",
                api_key="synthetic-only-key",
            ),
        )
        value = video_skill.settings(config)
        value.update(asr_backend="transcription-api", asr_api_model_configuration_id=model["id"])
        path = config.state_root / "configs/video-skill.json"
        path.write_text(json.dumps(value))
        assert catalog.references(db, model["id"]) == [
            {"kind": "asr", "name": "视频 ASR 转写模型"}
        ]
        with pytest.raises(ModelError, match="referenced"):
            catalog.change(db, model["id"], Change(expected_revision=1, action="disable"))
        value.update(asr_backend="local-qwen3")
        path.write_text(json.dumps(value))
        catalog.change(db, model["id"], Change(expected_revision=1, action="disable"))
