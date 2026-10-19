"""Only affected archival references block; migration defaults remain conservative."""

from fiber_bundle import publication_links


def test_unrelated_broken_links_do_not_block_stationary_notes(tmp_path):
    text = "![old](missing.png)\n[[missing-note]]\n[n](Inbox/actual.md)\n"
    moves = {"Inbox/actual.md": "Resource/actual.md"}
    after, missing = publication_links.relocate(
        text, tmp_path, "Index.md", "Index.md", list(moves), moves, affected_only=True
    )
    assert not missing and "Resource/actual.md" in after and "missing.png" in after
    assert publication_links.relocate(text, tmp_path, "Index.md", "Index.md", list(moves), moves)[1]


def test_moved_note_still_blocks_missing_dependencies(tmp_path):
    _, missing = publication_links.relocate(
        "![x](missing.png)",
        tmp_path,
        "Inbox/actual.md",
        "Resource/actual.md",
        ["Inbox/actual.md"],
        {"Inbox/actual.md": "Resource/actual.md"},
        affected_only=True,
    )
    assert missing == ["missing.png"]


def test_ambiguous_inbound_moved_name_is_not_ignored(tmp_path):
    _, missing = publication_links.relocate(
        "[[actual]]",
        tmp_path,
        "Index.md",
        "Index.md",
        ["Inbox/actual.md", "Other/actual.md"],
        {"Inbox/actual.md": "Resource/actual.md"},
        affected_only=True,
    )
    assert missing == ["actual"]


def test_unrelated_unsupported_markdown_does_not_block_but_affected_unknown_does(tmp_path):
    from types import SimpleNamespace

    from fiber_bundle import note_placement
    from fiber_bundle.vault_scope import Scope

    path = tmp_path / "Index.md"
    text = "[old](bad (syntax))\n"
    path.write_text(text)
    note = SimpleNamespace(relative_path="Index.md", text=text)
    inventory = SimpleNamespace(notes=[note])
    moves = {"Inbox/actual.md": "Resource/actual.md"}
    assert note_placement.plan_links(tmp_path, Scope(), inventory, moves, affected_only=True) == (
        [],
        [],
    )
    note.text = text + "[[actual]]\n"
    path.write_text(note.text)
    assert note_placement.plan_links(tmp_path, Scope(), inventory, moves, affected_only=True)[1]


def test_common_package_names_in_other_folders_are_not_inbound_links():
    assert not publication_links.may_reference_moved(
        "Read SKILL.md or [bad](bad (syntax))",
        "Other/Guide.md",
        {"Inbox/Draft/SKILL.md": "Book/Draft/SKILL.md"},
    )
    assert publication_links.may_reference_moved(
        "[skill](../Inbox/Draft/SKILL.md)",
        "Other/Guide.md",
        {"Inbox/Draft/SKILL.md": "Book/Draft/SKILL.md"},
    )
