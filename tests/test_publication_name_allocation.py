"""Directory size does not impose a note/image quota; collisions remain exclusive."""

from test_articles import PNG
from test_publication_layout import activate
from test_publication_layout import article_scope as article_scope

from fiber_bundle import database, publication_layout
from fiber_bundle.objects import digest


def populate(path):
    path.mkdir(parents=True, exist_ok=True)
    for i in range(5100):
        (path / f"existing-{i}.png").write_bytes(b"unchanged")


def test_large_directory_keeps_unicode_and_case_collisions(tmp_path):
    populate(tmp_path / "images")
    (tmp_path / "images/CAFÉ.png").write_bytes(b"human image")
    suffix = digest(b"same-origin")[:10]
    result = publication_layout.available(
        tmp_path, "images", "café", ".png", "same-origin", {f"images/café-{suffix}.png"}
    )
    assert result == f"images/café-{suffix}-1.png"
    assert (tmp_path / "images/CAFÉ.png").read_bytes() == b"human image"
    assert len(list((tmp_path / "images").iterdir())) == 5101


def test_real_publication_works_with_more_than_5000_shared_images(config, article_scope):
    activate(config, article_scope[1])
    root = config.project_root / "test vault/temp_vault1"
    populate(root / "images")
    files = {"note.md": b"# Large directory\n![](images/a.png)\n", "images/a.png": PNG}
    with database.connect(config.state_root) as db:
        publication_layout.prepare(config, db, "intake", "large-directory", article_scope[1], files)
        published = publication_layout.publish(
            config, db, "intake", "large-directory", article_scope[0], files
        )
    assert published["entry_note_path"] == "Inbox/Large directory.md"
    assert (root / published["entry_note_path"]).is_file()
    assert len(list((root / "images").iterdir())) == 5101
    assert (root / "images/existing-5099.png").read_bytes() == b"unchanged"
