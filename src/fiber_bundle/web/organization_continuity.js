// Explicit lifecycle controls; inspecting a task never starts or rolls back work.
import { el } from "./ui.js";
import { control } from "./record_ui.js";
import { phrase, uiText, setText } from "./i18n/index.js";
import { prepareWaitingSources } from "./organization_preparation.js";
import { revealSection } from "./view_sections.js";

export function appendContinuity(host, job, fixed, api, refresh) {
  if (job.state === "waiting_file" && job.waiting_sources?.length) {
    let previous = [],
      offset = 0;
    const status = el(
      "p",
      uiText("只准备本任务等待中的文件；每次最多 10 篇，不自动重复下载。"),
      "muted",
    );
    const list = el("ul");
    status.setAttribute("role", "status");
    host.append(
      status,
      list,
      control("准备等待资料并继续", async () => {
        const value = await prepareWaitingSources(job, fixed, api, {
          previous,
          offset,
          active: () =>
            host.isConnected && document.visibilityState !== "hidden",
          progress: (done, total) =>
            setText(
              status,
              phrase("已检查 {p0}/{p1} 篇", { p0: done, p1: total }),
            ),
        });
        previous = value.results;
        offset = value.nextOffset;
        if (value.resumed) await refresh({ automatic: true });
        else {
          setText(
            status,
            phrase(
              "本次已就绪 {p0}/{p1} 篇；其余请稍后检查，原任务保持等待。",
              { p0: value.ready, p1: value.total },
            ),
          );
          list.replaceChildren();
          for (const item of value.results)
            list.append(
              el(
                "li",
                phrase("{p0}：{p1}", {
                  p0: item.path,
                  p1: uiText(
                    {
                      available: "已在本机",
                      requested: "已请求下载",
                      downloading: "下载中",
                      conflict: "存在版本冲突",
                      error: "检查失败，请核对文件",
                      unknown: "状态待核对",
                      not_requested: "尚未请求下载",
                    }[item.state] || "状态待核对",
                  ),
                }),
              ),
            );
        }
      }),
    );
  }
  if (job.blocking_task) {
    const other = job.blocking_task;
    host.append(
      el(
        "p",
        phrase("等待原任务 {p0} · 已处理 {p1}/{p2} 项", {
          p0: other.id,
          p1: other.completed || 0,
          p2: other.total,
        }),
        "task-impact",
      ),
    );
    host.append(
      control("查看阻塞任务", () => {
        const target = [
          ...document.querySelectorAll(
            "[data-maintenance-job], details[data-view-key]",
          ),
        ].find(
          (node) =>
            node.dataset.maintenanceJob === other.id ||
            node.dataset.viewKey === `job:${other.id}`,
        );
        if (!target) return refresh();
        revealSection(target);
        target.scrollIntoView({ block: "center", behavior: "smooth" });
        target.tabIndex = -1;
        target.focus({ preventScroll: true });
      }),
    );
  }
  if (
    ![
      "partial",
      "failed",
      "blocked",
      "paused",
      "waiting_task",
      "waiting_file",
      "preview",
    ].includes(job.state)
  )
    return;
  const panel = el("div", null, "task-actions");
  const inspect = control("结束任务并保留已完成结果…", async () => {
    const packet = await api.maintenanceAction(job.id, "finish_preview", fixed);
    const proof = packet.close_preview;
    if (!proof?.plan_sha256)
      throw new Error(uiText("无法核对任务结束范围，请刷新。"));
    panel.replaceChildren(
      el(
        "p",
        phrase(
          "保留已处理 {p0} 项及已归档 {p1} 项，结束剩余 {p2} 项；不撤销文件，历史保留。",
          { p0: proof.completed, p1: proof.archived, p2: proof.remaining },
        ),
      ),
    );
    const checkbox = el("input"),
      label = el("label", uiText("我已核对，保留结果并结束此任务。"));
    checkbox.type = "checkbox";
    label.prepend(checkbox);
    const finish = control("确认结束并保留结果", async () => {
      await api.maintenanceAction(job.id, "finish", {
        ...fixed,
        plan_sha256: proof.plan_sha256,
      });
      await refresh();
    });
    finish.disabled = true;
    checkbox.addEventListener("change", () => {
      finish.disabled = !checkbox.checked;
    });
    panel.append(label, finish);
  });
  panel.append(inspect);
  host.append(panel);
}
