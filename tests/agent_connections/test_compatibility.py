import pytest

from fiber_bundle.agent_connections.hermes_compatibility import FILES, REQUIRED_ARGUMENTS, inspect
from fiber_bundle.config import InvalidInput


@pytest.fixture
def checkout(tmp_path):
    root = (tmp_path / "synthetic-hermes").resolve()
    for name in FILES:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# Synthetic source, never imported\n")
    (root / "pyproject.toml").write_text('[project]\nname="hermes-agent"\nversion="synthetic"\n')
    (root / "run_agent.py").write_text(
        "raise RuntimeError('must not import')\nclass AIAgent:\n"
        + " def __init__(self, "
        + ",".join(sorted(REQUIRED_ARGUMENTS))
        + "): pass\n"
        + " def run_conversation(self, user_message, system_message, conversation_history): pass\n"
        + " def close(self): pass\n"
    )
    return root


def test_static_candidate_is_never_runtime_qualification(checkout):
    result = inspect(checkout)
    assert result["state"] == "candidate" and result["runtime_verified"] is False
    assert result["configuration_read"] is False and result["model_called"] is False
    assert set(result["source_files"]) == set(FILES)
    (checkout / "agent/turn_context.py").write_text("# changed public source\n")
    assert inspect(checkout)["source_sha256"] != result["source_sha256"]


def test_missing_required_signature_is_incompatible(checkout):
    source = checkout / "run_agent.py"
    source.write_text(source.read_text().replace("base_url", "unknown"))
    assert inspect(checkout)["state"] == "incompatible"


@pytest.mark.parametrize("case", ["symlink", "missing", "syntax", "oversize"])
def test_invalid_installations_do_not_get_qualified(checkout, tmp_path, case):
    source = checkout / "run_agent.py"
    if case == "symlink":
        outside = tmp_path / "outside.py"
        outside.write_bytes(source.read_bytes())
        source.unlink()
        source.symlink_to(outside)
    elif case == "missing":
        source.unlink()
    elif case == "syntax":
        source.write_text("invalid !!!")
    else:
        source.write_bytes(b"x" * 2097153)
    with pytest.raises(InvalidInput):
        inspect(checkout)
