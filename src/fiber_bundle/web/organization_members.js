import { joinText, setText, uiText, phrase, msg } from "./i18n/index.js";
import { checkbox, el } from "./ui.js";
import { control } from "./record_ui.js";

export function manualError(code) {
  const key = {
    timeout: "timeout",
    network: "network",
    manual_response_invalid: "invalid_response",
    organization_catalog_pending: "catalog_pending",
    organization_catalog_changed: "catalog_changed",
    organization_moc_unavailable: "catalog_unavailable",
    organization_member_not_visible: "not_visible",
  }[code];
  if (key) return msg("manual_members." + key);
  const message = {
    maintenance_moc_managed_block_edited:
      "Map 系统关联区块内有人工作改动，已保留原文，请先核对该 Map。",
    maintenance_moc_pending_write:
      "Map 有未完成的文件写入，请先恢复对应整理任务。",
    maintenance_moc_changed: "Map 在操作期间发生变化，请刷新后重新选择。",
    note_moc_expert_package_untouched:
      "这是专家包内部文件，请在设置中扫描 Book，并对整包增挂 MOC。",
    note_moc_source_changed: "笔记原文已变化，请刷新后重新选择。",
  }[code];
  return message ? uiText(message) : msg("manual_members.incomplete_help");
}

export function renderMemberResult(host, result, refresh, unresolved = []) {
  host.replaceChildren();
  host.hidden = !result && !unresolved.length;
  for (const [index, operation] of [result, ...unresolved]
    .filter(Boolean)
    .entries()) {
    const section = el("section");
    appendMemberResult(section, operation, refresh, index > 0);
    host.append(section);
  }
}

function appendMemberResult(host, result, refresh, earlier) {
  host.append(
    el("h3", msg("manual_members." + (earlier ? "unresolved" : "recent"))),
    el("p", joinText([result.entity.title, " · ", result.entity.path])),
    el(
      "p",
      uiText(
        {
          link: "人工增挂并锁定",
          unlink: "人工移除并阻止自动加回",
          unlock: "解除所选关系的人工限制",
        }[result.action],
      ),
    ),
    el("p", msg("manual_members." + result.phase)),
  );
  for (const row of result.results || result.targets)
    host.append(
      el(
        "p",
        joinText([
          row.title,
          "：",
          msg("manual_members.item_" + (row.state || "pending")),
          row.state && row.state !== "completed" ? " · " : "",
          row.state && row.state !== "completed" ? manualError(row.error) : "",
        ]),
      ),
    );
  if (result.resolved)
    host.append(el("p", uiText("原整理任务中的对应项已标记为已人工处理。")));
  if (result.phase === "unknown")
    host.append(el("p", msg("manual_members.unknown_help"), "task-impact"));
  const verify = control("刷新核对挂载结果", refresh);
  verify.disabled = result.phase === "pending";
  verify.dataset.policyDisabled = String(verify.disabled);
  host.append(verify);
}

// A card owns presentation; its selection and request state belong to the session.
export function memberControls(item, mocs, session) {
  const state = session.entry(item, mocs),
    host = el("div"),
    picker = el("details"),
    summary = el("summary"),
    list = el("div"),
    search = el("input"),
    buttons = [];
  host.dataset.manualEntity = item.id;
  host.dataset.draft = "ignore";
  picker.open = state.open;
  picker.addEventListener("toggle", () => {
    state.open = picker.open;
  });
  picker.append(summary, list);
  search.value = state.search;
  setText(search, uiText("筛选 MOC"), "placeholder");
  setText(search, uiText("筛选 MOC"), "aria-label");
  list.append(search);
  const choices = mocs.map((moc) => {
    const box = checkbox(
      joinText([
        moc.title,
        item.moc_ids.includes(moc.id) ? uiText("（已挂载）") : "",
      ]),
      state.selected.has(moc.id),
    );
    box.input.addEventListener("change", () => {
      session.update(state, () => {
        if (box.input.checked) state.selected.add(moc.id);
        else state.selected.delete(moc.id);
      });
      update();
    });
    list.append(box.label);
    return { moc, ...box };
  });
  search.addEventListener("input", () => {
    session.update(state, () => {
      state.search = search.value;
    });
  });
  host.append(
    picker,
    el(
      "p",
      uiText(
        "可同时勾选多个 MOC；增挂保留已有分类，移除和解除限制仅作用于所选关系。",
      ),
    ),
  );
  for (const [action, label] of [
    ["link", "人工增挂并锁定"],
    ["unlink", "人工移除并阻止自动加回"],
    ["unlock", "解除所选关系的人工限制"],
  ]) {
    const button = control(label, () => session.apply(state, action));
    buttons.push(button);
    host.append(button);
  }
  const reset = control(msg("manual_members.clear_selection"), () =>
    session.clearSelection(state),
  );
  host.append(reset);
  function update() {
    const blocked = !session.editable(state);
    for (const node of [
      ...buttons,
      search,
      ...choices.map((box) => box.input),
    ]) {
      node.disabled = blocked;
      node.dataset.policyDisabled = String(blocked);
    }
    reset.disabled = session.busy();
    reset.dataset.policyDisabled = String(reset.disabled);
    for (const { moc, label, input } of choices) {
      input.checked = state.selected.has(moc.id);
      label.hidden = !moc.title
        .toLowerCase()
        .includes(state.search.toLowerCase());
    }
    setText(
      summary,
      state.selected.size
        ? phrase("已选择 {p0} 个 MOC（可多选）", { p0: state.selected.size })
        : uiText("选择 MOC（可多选）"),
    );
  }
  state.update = update;
  update();
  return host;
}
