// Partial-scope choices and separate link-repair confirmation; no automatic writes.
import { el } from "./ui.js";
import { control } from "./record_ui.js";
import { disclosure } from "./view_sections.js";
import { phrase, uiText, setText } from "./i18n/index.js";

export function unavailableChoice(draft) {
  const label = el("label", uiText("存在不可读笔记时")),
    select = el("select");
  setText(select, uiText("存在不可读笔记时"), "aria-label");
  for (const [key, title] of Object.entries({
    moc_only: "只挂载 MOC，延后归档（默认）",
    defer_links: "继续归档，保留待核对链接",
  })) {
    const option = el("option", uiText(title));
    option.value = key;
    select.append(option);
  }
  select.value = draft.unavailableAction || "moc_only";
  select.addEventListener("change", () => {
    draft.unavailableAction = select.value;
  });
  label.append(
    select,
    el(
      "small",
      uiText(
        "不可读笔记均跳过。继续归档可能留下旧链接；恢复可读后需预览并确认修复。",
      ),
    ),
  );
  return label;
}

export function partialTaskStatus(job, fixed, api, refresh) {
  const host = el("div", null, "task-impact");
  if (job.deferred_archives)
    host.append(
      el(
        "p",
        phrase("本次挂载 MOC；{p0} 项归档延后，文件保持原位置。", {
          p0: job.deferred_archives,
        }),
      ),
    );
  if (
    job.coverage &&
    job.unavailable_action === "defer_links" &&
    (job.state !== "completed" || job.links_pending)
  )
    host.append(
      el(
        "p",
        uiText(
          "本次允许继续归档，未读笔记的引用保留待核对，不能视为全部链接已验证。",
        ),
      ),
    );
  if (!job.links_pending || job.state !== "completed") return host;
  const result = el("div");
  host.append(
    el("p", uiText("归档已完成，仍有链接待核对。")),
    control("预览待核对链接", async () => {
      const plan = await api.repairPreview(job.id, fixed);
      result.replaceChildren();
      for (const [old, current] of Object.entries(plan.file_mapping))
        result.append(el("p", old + " → " + current));
      if (plan.remaining.length)
        result.append(
          el(
            "p",
            phrase("仍有 {p0} 篇未就绪；不会修改这些文件。", {
              p0: plan.remaining.length,
            }),
          ),
        );
      for (const change of plan.changes)
        result.append(
          disclosure(
            change.path,
            el("p", uiText("当前内容")),
            el("pre", change.before),
            el("p", uiText("修复后")),
            el("pre", change.after),
          ),
        );
      if (!plan.checked_paths.length) return;
      const confirm = el("input");
      confirm.type = "checkbox";
      const label = el(
        "label",
        uiText("已核对当前内容与路径变化，确认修复这些链接。"),
      );
      label.prepend(confirm);
      const apply = control(
        ["applying", "reconcile"].includes(plan.state)
          ? "继续核对本次链接修复"
          : "确认修复链接",
        async () => {
          if (!confirm.checked) return;
          await api.repairConfirm(job.id, {
            ...fixed,
            operation_id: plan.operation_id,
            plan_sha256: plan.plan_sha256,
          });
          await refresh();
        },
      );
      apply.disabled = true;
      confirm.addEventListener("change", () => {
        apply.disabled = !confirm.checked;
      });
      result.append(label, apply);
    }),
    result,
  );
  return host;
}
