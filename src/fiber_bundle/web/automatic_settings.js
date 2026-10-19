import { optionText, phrase, setText, uiText } from "./i18n/index.js";
// Explicit policy configuration and exception queue. Reads never start processing.
import { el, checkbox } from "./ui.js";
import { control } from "./record_ui.js";
import { rememberDraft, captureDraftSave } from "./unsaved_changes.js";
import { confirmAction } from "./confirm_dialog.js";
import { automaticStatus } from "./automatic_status.js";

export function automaticSettings(vault, api) {
  const root = el("details"),
    summary = el("summary", uiText("处理与审核策略")),
    content = el("div");
  root.append(summary, content);
  let loaded = false,
    generation = 0;
  let pending = null;
  async function load() {
    if (!root.open || loaded) return;
    loaded = true;
    const scope = ++generation;
    try {
      const [policy, clients] = await Promise.all([
        api.vaultSave.automaticPolicy(vault.id),
        api.clients(),
      ]);
      if (!root.isConnected || scope !== generation) return;
      const form = el("form"),
        output = el("div");
      form.className = "uv-policy-form";
      form.dataset.draft = "tracked";
      form.append(
        el(
          "p",
          uiText(
            "仅对启用后新进入本库的资料生效。系统判断不会记录成人工来源或视觉核对，异常保留待处理。",
          ),
        ),
      );
      const ownerLabel = el("label", uiText("自动处理身份")),
        owner = el("select");
      owner.append(el("option", uiText("选择具有查询、新增、原件权限的身份")));
      owner.firstChild.value = "";
      for (const client of clients.filter(
        (c) =>
          c.active &&
          c.grants.some(
            (g) =>
              g.vault_id === vault.id &&
              g.can_add &&
              g.can_query &&
              g.can_original,
          ),
      )) {
        const option = el("option", client.name);
        option.value = client.id;
        owner.append(option);
      }
      owner.value = policy.settings.client_id || "";
      ownerLabel.append(owner);
      form.append(ownerLabel);
      const isV2 = policy.settings.policy_version === 2;
      const enabled = checkbox(
        "启用本库处理策略",
        isV2
          ? policy.settings.enabled
          : policy.settings.review_mode === "automatic",
      );
      const completeness = checkbox(
        "完整性校验",
        isV2 ? policy.settings.completeness_check : true,
      );
      const direct = checkbox(
        "直接保存到 Inbox",
        isV2
          ? policy.settings.direct_to_inbox
          : policy.settings.publish_mode === "automatic",
      );
      enabled.input.name = "enabled";
      completeness.input.name = "completeness_check";
      direct.input.name = "direct_to_inbox";
      form.append(
        enabled.label,
        completeness.label,
        direct.label,
        el(
          "p",
          uiText(
            "关闭完整性校验后仍可直接保存，产物标记为未校验；权限、文件有效性和安全路径始终检查。",
          ),
          "muted",
        ),
      );
      const modeLabel = el("label", uiText("提取方式")),
        mode = el("select");
      mode.name = "processing_mode";
      mode.append(
        optionText("基础提取（无需 Key 或提取额度）", "basic"),
        optionText("Pi + Skill 智能提取", "pi_skill"),
      );
      mode.value = policy.settings.processing_mode || "basic";
      modeLabel.append(mode);
      const modelLabel = el("label", uiText("已配置的处理模型")),
        model = el("select");
      model.name = "model_configuration_id";
      for (const item of (policy.models || []).filter(
        (m) => m.processing_location === "cloud",
      ))
        model.append(new window.Option(item.name, item.id));
      model.value =
        policy.settings.model_configuration_id || model.options[0]?.value || "";
      modelLabel.append(model);
      const consent = checkbox(
        "允许将本次任务的资料发送至所选云模型",
        policy.settings.allow_model_source_content || false,
      );
      const extraSummary = checkbox(
        "入库后另外生成 Pi 摘要（可选，不阻塞原文）",
        policy.settings.extra_summary || false,
      );
      extraSummary.input.name = "extra_summary";
      const displayModel = () => {
        const needed = mode.value === "pi_skill" || extraSummary.input.checked;
        modelLabel.hidden = consent.label.hidden = !needed;
        model.required = needed;
      };
      mode.addEventListener("change", displayModel);
      extraSummary.input.addEventListener("change", displayModel);
      displayModel();
      form.append(
        modeLabel,
        extraSummary.label,
        modelLabel,
        consent.label,
        el(
          "p",
          uiText(
            "模型与 API Key 在模型与查询中配置，预算在执行与费用中统一设置。基础提取和完整性检查不消耗模型预算。",
          ),
          "muted",
        ),
      );
      const kinds = {};
      for (const [key, title] of [
        ["document", "普通文档"],
        ["article", "图文文章"],
        ["intake", "通道整理"],
        ["media", "媒体转录"],
        ["expert", "完整专家 Draft"],
      ]) {
        const box = checkbox(
          title,
          policy.settings.allowed_kinds.includes(key),
        );
        kinds[key] = box.input;
        form.append(box.label);
      }
      const save = el("button", uiText("保存本库自动处理策略"));
      save.type = "submit";
      form.append(save);
      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        save.disabled = true;
        const acknowledge = captureDraftSave(form);
        try {
          const result = await api.vaultSave.saveAutomaticPolicy(vault.id, {
            expected_revision: policy.revision,
            settings: {
              client_id: owner.value || null,
              policy_version: 2,
              enabled: enabled.input.checked,
              completeness_check: completeness.input.checked,
              direct_to_inbox: direct.input.checked,
              processing_mode: mode.value,
              extra_summary: extraSummary.input.checked,
              model_configuration_id:
                mode.value === "pi_skill" || extraSummary.input.checked
                  ? model.value || null
                  : null,
              allow_model_source_content: consent.input.checked,
              allowed_kinds: Object.keys(kinds).filter((k) => kinds[k].checked),
            },
          });
          if (!root.isConnected || scope !== generation) return;
          policy.revision = result.revision;
          acknowledge();
          setText(output, uiText("策略已保存；历史失败任务和旧资料不会重放。"));
        } catch (error) {
          if (root.isConnected && scope === generation)
            setText(output, uiText(error.message));
        } finally {
          save.disabled = false;
        }
      });
      const queue = el("div");
      const refresh = async () => {
        const jobs = await api.vaultSave.automaticJobs(vault.id);
        if (!root.isConnected || scope !== generation) return;
        queue.replaceChildren(el("h4", uiText("系统任务与待处理")));
        for (const job of jobs) {
          const status = automaticStatus(job);
          const row = el("article", null, "record-card");
          row.dataset.taskId = job.id;
          row.dataset.taskKind = "automatic";
          row.tabIndex = -1;
          row.append(
            el("p", phrase("任务编号：{p0}", { p0: job.id }), "muted"),
            el("p", `${job.title || job.kind} · ${job.phase} · ${status.text}`),
            el("p", job.display_message || job.reason || ""),
          );
          if (status.state === "superseded")
            row.append(
              el(
                "p",
                phrase("后续任务：{p0}", { p0: job.superseded_by }),
                "muted",
              ),
            );
          if (
            status.state !== "superseded" &&
            ["reconcile", "needs_review"].includes(job.state)
          )
            row.append(
              control(
                status.state === "index_reconcile"
                  ? "核对后仅继续索引"
                  : "核对后继续同一冻结任务",
                async () => {
                  const approve = job.requires_new_model_attempt || false;
                  if (
                    approve &&
                    !(await confirmAction({
                      title: "重新尝试失败的可选摘要？",
                      body: [
                        "旧调用和未知用量记录保留，新尝试可能再次消耗 Tokens。原文已入库，不受摘要失败影响。",
                      ],
                      confirmLabel: "确认新增处理尝试",
                    }))
                  )
                    return;
                  await api.vaultSave.reconcileAutomaticJob(
                    vault.id,
                    job.id,
                    approve,
                  );
                  await refresh();
                },
              ),
            );
          if (status.state === "awaiting_review")
            row.append(
              control("确认保存此提取产物", async () => {
                await api.vaultSave.publishAutomaticJob(vault.id, job.id);
                await refresh();
              }),
            );
          if (!["complete", "cancelled", "superseded"].includes(status.state))
            row.append(
              control("停止后续处理（保留已完成结果）", async () => {
                await api.vaultSave.cancelAutomaticJob(vault.id, job.id);
                await refresh();
              }),
            );
          queue.append(row);
        }
        if (!jobs.length)
          queue.append(
            el("p", uiText("暂无系统任务。读取此列表不会创建任务。")),
          );
      };
      const attachments = el("div"),
        refreshAttachments = async () => {
          const values = await api.vaultSave.attachmentProjections(vault.id);
          if (!root.isConnected || scope !== generation) return;
          attachments.replaceChildren(el("h4", uiText("原件目录副本")));
          for (const item of values) {
            const row = el("article", null, "record-card");
            row.append(
              el("p", `${item.original_name} · ${item.state} · ${item.path}`),
            );
            if (item.state === "reconcile")
              row.append(
                control("核对并恢复原件副本", async () => {
                  await api.vaultSave.reconcileAttachment(vault.id, item.id);
                  await refreshAttachments();
                }),
              );
            attachments.append(row);
          }
          if (!values.length)
            attachments.append(
              el("p", uiText("暂无已登记的目录副本。内部原件账本独立保留。")),
            );
        };
      content.replaceChildren(
        form,
        output,
        control("刷新系统任务", refresh),
        queue,
        control("刷新原件副本", refreshAttachments),
        attachments,
      );
      rememberDraft(form);
      await Promise.all([
        refresh().catch((error) => {
          setText(queue, uiText(error.message));
        }),
        refreshAttachments().catch((error) => {
          setText(attachments, uiText(error.message));
        }),
      ]);
    } catch (error) {
      if (root.isConnected && scope === generation) {
        setText(content, uiText(error.message));
        loaded = false;
      }
    }
  }
  root.openTasks = async () => {
    root.open = true;
    pending ||= load().finally(() => {
      pending = null;
    });
    await pending;
  };
  root.addEventListener("toggle", () => {
    if (root.open) void root.openTasks();
  });
  return root;
}
