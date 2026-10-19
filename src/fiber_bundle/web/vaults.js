import { phrase, setText, uiText } from "./i18n/index.js";
import { confirmAction } from "./confirm_dialog.js";
import { control } from "./record_ui.js";
import { externalUse } from "./skill_exports.js";
import { captureDraftSave } from "./unsaved_changes.js";
import { $, el, checkbox, captureMessage } from "./ui.js";

export function renderVaults(
  vaults,
  api,
  refreshData = async () => {},
  prefill = () => {},
  configure = () => {},
  organize = () => {},
  createConnection = () => {},
  relocate = () => {},
) {
  const target = $("vault-list");
  target.replaceChildren();

  function addTrash(parent, vault) {
    const button = control("移到废纸篓", async (notify) => {
      const preview = await api.vaultTrashPreview(vault.id);
      const body = [
        { label: "目录", items: [preview.root] },
        `${preview.files} 个文件 · ${preview.bytes.toLocaleString()} 字节。访问将撤销；系统恢复后须重新接入。`,
      ];
      if (preview.synchronized)
        body.push("此目录处于同步位置，移动会影响同步副本。");
      if (
        !(await confirmAction({
          title: `将“${vault.name}”移到系统废纸篓？`,
          body,
          confirmLabel: "移到废纸篓",
          tone: "danger",
        }))
      )
        return;
      await api.vaultTrashConfirm(preview.id, preview.root);
      await refreshData();
      notify("已移到系统废纸篓，旧授权保持撤销。");
    });
    button.classList.add("danger");
    parent.append(button);
  }
  function management(vault) {
    const actions = el("details", null, "vault-actions"),
      summary = el("summary", uiText("管理")),
      body = el("div", null, "vault-actions-body"),
      buttons = el("div", null, "vault-actions-group"),
      danger = el("div", null, "vault-actions-group vault-actions-danger");
    setText(summary, phrase("管理 {p0}", { p0: vault.name }), "aria-label");
    danger.setAttribute("role", "group");
    setText(danger, uiText("撤销访问或移动文件"), "aria-label");
    body.append(buttons, danger);
    actions.append(summary, body);
    return { actions, buttons, danger };
  }
  // Successful Trash records remain in the API for audit/recovery, not in this list.
  const visible = vaults.filter(
    (v) => v.attachment_state !== "trashed" && v.status !== "trashed",
  );
  const active = visible.filter((v) => v.status === "active");
  if (!active.length)
    target.append(
      el(
        "p",
        uiText(
          "暂无启用的知识库。通过下方「接入笔记仓库」接入现有目录或新建仓库。",
        ),
        "empty",
      ),
    );
  for (const vault of visible.filter(
    (v) => v.attachment_state && v.attachment_state !== "attached",
  )) {
    const row = el("article", null, "row vault-row ui-card"),
      info = el("div", null, "vault-info"),
      { actions, buttons, danger } = management(vault);
    const stateLabels = {
      detached: "已解除接入",
      reconcile: "待核对系统恢复结果",
      trash_pending: "废纸篓操作待完成",
    };
    info.append(
      el("h3", vault.name),
      el("p", stateLabels[vault.attachment_state] || vault.attachment_state),
    );
    row.append(info);
    if (vault.attachment_state === "detached") {
      row.append(control("重新接入并重建索引", async () => prefill(vault)));
      addTrash(danger, vault);
    }
    if (["reconcile", "trash_pending"].includes(vault.attachment_state)) {
      buttons.append(
        control("核对系统恢复结果", async (notify) => {
          const jobs = await api.vaultTrashJobs();
          const job = jobs.find(
            (j) =>
              j.vault_id === vault.id &&
              ["reconcile", "running"].includes(j.state),
          );
          if (!job) {
            notify("没有可核对的操作记录。", true);
            return;
          }
          if (
            !(await confirmAction({
              title: "确认已恢复原目录？",
              body: [
                { label: "目录", items: [job.root] },
                "先在系统废纸篓核对并恢复原目录。此步骤只核对目录身份，不重试移动。",
              ],
              confirmLabel: "确认已恢复原目录",
            }))
          )
            return;
          await api.vaultRecovery(job.id, job.root);
          await refreshData();
          notify("目录身份已核对，仍需显式重新接入和设置权限。");
        }),
      );
    }
    if (buttons.children.length || danger.children.length) row.append(actions);
    target.append(row);
  }
  for (const vault of active) {
    const row = el("article", null, "row vault-row ui-card"),
      info = el("div", null, "vault-info"),
      heading = el("div", null, "vault-heading"),
      title = el("h3", vault.name);
    title.id = `vault-title-${vault.id}`;
    heading.append(title, el("span", uiText("已启用"), "status-badge success"));
    info.append(heading, el("p", uiText("原件与来源版本独立保留")));
    const box = checkbox("默认参与查询", vault.default_query);
    box.input.setAttribute("aria-describedby", title.id);
    box.input.dataset.draft = "tracked";
    box.input.addEventListener("change", async () => {
      const notify = captureMessage("vaults");
      const acknowledge = captureDraftSave(box.input);
      box.input.disabled = true;
      try {
        await api.setDefaultQuery(vault.id, box.input.checked);
        vault.default_query = box.input.checked;
        acknowledge();
        notify("默认查询设置已保存，客户端权限保持独立。");
      } catch (error) {
        box.input.checked = !!vault.default_query;
        notify(error.message, true);
      } finally {
        box.input.disabled = false;
      }
    });
    if (vault.absolute_root)
      info.append(
        el("p", vault.absolute_root, "vault-path"),
        el("p", vault.read_only ? uiText("只读接入") : uiText("可读写")),
      );
    const { actions, buttons, danger } = management(vault),
      unlink = el("button", uiText("解除接入"));
    unlink.className = "danger";
    unlink.type = "button";
    unlink.addEventListener("click", async () => {
      if (
        !(await confirmAction({
          title: `解除“${vault.name}”的接入？`,
          body: ["保留原文件，撤销旧访问。重新使用须显式接入并授权。"],
          confirmLabel: "解除接入",
          cancelLabel: "保留接入",
          tone: "danger",
        }))
      )
        return;
      const notify = captureMessage("vaults");
      unlink.disabled = true;
      try {
        await api.vaultUnlink(vault.id);
        await refreshData();
        notify("已解除接入，原文件保留；旧访问已撤销。");
      } catch (e) {
        notify(e.message, true);
        unlink.disabled = false;
      }
    });
    danger.append(unlink);
    buttons.append(control("保存规则", async () => configure(vault)));
    buttons.append(control("整理与布局迁移", async () => organize(vault)));
    buttons.append(control("迁移仓库位置", async () => relocate(vault)));
    buttons.append(externalUse(vault, api, createConnection));
    addTrash(danger, vault);
    row.append(info, box.label, actions);
    target.append(row);
  }
}
