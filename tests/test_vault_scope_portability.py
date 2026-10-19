"""The inherited local L2 exclusion follows the owner on recipient devices."""

from pathlib import Path

import pytest

from fiber_bundle.config import InvalidInput
from fiber_bundle.vault_scope import PROTECTED, effective


def test_protected_l2_follows_owner_home():
    assert PROTECTED == Path.home() / (
        "Library/Mobile Documents/iCloud~md~obsidian/Documents/L2/"
        "01-Projects/PROTECTED_PROJECT"
    )


@pytest.mark.parametrize("child", ["", "private-note.md", "subfolder/file.md"])
def test_protected_l2_rejected_before_filesystem_access(child):
    with pytest.raises(InvalidInput, match="vault_scope_protected"):
        effective(PROTECTED / child, {"include": [""], "exclude": []})
