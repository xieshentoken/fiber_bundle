import pytest
from pydantic import ValidationError

from fiber_bundle.organization.contracts import Policy


def test_legacy_defaults_are_disabled_and_unlimited():
    p = Policy()
    assert p.mode == "off" and not p.periodic and not p.full_review
    assert "limit" not in p.model_dump()


@pytest.mark.parametrize("path", ["../x", "/x", "a/../b", "a\\b"])
def test_scope_refuses_traversal(path):
    with pytest.raises(ValidationError):
        Policy(include=[path])


def test_policy_conditions_are_explicit():
    p = Policy(mode="automatic", match="any", conditions=[{"field": "tag", "values": ["thermal"]}])
    assert p.conditions[0].field == "tag"
