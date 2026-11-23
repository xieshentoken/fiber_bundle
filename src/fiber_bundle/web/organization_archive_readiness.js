// Shared by Atlas and classic task cards; every recovery write needs fresh proof.
import { el } from "./ui.js";
import { control } from "./record_ui.js";
import { phrase, uiText } from "./i18n/index.js";

export function archiveReadiness(job) {
  const items = job.items || [];
  return {
    memberships: items.filter((i) => i.status === "linked" && i.after?.length)
      .length,
    ready: items.filter(
      (i) => i.target && i.archive_preflight?.state === "ready",
    ).length,
    blocked: items.filter(
      (i) => i.archive_wait || i.archive_preflight?.state === "blocked",
    ).length,
  };
}

export function appendArchivePreview(host, job) {
  if (job.confirmed) {
    if (job.state !== "closed" && job.items?.some((i) => i.archive_wait))
      host.append(
        el(
          "p",
          uiText(
            "如需按修改后的正文归档，请先保留结果结束本任务，再按当前资料生成新预览。",
          ),
          "task-impact",
        ),
      );
    return;
  }
  if (!job.items?.some((i) => i.archive_preflight)) return;
  if (job.items.some((i) => i.archive_preflight?.coverage === "selected_scope"))
    host.append(
      el(
        "p",
        uiText(
          "预览已核对所选范围的正文与链接；外部来源仅检查文件版本，完整反向引用在归档前核对。",
        ),
        "muted",
      ),
    );
  const totals = archiveReadiness(job);
  host.append(
    el(
      "p",
      phrase(
        "可挂载或保留归属 {p0} 项 · 所选范围可归档 {p1} 项 · 暂缓归档 {p2} 项",
        {
          p0: totals.memberships,
          p1: totals.ready,
          p2: totals.blocked,
        },
      ),
      "task-stage-summary",
    ),
  );
  if (totals.blocked)
    host.append(
      el(
        "p",
        uiText(
          "暂缓项保留可靠的 MOC 挂载与原路径；其他无冲突条目继续。请展开条目核对文件、链接及行号。",
        ),
        "task-impact",
      ),
    );
}

export function appendArchiveIssue(host, item) {
  const proof = item.archive_wait || item.archive_preflight;
  if (proof?.state !== "blocked") return;
  host.append(
    el(
      "p",
      uiText(
        proof.reason === "maintenance_archive_waiting_dependency"
          ? "本次移动会改写暂缓项的链接，暂缓此依赖项；其他独立项继续。"
          : proof.reason === "placement_preview_expired"
            ? "原归档预览已过期；本项保留原路径与可靠挂载。"
            : proof.reason === "maintenance_archive_target_conflict"
              ? "归档目标已存在；本项保留原路径与可靠挂载。"
              : "本项链接需核对，暂缓归档；原文保留，可靠挂载可继续。",
      ),
      "task-impact",
    ),
  );
  const list = el("ul");
  for (const link of proof.links || [])
    list.append(
      el(
        "li",
        phrase("{p0} · 第 {p1} 行 · 链接：{p2}", {
          p0: link.note_path,
          p1: link.line || uiText("未定位"),
          p2: link.target,
        }),
      ),
    );
  for (const path of proof.blocked_sources || []) list.append(el("li", path));
  if (list.children.length) host.append(list);
}

export function appendArchiveRecovery(host, job, fixed, api, refresh) {
  if (
    !job.confirmed ||
    !["partial", "failed", "paused", "waiting_file"].includes(job.state) ||
    !job.items?.some(
      (i) =>
        i.archive_operation &&
        i.archive_kind === "managed" &&
        i.phase === "members_done" &&
        !i.archive_deferral,
    )
  )
    return;
  const panel = el("div", null, "task-actions");
  panel.append(
    control("核对并暂缓受阻归档…", async () => {
      const value = await api.maintenanceAction(
        job.id,
        "defer_archive_preview",
        fixed,
      );
      const proof = value.archive_deferral_preview;
      if (!proof?.plan_sha256)
        throw new Error(uiText("无法核对归档暂缓范围，请刷新。"));
      panel.replaceChildren(
        el(
          "p",
          phrase(
            "已核对 {p0} 项归档尚未移动或交换文件。保留挂载并暂缓这些移动，继续其余独立项。",
            { p0: proof.items.length },
          ),
        ),
      );
      for (const item of proof.items) {
        panel.append(el("p", `${item.path} → ${item.target}`));
        appendArchiveIssue(panel, {
          archive_wait: {
            state: "blocked",
            reason: item.reason,
            links: item.links,
          },
        });
      }
      const checkbox = el("input"),
        label = el("label", uiText("我已核对，暂缓受阻移动并继续独立项。"));
      checkbox.type = "checkbox";
      label.prepend(checkbox);
      const button = control("确认暂缓归档并继续", async () => {
        await api.maintenanceAction(job.id, "defer_archives", {
          ...fixed,
          plan_sha256: proof.plan_sha256,
        });
        await refresh({ automatic: true });
      });
      button.disabled = true;
      checkbox.addEventListener("change", () => {
        button.disabled = !checkbox.checked;
      });
      panel.append(label, button);
    }),
  );
  host.append(panel);
}

export function appendArchiveDependencyFailure(host, error) {
  if (
    !error?.issues?.length ||
    ![
      "maintenance_preview_changed",
      "maintenance_archive_dependency_unavailable",
    ].includes(error.code || error.reason)
  )
    return;
  host.append(
    el(
      "p",
      uiText(
        "引用来源发生变化或暂未能核对；尚未确认执行。以下文件需要重新核对：",
      ),
      "task-impact",
    ),
  );
  const list = el("ul");
  for (const issue of error.issues)
    list.append(
      el(
        "li",
        phrase("{p0}：{p1}", {
          p0: issue.path || uiText("知识库根目录"),
          p1: uiText(
            {
              source_missing: "来源文件已不在原路径",
              source_budget: "变化来源超过本次有界核对范围",
              source_unavailable: "来源文件暂不可读",
              changing_sources: "文件在核对期间仍有变化",
              backlink_changed: "归档依赖的引用已变化",
              archive_inputs_changed: "归档依据已变化",
            }[issue.reason] || "归档依据已变化",
          ),
        }),
      ),
    );
  host.append(list);
}
