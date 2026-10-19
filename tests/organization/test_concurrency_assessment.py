"""Assess the existing single-consumer queue; never enable parallel production workers."""

import copy
import threading
from types import SimpleNamespace

from test_jobs import ready as ready

from fiber_bundle import database
from fiber_bundle.organization import (
    maintenance,
    maintenance_analysis,
    model_review,
    semantic,
    store,
)


def test_two_existing_workers_choose_the_same_item_so_default_stays_serial(
    config, ready, monkeypatch
):
    cid, vid, root, _, note = ready
    body = (
        "正文说明材料与制造过程，包含独立的完整观察结果。\n另一个段落用于验证分类覆盖与保存边界。\n"
    )
    note.write_text("# 资料甲\n" + body)
    (root / "Inbox/资料乙.md").write_text("# 资料乙\n" + body)
    monkeypatch.setattr(model_review, "route", lambda *a: SimpleNamespace(digest="fixed"))
    monkeypatch.setattr(semantic, "recall", lambda *a: {"state": "disabled"})
    with database.connect(config.state_root) as db:
        job = maintenance.submit(config, db, cid, vid, "additive")
    assert len(job["items"]) == 2 and not any(i["evaluated"] for i in job["items"])
    calls, errors = [], []
    barrier = None

    def resolve(config, job, entity, mocs, item, check):
        check()
        calls.append(entity["id"])
        if barrier is not None:
            barrier.wait(5)
        return {"selected": [], "model_called": False}

    monkeypatch.setattr(model_review, "resolve", resolve)
    for _ in range(2):
        maintenance_analysis.prepare(config, job["id"])
    assert len(set(calls)) == 2
    with database.locked(config):
        store.put(config, "jobs", job["id"], vid, copy.deepcopy(job))
    calls.clear()
    barrier = threading.Barrier(2)

    def run():
        try:
            maintenance_analysis.prepare(config, job["id"])
        except Exception as error:
            errors.append(error)

    threads = [threading.Thread(target=run) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(8)
    assert not any(t.is_alive() for t in threads) and not errors
    assert len(calls) == 2 and len(set(calls)) == 1
    current = store.get(config, "jobs", job["id"])
    assert sum(i["evaluated"] for i in current["items"]) == 1
    assert not current["confirmed"]
