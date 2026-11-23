"""Reference reuse is always bound to current source revisions and current names."""

from unittest.mock import patch

import pytest

from fiber_bundle import note_index, note_inventory, note_placement, publication_links
from fiber_bundle.archive_link_planning import Candidates, LinkPlanningSession
from fiber_bundle.config import IntegrityError
from fiber_bundle.note_transfer_links import resolve
from fiber_bundle.vault_scope import Scope


def inventory(root):
    return note_inventory.Inventory(
        root,
        [
            note_index.read_note(root, p.relative_to(root).as_posix(), 10000)
            for p in sorted(root.rglob("*.md"))
        ],
        {},
        {},
        {},
    )


def test_reuse_changes_new_backlinks_and_broken_references(tmp_path):
    root = tmp_path
    (root / "Inbox").mkdir()
    (root / "Resource").mkdir()
    (root / "Inbox/A.md").write_text("# A\n[caption](missing.png)\n")
    (root / "Resource/unrelated.md").write_text("# unrelated\n[[old-gap]]\n")
    session = LinkPlanningSession()
    moves = {"Inbox/A.md": "Resource/A.md"}
    _, issues = note_placement.archive_preflight(
        root, Scope(), inventory(root), moves, session=session
    )
    assert issues == [{"note_path": "Inbox/A.md", "target": "missing.png", "line": 2, "column": 11}]
    (root / "Inbox/A.md").write_text("# A\n")
    with patch.object(publication_links, "references", wraps=publication_links.references) as parse:
        updates, issues = note_placement.archive_preflight(
            root, Scope(), inventory(root), moves, session=session
        )
        assert not issues and not updates
        assert parse.call_count == 1
        (root / "Resource/new.md").write_text("# New\n[[Inbox/A]]\n")
        (root / "Resource/unrelated.md").write_text("# Human relink\n[[Inbox/A]]\n")
        updates, issues = note_placement.archive_preflight(
            root, Scope(), inventory(root), moves, session=session
        )
        assert not issues
        assert {u["path"] for u in updates} == {"Resource/new.md", "Resource/unrelated.md"}
        assert parse.call_count == 3
        (root / "Other").mkdir()
        (root / "Other/A.md").write_text("# Another A")
        (root / "Resource/new.md").write_text("# New\n[[A]]\n")
        _, issues = note_placement.archive_preflight(
            root, Scope(), inventory(root), moves, session=session
        )
        assert any(i["note_path"] == "Resource/new.md" and i["line"] == 2 for i in issues)


def test_source_syntax_and_caption_targets_keep_line_numbers(tmp_path):
    (tmp_path / "A.md").write_text("# A\n[110](上)\n[111](下)\n")
    _, issues = note_placement.archive_preflight(
        tmp_path, Scope(), inventory(tmp_path), {"A.md": "Archive/A.md"}
    )
    assert [(i["target"], i["line"]) for i in issues] == [("上", 2), ("下", 3)]
    (tmp_path / "A.md").write_text("# A\n\n[x](a (nested).md)\n")
    _, issues = note_placement.archive_preflight(
        tmp_path, Scope(), inventory(tmp_path), {"A.md": "Archive/A.md"}
    )
    assert issues[0]["target"] == "unsupported_link_syntax" and issues[0]["line"] == 3


@pytest.mark.parametrize(
    "raw,wiki",
    [
        ("A", True),
        ("A.md", True),
        ("../Inbox/A", True),
        ("../Inbox/A.md", False),
        ("图.png", False),
        ("missing", True),
        ("dup", True),
    ],
)
def test_indexed_candidates_preserve_exact_resolution_and_ambiguity(tmp_path, raw, wiki):
    paths = ["Inbox/A.md", "Resource/图.png", "One/dup.md", "Two/dup.md"]

    def result(candidates):
        try:
            return resolve(raw, tmp_path, "Resource/B.md", candidates, wiki=wiki)
        except IntegrityError as error:
            return str(error)

    assert result(paths) == result(Candidates(paths))


def test_changed_and_deleted_parses_release_their_budget_and_keep_no_traceback(tmp_path):
    (tmp_path / "A.md").write_text("# A\n[x](complex (A).md)")
    session = LinkPlanningSession()
    moves = {"A.md": "Archive/A.md"}
    session.sources(inventory(tmp_path), moves)
    first = session.bytes
    assert session.parsed["A.md"].error_offset == 4
    (tmp_path / "A.md").write_text("# B")
    session.sources(inventory(tmp_path), moves)
    assert session.bytes < first
    assert session.parsed["A.md"].error_offset is None
    (tmp_path / "A.md").unlink()
    session.sources(inventory(tmp_path), moves)
    assert session.bytes == 0 and not session.parsed


def test_cached_namespace_rechecks_new_names_and_target_permissions(tmp_path):
    (tmp_path / "A.md").write_text("[[B]]")
    (tmp_path / "B.md").write_text("# B")
    session = LinkPlanningSession()
    moves = {"A.md": "Archive/A.md"}
    notes = inventory(tmp_path)
    note_placement.archive_preflight(tmp_path, Scope(), notes, moves, session=session)
    (tmp_path / "Other").mkdir()
    (tmp_path / "Other/B.md").write_text("# Another B")
    # Local exact root B remains unambiguous; replacing it with a symlink must be rejected.
    (tmp_path / "B.md").unlink()
    (tmp_path / "B.md").symlink_to(tmp_path / "Other/B.md")
    with pytest.raises(IntegrityError):
        note_placement.archive_preflight(tmp_path, Scope(), notes, moves, session=session)
