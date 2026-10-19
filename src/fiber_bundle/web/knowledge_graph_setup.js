import { phrase, setText, uiText } from "./i18n/index.js";
// Persistent recovery controls; detection is explicit and never starts a note analysis.
import { el } from "./ui.js";
import { control } from "./record_ui.js";
import { noteIndexErrorText } from "./note_index_errors.js";

export function graphLoadFailure(error) {
  if (error?.code === "note_index_update_required")
    return "笔记列表暂不可用：全文索引已过期。请更新索引；若更新提示位置变化，先核对笔记位置。";
  if (
    ["layout_rebind_required", "layout_tracked_note_replaced"].includes(
      error?.code,
    )
  )
    return noteIndexErrorText(error.code);
  return (
    "笔记列表加载失败：" +
    (error?.message || "请重试或检查当前身份的查询权限。")
  );
}

export function createGraphSetup(graph, scope, refresh, profile) {
  const host = el("section", null, "graph-setup record-card"),
    message = el("p"),
    actions = el("div", null, "panel-actions"),
    candidate = el("div");
  message.setAttribute("role", "status");
  const go = control("打开知识库核对位置", async () => {});
  go.dataset.pageTarget = "vaults";
  actions.append(
    go,
    control("更新此知识库索引", async () => {
      const fixed = scope();
      if (!fixed.client_id || !fixed.vault_id)
        throw new Error("请先选择分析身份与知识库。");
      setText(message, uiText("正在更新索引，完成前请保留此页面…"));
      try {
        const result = await graph.updateIndex(fixed);
        if (JSON.stringify(fixed) !== JSON.stringify(scope())) return;
        setText(
          message,
          phrase("索引已更新，共 {p0} 篇笔记。", { p0: result.notes }),
        );
        await refresh();
      } catch (error) {
        if (JSON.stringify(fixed) === JSON.stringify(scope()))
          setText(message, uiText(graphLoadFailure(error)));
        throw error;
      }
    }),
    control("检查本地分析环境", async () => {
      candidate.replaceChildren();
      setText(
        message,
        uiText("正在校验本机已有的离线分析环境；不会下载或执行笔记分析…"),
      );
      try {
        const result = await graph.detectRuntime();
        if (result.state !== "ready") {
          setText(
            message,
            uiText(
              "未找到可用的本地分析环境。请确认 Docker 已启动，并按使用文档准备固定图谱镜像后重试。",
            ),
          );
          return;
        }
        const prior = profile(),
          value = result.profile;
        setText(
          message,
          phrase("已验证本地模型 {p0}，可用于正文主题分析。", {
            p0: value.engine.model.name,
          }),
        );
        candidate.append(
          control("配置并启用此分析环境", async () => {
            await graph.savePolicy({
              expected_sha256: prior?.configuration || null,
              value: { ...value, revision: (prior?.value?.revision || 0) + 1 },
            });
            candidate.replaceChildren();
            await refresh();
            setText(
              message,
              uiText("分析环境已启用。请明确选择笔记，再创建并执行任务。"),
            );
          }),
        );
      } catch (error) {
        setText(
          message,
          phrase("本地环境检测失败：{p0}", { p0: uiText(error.message) }),
        );
        throw error;
      }
    }),
    control("重试加载笔记列表", refresh),
  );
  host.append(
    el("h3", uiText("笔记列表与分析环境")),
    el(
      "p",
      uiText(
        "位置变化 → 核对位置；索引过期 → 更新索引；首次分析 → 检查并启用本地环境。",
      ),
    ),
    message,
    actions,
    candidate,
  );
  return {
    host,
    fail(error) {
      setText(message, uiText(graphLoadFailure(error)));
    },
    loaded(warnings = []) {
      setText(
        message,
        warnings.length
          ? uiText(warnings.join("；"))
          : profile()?.configured
            ? uiText("笔记列表已加载。")
            : uiText("笔记列表已加载；请先检查并启用本地分析环境。"),
      );
    },
  };
}
