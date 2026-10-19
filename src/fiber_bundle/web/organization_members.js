import { joinText, setText, uiText, phrase } from "./i18n/index.js";
// Explicit manual selections are additive; existing membership stays visible.
import { checkbox, el } from "./ui.js";
import { control } from "./record_ui.js";

export function manualError(code) {
  return (
    {
      timeout:
        "等待挂载结果超时，后台可能仍在执行。请先刷新核对所选 MOC，不要立即重复提交。",
      network:
        "连接中断，暂时无法确认挂载结果。后台可能仍在执行，请先刷新核对所选 MOC。",
      maintenance_moc_managed_block_edited:
        "Map 系统关联区块内有人工作改动，已保留原文，请先核对该 Map。",
      maintenance_moc_pending_write:
        "Map 有未完成的文件写入，请先恢复对应整理任务。",
      maintenance_moc_changed: "Map 在操作期间发生变化，请刷新后重新选择。",
      note_moc_expert_package_untouched:
        "这是专家包内部文件，请在设置中扫描 Book，并对整包增挂 MOC。",
      note_moc_source_changed: "笔记原文已变化，请刷新后重新选择。",
      organization_member_not_visible:
        "笔记或 MOC 已变化，或不在当前权限范围内，请刷新。",
    }[code] || "写入未完成，已保留现有关系与资料；可重试所选 MOC 以完成同步。"
  );
}

export function memberControls(item, mocs, apply, refresh) {
  const host = el("div"),
    picker = el("details"),
    summary = el("summary", uiText("选择 MOC（可多选）")),
    list = el("div"),
    selected = new Set();
  picker.append(summary, list);
  const search = el("input");
  setText(search, uiText("筛选 MOC"), "placeholder");
  setText(search, uiText("筛选 MOC"), "aria-label");
  list.append(search);
  const choices = mocs.map((moc) => {
    const box = checkbox(
      joinText([
        moc.title,
        item.moc_ids.includes(moc.id) ? uiText("（已挂载）") : "",
      ]),
      false,
    );
    box.input.addEventListener("change", () => {
      if (box.input.checked) selected.add(moc.id);
      else selected.delete(moc.id);
      setText(
        summary,
        selected.size
          ? phrase("已选择 {p0} 个 MOC（可多选）", { p0: selected.size })
          : uiText("选择 MOC（可多选）"),
      );
    });
    list.append(box.label);
    return { moc, label: box.label };
  });
  search.addEventListener("input", () => {
    for (const { moc, label } of choices)
      label.hidden = !moc.title
        .toLowerCase()
        .includes(search.value.toLowerCase());
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
  const status = el("p");
  status.setAttribute("role", "status");
  status.setAttribute("aria-live", "polite");
  host.append(status);
  const buttons = [];
  const verify = control("刷新核对挂载结果", async () => {
    await refresh();
  });
  verify.hidden = true;
  for (const [action, label] of [
    ["link", "人工增挂并锁定"],
    ["unlink", "人工移除并阻止自动加回"],
    ["unlock", "解除所选关系的人工限制"],
  ]) {
    const button = control(label, async (notify) => {
      if (!selected.size) throw new Error("请先勾选一个或多个 MOC。");
      let uncertain = false;
      for (const b of buttons) b.disabled = true;
      setText(
        status,
        uiText("正在逐项同步所选 MOC，请等待结果；请勿重复提交。"),
      );
      try {
        const result = await apply([...selected], action);
        const failed = result.results.filter((r) => !r.completed);
        const done = result.results.length - failed.length;
        const feedback = failed.length
          ? `已完成 ${done} 项；` +
            failed.map((r) => `${r.title}：${manualError(r.error)}`).join("；")
          : `已完成 ${done} 个 MOC 的关系调整，关系与 Map 已同步。` +
            (result.resolved_items
              ? "原整理任务中的对应项已标记为已人工处理。"
              : "");
        setText(status, uiText(feedback));
        notify(feedback, !!failed.length);
      } catch (error) {
        uncertain = ["timeout", "network"].includes(error.code);
        setText(status, uiText(manualError(error.code)));
        verify.hidden = !uncertain || !refresh;
        if (error.code)
          throw new Error(manualError(error.code), { cause: error });
        throw error;
      } finally {
        for (const b of buttons) {
          b.dataset.policyDisabled = String(uncertain);
          b.disabled = uncertain;
        }
      }
    });
    buttons.push(button);
    host.append(button);
  }
  host.append(verify);
  return host;
}
