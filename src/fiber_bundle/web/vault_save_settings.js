import { phrase, setText, translate, uiText } from "./i18n/index.js";
import { vaultMergeView } from "./vault_merge_view.js";
import { automaticSettings } from "./automatic_settings.js";
// Per-Vault rules; activating a layout always requires a fresh explicit preview.
import { el, checkbox, captureMessage } from "./ui.js";
import { control } from "./record_ui.js";
import {
  captureDraftSave,
  hasUnsavedChanges,
  rememberDraft,
} from "./unsaved_changes.js";
import { createDirectoryPicker } from "./directory_picker.js";
import { createRoleFields } from "./vault_role_fields.js";
import { disclosure, revealSection } from "./view_sections.js";
import { formSection } from "./form_sections.js";
import { helpTip } from "./help_tip.js";

export function createVaultSaveView(api, host) {
  let generation = 0,
    active = null,
    browser = null,
    automaticPanel = null;
  function clear() {
    generation++;
    active = null;
    browser?.clear();
    browser = null;
    automaticPanel = null;
    host.replaceChildren();
    host.hidden = true;
  }
  async function open(vault) {
    revealSection(host);
    if (active === vault.id && !host.hidden) {
      host.scrollIntoView({ block: "nearest" });
      return;
    }
    if (
      hasUnsavedChanges(host) &&
      !window.confirm(translate("切换仓库会清除未保存的规则草稿，是否继续？"))
    )
      return;
    clear();
    active = vault.id;
    const epoch = generation,
      notify = captureMessage("vaults");
    host.hidden = false;
    host.replaceChildren(el("p", uiText("正在读取保存规则…")));
    let state;
    try {
      state = await api.vaultSave.get(vault.id);
    } catch (error) {
      if (epoch === generation) {
        active = null;
        host.replaceChildren(
          el("p", uiText(error.message), "error"),
          control("重试读取规则", () => open(vault)),
        );
      }
      return;
    }
    if (epoch !== generation) return;
    host.replaceChildren();
    const heading = el("h3", phrase("{p0} · 保存规则", { p0: vault.name }));
    heading.tabIndex = -1;
    const form = el("form"),
      mappings = [],
      previewArea = el("div"),
      mode = el("select"),
      modeLabel = el("label", uiText("保存形式"));
    form.dataset.draft = "tracked";
    form.addEventListener("submit", (event) => event.preventDefault());
    for (const [value, title] of [
      ["legacy_bundle", "独立文件夹"],
      ["obsidian_folders", "标题笔记与集中图片"],
    ]) {
      const option = el("option", uiText(title));
      option.value = value;
      mode.append(option);
    }
    mode.value = state.settings.layout_mode;
    modeLabel.append(mode);
    const positions = formSection("保存位置"),
      archiving = formSection("归档规则");
    let directoryTarget = positions.fields;
    positions.fields.append(modeLabel);
    form.append(positions.host, archiving.host);
    browser = createDirectoryPicker(host, (parent, cursor) =>
      api.vaultSave.directories(vault.id, parent, cursor),
    );
    function directoryField(title, value) {
      const label = el("label", null, "directory-field"),
        heading = el("span", null, "directory-field-heading"),
        input = el("input"),
        select = control("选择目录…", () => browser.open(select, input, title));
      setText(select, phrase("{p0}：选择目录", { p0: title }), "aria-label");
      setText(input, phrase("{p0}：相对路径", { p0: title }), "aria-label");
      input.value = value;
      input.required = true;
      input.maxLength = 1024;
      setText(
        input,
        uiText("选择已有目录，或填写待创建的相对路径"),
        "placeholder",
      );
      select.dataset.draft = "ignore";
      heading.append(el("span", uiText(title)), select);
      label.append(heading, input);
      directoryTarget.append(label);
      const field = { select, input };
      return field;
    }
    const inbox = directoryField(
        "未分类笔记目录（兼容规则包含专家 Draft）",
        state.settings.inbox_dir,
      ),
      archive = directoryField("已整理默认目录", state.settings.archive_dir),
      image = directoryField("共享图片目录", state.settings.image_dir),
      linkLabel = el("label", uiText("图片引用方式")),
      link = el("select");
    for (const [value, title] of [
      ["obsidian", "Obsidian 引用"],
      ["markdown", "Markdown 相对引用"],
    ]) {
      const option = el("option", uiText(title));
      option.value = value;
      link.append(option);
    }
    link.value = state.settings.image_link_style;
    linkLabel.append(link);
    positions.fields.append(linkLabel);
    const automatic = checkbox(
      "确认 MOC 关联后准备归档",
      state.settings.archive_on_moc,
    );
    archiving.fields.append(automatic.label);
    const roles = createRoleFields(
      archiving.fields,
      directoryField,
      state.settings,
      {
        mode,
        inbox,
        archive,
        image,
        legacyArchive: automatic.input,
        legacyArchiveLabel: automatic.label,
      },
    );
    const enable = checkbox("确认启用本规则用于随后新准备的发布", false);
    form.append(enable.label);
    directoryTarget = archiving.fields;
    for (const moc of state.mocs) {
      const field = directoryField(
        "MOC「" + moc.title + "」归档目录",
        state.settings.moc_destinations[moc.id] || "",
      );
      field.input.required = false;
      setText(field.input, uiText("留空使用已整理默认目录"), "placeholder");
      mappings.push([moc.id, field]);
    }
    if (state.mocs_truncated)
      form.append(el("p", uiText("MOC 较多，本页仅显示前 100 项。"), "muted"));
    let preview = null,
      version = state.revision;
    const invalid = () => {
      preview = null;
      previewArea.replaceChildren();
      save.dataset.policyDisabled = "true";
      save.disabled = true;
    };
    const save = control("确认保存预览中的规则", async () => {
      if (!preview) return;
      const submitted = preview,
        acknowledge = captureDraftSave(form);
      let saved;
      try {
        saved = await api.vaultSave.save(vault.id, {
          preview_id: submitted.preview_id,
          expected_revision: version,
        });
      } catch (error) {
        if (epoch === generation) {
          invalid();
          preflight.dataset.policyDisabled = "true";
          preflight.disabled = true;
        }
        throw new Error(error.message + " 请点“重新读取规则”核对保存状态。", {
          cause: error,
        });
      }
      if (epoch !== generation) return;
      version = saved.revision;
      acknowledge();
      invalid();
      notify(
        saved.publication_ready
          ? "保存规则已启用，将用于随后新准备的发布。"
          : "规则草稿已保存；随后新准备的发布继续使用独立文件夹。",
      );
      await browser.refresh();
    });
    save.dataset.policyDisabled = "true";
    save.disabled = true;
    const preflight = control("预览保存规则", async () => {
      if (!form.reportValidity()) return;
      const value = read(),
        stamp = JSON.stringify([value, enable.input.checked]),
        result = await api.vaultSave.preview(vault.id, {
          expected_revision: version,
          settings: value,
          enable_publication: enable.input.checked,
        });
      if (
        epoch !== generation ||
        stamp !== JSON.stringify([read(), enable.input.checked])
      )
        return;
      preview = result;
      previewArea.replaceChildren();
      for (const [kind, path] of Object.entries(result.paths)) {
        const names = {
          note: "新笔记",
          archived_note: "归档笔记",
          image: "图片",
          expert_package: "专家 Draft 包",
          moc: "新建 MOC",
          original: "非图片原件",
        };
        previewArea.append(el("p", `${names[kind] || kind}：${path}`));
      }
      previewArea.append(
        el(
          "p",
          result.publication_ready
            ? uiText("确认后启用此规则。")
            : uiText("仅保存草稿，不启用。"),
        ),
      );
      previewArea.append(
        el(
          "p",
          result.create_directories.length
            ? phrase("保存时创建：{p0}", {
                p0: result.create_directories.join("、"),
              })
            : uiText("所选目录已存在。"),
        ),
      );
      save.dataset.policyDisabled = "false";
      save.disabled = false;
    });
    function read() {
      return {
        layout_mode: mode.value,
        inbox_dir: inbox.input.value,
        archive_dir: archive.input.value,
        image_dir: image.input.value,
        image_link_style: link.value,
        archive_on_moc: roles.isUnified() ? false : automatic.input.checked,
        ...roles.read(),
        moc_destinations: Object.fromEntries(
          mappings
            .filter(([, f]) => f.input.value)
            .map(([id, f]) => [id, f.input.value]),
        ),
      };
    }
    form.addEventListener("input", invalid);
    form.addEventListener("change", invalid);
    host.append(
      heading,
      helpTip(
        "目录选择说明",
        "逐级选择已有目录，或填写相对路径。新目录只在确认保存时创建。",
      ),
      el(
        "p",
        state.publication_ready
          ? uiText(
              "当前规则已启用。更新时须重新勾选启用并预览；只保存草稿会停止新规则。",
            )
          : uiText(
              "当前未启用新规则。勾选启用并确认预览后，规则用于新准备的发布。已有笔记需单独预览迁移。",
            ),
        "muted",
      ),
      control("重新读取规则", () => {
        active = null;
        return open(vault);
      }),
      form,
      preflight,
      previewArea,
      save,
      control("关闭规则编辑", async () => {
        if (
          !hasUnsavedChanges(host) ||
          window.confirm(translate("关闭会清除未保存的规则草稿，是否继续？"))
        )
          clear();
      }),
    );
    if (!state.binding_current)
      host.append(
        el("p", uiText("之前选定的目录已变化，请重新选择并预览。"), "error"),
      );
    if (state.pending_operation) {
      preflight.dataset.policyDisabled = "true";
      preflight.disabled = true;
      host.append(
        el(
          "p",
          uiText(
            "上次创建目录未完成。请先在本机核对已创建的空目录，再结束这次操作并重新预览。",
          ),
          "error",
        ),
        control("确认保留目录并结束待处理操作", async () => {
          await api.vaultSave.abandon(vault.id, state.pending_operation.id);
          if (epoch === generation) {
            preflight.dataset.policyDisabled = String(state.read_only);
            preflight.disabled = state.read_only;
            notify("已保留目录并结束旧操作，请重新预览规则。");
          }
        }),
      );
    }
    if (state.read_only) {
      preflight.dataset.policyDisabled = "true";
      preflight.disabled = true;
      host.append(
        el("p", uiText("此仓库只读，可查看目录，不能保存写入规则。"), "muted"),
      );
    }
    automaticPanel = automaticSettings(vault, api);
    automaticPanel.classList.add("vault-policy-section");
    automaticPanel.open = true;
    host.append(
      automaticPanel,
      disclosure("整库合并（预览后明确确认）", vaultMergeView(vault, api)),
    );
    rememberDraft(form);
    heading.focus();
    await browser.refresh();
  }
  return {
    open,
    clear,
    get vaultId() {
      return active;
    },
    async openTask(vault) {
      await open(vault);
      if (automaticPanel) revealSection(automaticPanel);
      await automaticPanel?.openTasks();
    },
  };
}
