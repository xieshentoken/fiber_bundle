import { phrase, setText, uiText } from "./i18n/index.js";
import { confirmPublication } from "./publication_preview.js";
// Explicit full compiler artifacts and candidate approval; no source text executes actions.
import { el, action } from "./ui.js";
export function addExpertControls(row, item, api, refresh) {
  const task = item.expert;
  if (!task) return;
  if (item.plan.project_skill) {
    const skill = item.plan.project_skill;
    row.append(
      el(
        "p",
        phrase("任务 Skill：fake-expert {p0} / {p1} · 本任务固定此版本", {
          p0: skill.runtime.release,
          p1: skill.snapshot_sha256.slice(0, 12),
        }),
      ),
    );
  }
  const original = el("a", uiText("下载原 PDF 核对"));
  original.href = `/admin/imports/${encodeURIComponent(item.id)}/source?client_id=${encodeURIComponent(item.client_id)}`;
  original.download = "source.pdf";
  row.append(original);
  row.append(el("p", `${task.code || task.state} · ${task.next_action || ""}`));
  row.append(
    el(
      "small",
      phrase(
        "Reference Draft · 独立语义审核待完成 · 模型调用 {p0}（编译器本身无需模型）",
        { p0: task.model_calls ?? "未知" },
      ),
    ),
  );
  if (task.authoring) {
    row.append(
      el(
        "p",
        phrase("Pi章节材料：{p0} · {p1}/{p2}；已完成 {p3} 个分片", {
          p0: uiText(task.authoring.state),
          p1: task.authoring.completed_units,
          p2: task.authoring.total_units ?? "待解析",
          p3: task.authoring.completed_fragments ?? 0,
        }),
      ),
    );
    if (task.authoring.message)
      row.append(el("p", uiText(task.authoring.message), "error"));
    if (task.authoring.known_gaps?.length)
      row.append(el("pre", task.authoring.known_gaps.join("\n")));
    if (
      ["waiting_budget", "failed", "interrupted", "queued"].includes(
        task.authoring.state,
      )
    ) {
      const resume = el("button", uiText("继续 Pi 章节材料（保留已完成调用）"));
      action(resume, async () => {
        await api.startImport(item.id, item.client_id);
        await refresh();
      });
      row.append(resume);
    }
  }
  if (task.state === "paused") {
    const label = el(
        "label",
        uiText(
          "明确归档的 workpack ZIP 导入编号（同身份、同库；留空恢复原作业）",
        ),
      ),
      field = el("input"),
      button = el("button", uiText("恢复完整 Skill"));
    field.value = task.workpack_import_id || "";
    setText(field, uiText("恢复 workpack 导入编号"), "aria-label");
    label.append(field);
    action(button, async () => {
      await api.resumeExpert(item.id, {
        client_id: item.client_id,
        workpack_import_id: field.value.trim() || null,
      });
      await refresh();
    });
    row.append(label, button);
  }
  if (task.state !== "completed") return;
  const show = el("button", uiText("查看结构化 Draft 与证据锚点")),
    panel = el("section");
  panel.hidden = true;
  action(show, async () => {
    const packet = await api.expertArtifact(item.id, item.client_id);
    panel.replaceChildren();
    panel.hidden = false;
    panel.append(
      el(
        "p",
        phrase("{p0} 个对象 · {p1} 个证据锚点 · draft / deferred", {
          p0: packet.objects.length,
          p1: packet.anchors.length,
        }),
      ),
    );
    const data = el(
      "pre",
      JSON.stringify(
        { objects: packet.objects, anchors: packet.anchors },
        null,
        2,
      ),
    );
    panel.append(data);
    panel.append(
      el(
        "p",
        phrase("版本 {p0} · 来源版本 {p1} · {p2}", {
          p0: packet.manifest_sha256,
          p1: packet.source_version_id || "未知",
          p2: packet.publication?.completeness_status || "unknown",
        }),
      ),
    );
    if (packet.publication?.known_gaps?.length)
      panel.append(el("pre", packet.publication.known_gaps.join("\n")));
    const queryLabel = el(
        "label",
        uiText("查询原始候选协议（保留 assurance、对象与证据 ID）"),
      ),
      question = el("input"),
      query = el("button", uiText("查询 Reference Draft")),
      answer = el("pre");
    question.maxLength = 256;
    setText(question, uiText("Reference Draft 查询"), "aria-label");
    queryLabel.append(question);
    action(query, async () => {
      const result = await api.expertQuery(
        item.id,
        item.client_id,
        question.value,
      );
      setText(answer, uiText(JSON.stringify(result, null, 2)));
    });
    panel.append(queryLabel, query, answer);
    const download = el("a", uiText("下载完整原始 Reference Draft 包"));
    download.href = `/admin/imports/${encodeURIComponent(item.id)}/expert-package?client_id=${encodeURIComponent(item.client_id)}`;
    download.download = "reference-draft.zip";
    panel.append(download);
    if (packet.publication?.state === "published") {
      const exportTheme = el("button", uiText("导出此专家模块的主题 Skill"));
      exportTheme.type = "button";
      Object.assign(exportTheme.dataset, {
        pageTarget: "vaults",
        exportVaultId: packet.publication.vault_id || item.vault_id,
        exportModuleId: packet.module_id,
      });
      panel.append(exportTheme);
      panel.append(
        el(
          "p",
          phrase("候选已导入：{p0}；索引仍须显式更新。", {
            p0:
              packet.publication.entry_note_path ||
              packet.publication.relative_path,
          }),
        ),
      );
      return;
    }
    const sourceLabel = el("label", uiText("我已核对原 PDF、对象与证据锚点")),
      source = el("input"),
      candidateLabel = el(
        "label",
        uiText("以未独立核实的候选分析导入，不作为原文证据"),
      ),
      candidate = el("input"),
      publish = el("button", uiText("确认导入候选笔记与结构化包"));
    source.type = candidate.type = "checkbox";
    sourceLabel.append(source);
    candidateLabel.append(candidate);
    action(publish, async () => {
      if (!source.checked || !candidate.checked)
        throw new Error("请先核对来源并确认候选状态。");
      const publication = {
        client_id: item.client_id,
        artifact_sha256: packet.manifest_sha256,
        source_reviewed: true,
        candidate_acknowledged: true,
      };
      if (!(await confirmPublication(api, "expert", item.id, publication)))
        return;
      await api.publishExpert(item.id, publication);
      await refresh();
    });
    panel.append(sourceLabel, candidateLabel, publish);
  });
  row.append(show, panel);
}
