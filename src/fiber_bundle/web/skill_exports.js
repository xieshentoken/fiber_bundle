import { joinText, phrase, setText, uiText } from "./i18n/index.js";
// Offline export wizard; preview confirmation and persistent jobs stay on the server.
import { el, checkbox } from "./ui.js";
import { control } from "./record_ui.js";
import { rememberDraft, captureDraftSave } from "./unsaved_changes.js";

export function externalUse(vault, api, createConnection = () => {}) {
  const root = el("details"),
    content = el("div"),
    status = el("pre");
  root.append(
    el("summary", uiText("对外使用 · 接入与主题 Skill")),
    content,
    status,
  );
  root.dataset.exportVault = vault.id;
  let run = 0;
  const visible = () =>
    root.isConnected && !root.closest('[id$="-panel"]')?.hidden;
  const button = (label, task) =>
    control(label, async () => {
      try {
        await task();
      } catch (error) {
        if (root.isConnected) setText(status, uiText(error.message));
        throw error;
      }
    });
  function jobCard(job) {
    const card = el("article", null, "record-card"),
      output = el("pre"),
      progress = el("p"),
      error = el("p", null, "error"),
      gaps = el("ul"),
      members = el("details"),
      metadata = el("details"),
      actions = el("div");
    const states = {
      preview: "等待确认范围",
      running: "生成中",
      failed: "生成未完成",
      complete: "已校验，可下载",
      cancelled: "已取消",
    };
    const qualities = {
      passed: "完整性通过，未独立核验",
      not_checked: "未做完整性校验",
      unknown: "完整性未记录",
      partial: "存在缺项",
      failed: "完整性未通过",
    };
    members.append(
      el(
        "summary",
        phrase("所选资料 · {p0} 个模块", { p0: job.members.length }),
      ),
    );
    const selected = el("ul");
    for (const member of job.members)
      selected.append(
        el(
          "li",
          joinText([
            "",
            member.title,
            " · ",
            member.kind === "expert"
              ? uiText("专家 Draft，审核待完成")
              : uiText("笔记"),
            " · ",
            qualities[member.completeness_status] ||
              member.completeness_status ||
              "完整性未记录",
            "",
          ]),
        ),
      );
    members.append(selected);
    members.open = job.state === "preview";
    metadata.append(el("summary", uiText("版本、文件摘要与技术详情")), output);
    function show(value) {
      job = value;
      setText(output, uiText(JSON.stringify(value, null, 2)));
      setText(
        progress,
        phrase("{p0} · 已生成 {p1}/{p2} 个模块 · 预计内容 {p3} 字节", {
          p0: states[job.state] || job.state,
          p1: job.completed_members,
          p2: job.members.length,
          p3: (job.estimated_content_bytes || 0).toLocaleString(),
        }),
      );
      error.hidden = !job.message && job.state !== "failed";
      setText(
        error,
        uiText(
          job.message ||
            "生成未完成，请查看技术详情中的错误代码并核对后台状态。",
        ),
      );
      gaps.replaceChildren(...job.known_gaps.map((gap) => el("li", gap)));
      actions.replaceChildren();
      if (["preview", "running", "failed"].includes(job.state))
        actions.append(
          button(
            job.state === "preview" ? "确认范围并生成" : "明确继续生成",
            async () => {
              const generation = ++run;
              do {
                job = await api.skillExports.action(
                  job.id,
                  "step",
                  job.preview_sha256,
                );
                if (!card.isConnected || !visible() || generation !== run)
                  break;
                show(job);
              } while (job.state === "running");
            },
          ),
          button("取消此导出", async () => {
            ++run;
            show(
              await api.skillExports.action(
                job.id,
                "cancel",
                job.preview_sha256,
              ),
            );
          }),
        );
      if (job.state === "complete") {
        const link = el("a", uiText("下载已校验主题 Skill"));
        link.href =
          "/admin/skill-exports/" + encodeURIComponent(job.id) + "/download";
        link.download = "Fiber-theme.zip";
        actions.append(link);
      }
    }
    card.append(
      el("h4", phrase("离线快照 {p0}", { p0: job.name })),
      progress,
      members,
      gaps,
      error,
      el(
        "p",
        uiText(
          "此导出不调用模型；外部宿主回答费用由宿主管理。离线副本下载后无法撤回。",
        ),
      ),
      actions,
      metadata,
    );
    show(job);
    return card;
  }
  async function open() {
    const connections = (await api.agents.list()).filter((item) =>
      item.configuration.vault_ids.includes(vault.id),
    );
    if (!root.isConnected) return;
    content.replaceChildren(
      el(
        "p",
        uiText(
          "在线连接可撤销；已下载的离线快照无法收回。导出不调用模型，不需要 API Key 或提取额度。",
        ),
      ),
    );
    content.append(
      button("创建本库接入（预填，不授予权限）", () => createConnection(vault)),
    );
    for (const item of connections)
      content.append(
        el(
          "p",
          `${item.configuration.name} · ${item.access_state} · ${item.configuration.quality_scope || "all"}`,
        ),
      );
    const enabled = connections.filter(
      (item) => item.access_state === "enabled",
    );
    if (enabled.length) {
      const form = el("form"),
        scope = el("select"),
        members = el("div"),
        name = el("input"),
        mode = el("select");
      form.dataset.draft = "tracked";
      setText(scope, uiText("导出使用的连接权限"), "aria-label");
      for (const item of enabled) {
        const option = el("option", item.configuration.name);
        option.value = item.id;
        scope.append(option);
      }
      name.value = "knowledge-theme";
      name.pattern = "[a-z][a-z0-9-]{0,47}";
      name.required = true;
      setText(name, uiText("主题包名称（小写英文）"), "aria-label");
      setText(mode, uiText("主题选材方式"), "aria-label");
      for (const [value, label] of [
        ["selected", "所选资料或单本书"],
        ["moc", "已有MOC及其直接引用"],
        ["vault", "明确选择整个当前授权库"],
      ]) {
        const option = el("option", uiText(label));
        option.value = value;
        mode.append(option);
      }
      const originals = checkbox(
        "显式包含可读取的附件与原件（需独立权限）",
        false,
      );
      form.append(scope, name, mode, originals.label, members);
      let scopeEpoch = 0;
      const loadMembers = async () => {
        const selectedEpoch = ++scopeEpoch,
          selectedConnection = scope.value;
        members.replaceChildren();
        let offset = 0;
        do {
          const packet = await api.agents.knowledgeCatalog(selectedConnection, {
            vault_ids: [vault.id],
            offset,
            limit: 50,
          });
          if (!form.isConnected || selectedEpoch !== scopeEpoch) return;
          for (const item of packet.items) {
            const choice = checkbox(
              `${item.title} · ${item.kind} · ${item.completeness_status || "unknown"}`,
              false,
            );
            choice.input.name = "modules";
            choice.input.value = item.module_id;
            choice.input.checked = item.module_id === root.dataset.exportModule;
            members.append(choice.label);
          }
          offset = packet.next_offset;
        } while (offset !== null && offset < 1000);
        rememberDraft(members);
      };
      scope.addEventListener("change", () =>
        loadMembers().catch((error) => {
          setText(status, uiText(error.message));
        }),
      );
      form.append(
        button("预览并冻结所选来源", async () => {
          if (!form.reportValidity()) return;
          const acknowledge = captureDraftSave(form);
          const job = await api.skillExports.preview({
            connection_id: scope.value,
            vault_id: vault.id,
            name: name.value,
            mode: mode.value,
            include_originals: originals.input.checked,
            module_ids: Array.from(
              members.querySelectorAll("input:checked"),
              (item) => item.value,
            ),
          });
          acknowledge();
          content.append(jobCard(job));
        }),
      );
      form.addEventListener("submit", (event) => event.preventDefault());
      content.append(form);
      rememberDraft(form);
      await loadMembers();
    } else
      content.append(
        el(
          "p",
          uiText(
            "请先在 Agent 与客户端应用并启用一个有本库查询权限的连接。导出不会自动授予权限。",
          ),
        ),
      );
    const history = el("div");
    content.append(
      history,
      button("刷新导出版本记录", async () => {
        const rows = await api.skillExports.history();
        history.replaceChildren(
          ...rows.filter((job) => job.vault_id === vault.id).map(jobCard),
        );
      }),
    );
  }
  const openButton = button("查看接入与导出向导", open);
  openButton.dataset.exportOpen = "true";
  root.append(openButton);
  return root;
}
