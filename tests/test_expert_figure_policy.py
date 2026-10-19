"""Pinned profile policy reaches the actual CLI; scan planning never starts OCR."""

import json
import os
import subprocess
from uuid import uuid4

import pytest
from document_support import pdf_bytes

from fiber_bundle import (
    executor_container,
    project_skill_contract,
    project_skill_runtime,
    project_skills,
)

pytest_plugins = ["test_source_imports", "test_execution"]


def test_legacy_and_profile_figure_mode():
    assert project_skill_contract.figure_mode(None, "native-text") is None
    assert project_skill_contract.figure_mode({"schema": "old"}, "scan-text") is None
    binding = {"features": {"direct_compiler": "1.4.0-direct-reference", "figures": "index+images"}}
    assert project_skill_contract.figure_mode(binding, "native-text") == "index+images"
    for profile in ("scan-text", "scan-structure", "visual"):
        assert project_skill_contract.figure_mode(binding, profile) == "none"


@pytest.mark.skipif(not os.getenv("FIBER_PROJECT_SKILL_SOURCE"), reason="fixed Docker required")
def test_native_and_scan_actual_cli_plan(config):
    from test_project_skill_docker import install

    install(config)
    binding = project_skills.resolve(config)
    task = config.state_root / "figure-plan-check"
    task.mkdir()
    (task / "source.pdf").write_bytes(
        pdf_bytes(("1 Plan reference\nBounded synthetic planning source.",))
    )
    image = binding["runtime"]["image"]

    def plan(profile, figures, folder):
        invocation = executor_container.command(image, task, str(uuid4()), network="none")
        pos = invocation.index(image)
        invocation[pos:pos] = [
            "--entrypoint",
            "python",
            *project_skill_runtime.compiler_mounts(config, binding),
        ]
        invocation += [
            "-B",
            "/opt/skill/scripts/fake_expert.py",
            "plan",
            "--source",
            "/task/source.pdf",
            "--workspace",
            "/task/" + folder,
            "--profile",
            profile,
            "--name",
            "plan-contract",
            "--display-name",
            "Plan Contract",
            "--domain",
            "plan-contract",
            "--target-stage",
            "draft",
            "--gold-mode",
            "none",
            "--figures",
            figures,
            "--json",
        ]
        if profile == "scan-text":
            invocation += ["--backend", "pymupdf-tesseract"]
        return subprocess.run(invocation, capture_output=True, timeout=30, check=False)

    for profile, folder in (("native-text", "native"), ("scan-text", "scan")):
        mode = project_skill_contract.figure_mode(binding, profile)
        result = plan(profile, mode, folder)
        assert result.returncode == 0, result.stdout.decode()
        job = json.loads((task / folder / "fake-expert-job.json").read_bytes())
        assert job["package"].get("figures", "none") == mode
        assert job["profile"] == profile
    refused = plan("scan-text", "index+images", "invalid-scan")
    assert refused.returncode != 0
    assert b"figure_packaging_requires_native_text" in refused.stdout + refused.stderr
