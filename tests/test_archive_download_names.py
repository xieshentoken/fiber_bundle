"""Synthetic encoding/reference and HTTP filename checks; no changes to real originals."""

import hashlib
import io
import zipfile

from test_api import add_client, permissions, vault_ids

from fiber_bundle import archive_members, feishu_archives
from fiber_bundle.download_names import disposition, filename


def archive(entries):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as stream:
        for name, data in entries:
            stream.writestr(name, data)
    return output.getvalue()


def projection(data):
    assets = feishu_archives.assets(data, "chat.zip")
    rows = [dict(a.descriptor(), id=str(i)) for i, a in enumerate(assets)]
    return archive_members.project(data, rows)


def test_unflagged_utf8_roundtrip_and_exact_original_bytes():
    raw = "微信图片.jpg".encode()
    placeholder = "x" * len(raw)
    txt = "[图片] 微信图片.jpg\n".encode()
    zipped = archive([(placeholder, b"\xff\xd8\xffimage"), ("chat.txt", txt)])
    zipped = zipped.replace(placeholder.encode(), raw)
    view = projection(zipped)
    assert view["members"][0]["encoding"] == "utf-8-roundtrip-unflagged"
    assert view["members"][0]["decoded_path"] == "微信图片.jpg"
    assert view["references"][0]["state"] == "resolved"
    assert view["references"][0]["download_name"] == "zip-member-0-微信图片.jpg"
    assert feishu_archives.assets(zipped, "chat.zip")[2].data == txt
    assert feishu_archives.assets(zipped, "chat.zip")[0].data == zipped
    assert view["chat_message_count"] is None and view["chat_complete"] is None


def test_subdirectory_and_duplicate_basename_never_guess():
    data = archive(
        [
            ("a/图.jpg", b"\xff\xd8\xff1"),
            ("b/图.jpg", b"\xff\xd8\xff2"),
            ("chat.txt", "[图片] 图.jpg\n[图片] a/图.jpg\n".encode()),
        ]
    )
    refs = projection(data)["references"]
    assert [r["state"] for r in refs] == ["needs_review", "resolved"]
    assert refs[0]["asset_id"] is None and refs[1]["asset_id"] == "1"
    assert [
        m["index"] if "index" in m else m["member_index"] for m in projection(data)["members"]
    ] == [0, 1, 2]


def test_unknown_encoding_retained_with_uncertainty_and_safe_headers():
    data = archive([("xx.jpg", b"\xff\xd8\xff")]).replace(b"xx.jpg", b"\xff\xfe.jpg")
    member = projection(data)["members"][0]
    assert member["encoding"] == "cp437-default" and member["uncertain"]
    assert filename("../中文\r\n.bin", "text/plain").endswith(".txt")
    value = disposition(filename("中文.txt", "text/plain"))
    assert "UTF-8''%E4%B8%AD%E6%96%87.txt" in value
    assert value.isascii() and "\r" not in value and "\n" not in value


def test_download_filename_and_projection_for_old_manifest(http, config, store):
    from fiber_bundle import database, operations
    from fiber_bundle.inputs import AssetInput

    cid, headers = add_client(http)
    vault = vault_ids(http)["personal"]
    permissions(http, cid, vault, query=True, original=True, add=True)
    data = archive([("中文.jpg", b"\xff\xd8\xffimage"), ("聊天.txt", "[图片] 中文.jpg".encode())])
    members = feishu_archives.assets(data, "聊天.zip")
    # An old CP437 name is preserved in its immutable manifest.
    old = tuple(
        AssetInput(a.data, a.original_name.encode().decode("cp437"), a.media_type, a.role)
        for a in members
    )
    receipt = store.ingest(
        ingress_scope="synthetic",
        event_id="old",
        vault="personal",
        source_key="old",
        raw_event=b"{}",
        items=old,
        session_scope="synthetic",
        session_id="old",
        retention="knowledge",
    )
    db = database.connect(config.state_root)
    before = db.execute(
        "SELECT manifest_json FROM receipts WHERE job_id=?", (receipt.job_id,)
    ).fetchone()[0]
    view = operations.versions(config, db, cid, vault, 0, 20)[0]
    image = next(a for a in view["assets"] if a["media_type"] == "image/jpeg")
    url = (
        f"/admin/inventory/original/{vault}/{receipt.source_version_id}/"
        f"{image['id']}?client_id={cid}"
    )
    response = http.get(url)
    assert response.status_code == 200 and response.content == members[1].data
    assert response.headers["content-type"] == "image/jpeg"
    assert image["download_name"] == "zip-member-0-中文.jpg"
    assert disposition(image["download_name"]) == response.headers["content-disposition"]
    assert hashlib.sha256(response.content).hexdigest() == image["sha256"]
    assert (
        db.execute(
            "SELECT manifest_json FROM receipts WHERE job_id=?", (receipt.job_id,)
        ).fetchone()[0]
        == before
    )
    permissions(http, cid, vault, query=True, original=False, add=True)
    assert http.get(url).status_code == 404
    db.close()
