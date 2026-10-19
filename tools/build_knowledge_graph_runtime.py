"""Build an optional offline context from explicitly supplied, pinned public artifacts."""

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def build(sdk: Path, deps: Path, model: Path, output: Path) -> None:
    from fiber_bundle.config import InvalidInput
    from fiber_bundle.knowledge_graph.contracts import fingerprint
    from fiber_bundle.knowledge_graph.runtime_context import file_hashes

    profile = json.loads((ROOT / "configs/knowledge-graph-runtime.example.json").read_text())
    recipe = ROOT / "src/fiber_bundle/knowledge_executor"
    if output.exists() or output.is_symlink():
        raise InvalidInput("graph_build_output_must_be_new")
    if file_hashes(model) != profile["model_files"]:
        raise InvalidInput("graph_build_model_mismatch")
    if fingerprint(file_hashes(sdk)) != profile.pop("sdk_source_files_sha256"):
        raise InvalidInput("graph_build_sdk_mismatch")
    if hashlib.sha256((recipe / "requirements.lock").read_bytes()).hexdigest() != profile.pop(
        "requirements_sha256"
    ):
        raise InvalidInput("graph_build_lock_mismatch")
    if deps.is_symlink() or not deps.is_dir() or not list(deps.glob("torch-*.dist-info")):
        raise InvalidInput("graph_build_linux_dependencies_required")
    for artifact in (sdk, deps, model):
        if artifact.resolve() == output.resolve() or artifact.resolve() in output.resolve().parents:
            raise InvalidInput("graph_build_output_scope")
    output.mkdir(mode=0o700, parents=True)
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store")
    shutil.copytree(ROOT / "src/fiber_bundle", output / "fiber_bundle", ignore=ignore)
    shutil.copytree(deps, output / "deps", ignore=ignore)
    if (output / "deps/semantica").exists():
        raise InvalidInput("graph_build_dependency_sdk_must_be_separate")
    shutil.copytree(sdk, output / "deps/semantica", ignore=ignore)
    shutil.copytree(model, output / "model", ignore=ignore)
    (output / "context.json").write_text(json.dumps(profile, indent=2))
    shutil.copy2(recipe / "Dockerfile", output / "Dockerfile")
    shutil.copy2(recipe / "SEMANTICA-LICENSE", output / "SEMANTICA-LICENSE")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sdk", type=Path, required=True, help="semantica/ from the pinned upstream source"
    )
    parser.add_argument(
        "--deps", type=Path, required=True, help="Linux aarch64 / Python 3.13 dependency target"
    )
    parser.add_argument(
        "--model", type=Path, required=True, help="the pinned model's ten public files"
    )
    parser.add_argument(
        "--output", type=Path, required=True, help="new private build context; never a Vault"
    )
    arguments = parser.parse_args()
    build(arguments.sdk, arguments.deps, arguments.model, arguments.output)
    print("Graph runtime build context created. No worker started or application policy changed.")
