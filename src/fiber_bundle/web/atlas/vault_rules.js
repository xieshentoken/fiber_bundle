// Per-vault save rules. Activating a layout always requires a fresh explicit preview;
// listing folders never reads note content.
import { h, clear } from "./dom.js";
import { strings } from "./i18n.js";
import {
  button,
  card,
  checkbox,
  disclosure,
  failure,
  field,
  input,
  notice,
  select,
  setOptions,
  skeleton,
  facts,
} from "./ui.js";
import { run, toast } from "./feedback.js";
import { createDirectoryTree } from "./dirtree.js";
import { captureDraftSave, rememberDraft } from "../unsaved_changes.js";

const t = strings({
  zh: {
    intro: "先展开并选择已有目录；手填的新目录只在确认保存时创建。",
    active:
      "当前规则已启用。更新时须重新勾选启用并预览；只保存草稿会停止新规则。",
    inactive:
      "当前未启用新规则。勾选启用并确认预览后，规则用于新准备的发布。已有笔记需在“整理与迁移”中单独迁移。",
    stale: "之前选定的目录已变化，请重新选择并预览。",
    pending:
      "上次创建目录未完成。请先在本机核对已创建的空目录，再结束这次操作并重新预览。",
    abandon: "确认保留目录并结束待处理操作",
    abandoned: "已保留目录并结束旧操作，请重新预览规则。",
    readOnly: "此仓库只读，可查看目录，不能保存写入规则。",
    folders: "仓库目录",
    refreshFolders: "刷新子文件夹",
    layout: "保存形式",
    legacy_bundle: "独立文件夹",
    obsidian_folders: "标题笔记与集中图片",
    version: "目录规则版本",
    v1: "兼容现有规则",
    v2: "统一知识库目录",
    preset: "填入统一库目录预设",
    inbox: "未分类笔记目录（兼容规则包含专家 Draft）",
    archive: "已整理默认目录",
    image: "共享图片目录",
    moc_dir: "MOC 默认目录",
    book_dir: "完整专家 Draft 目录",
    attachment_root: "附件根目录（不索引正文）",
    original_dir: "非图片原件与附件目录",
    link: "图片引用方式",
    obsidian: "Obsidian 引用",
    markdown: "Markdown 相对引用",
    archiveOnMoc: "确认 MOC 关联后准备归档（兼容规则）",
    mocMode: "确认 MOC 后归档方式",
    off: "关闭",
    preview_mode: "仅准备预览",
    automatic: "自动执行（普通笔记）",
    enable: "确认启用本规则用于随后新准备的发布",
    mocs: "MOC 归档目录",
    mocField: "MOC「{title}」",
    mocPlaceholder: "留空使用已整理默认目录",
    mocsTruncated: "MOC 较多，本页仅显示前 100 项。",
    pickExisting: "选择已有子文件夹",
    pathPlaceholder: "选择已有目录，或填写待创建的相对路径",
    preview: "预览保存规则",
    save: "确认保存预览中的规则",
    reload: "重新读取规则",
    previewTitle: "规则预览",
    note: "新笔记",
    archived_note: "归档笔记",
    image_path: "图片",
    expert_package: "专家 Draft 包",
    moc: "新建 MOC",
    original: "非图片原件",
    willEnable: "确认后启用此规则。",
    draftOnly: "仅保存草稿，不启用。",
    create: "保存时创建：{list}",
    exists: "所选目录已存在。",
    enabled: "保存规则已启用，将用于随后新准备的发布。",
    draftSaved: "规则草稿已保存；随后新准备的发布继续使用独立文件夹。",
    saveFailed: "请点“重新读取规则”核对保存状态。",
    groupLayout: "布局",
    groupDirs: "目录用途",
    groupMoc: "MOC",
  },
  en: {
    intro:
      "Expand and pick existing folders first; typed new folders are only created when you confirm saving.",
    active:
      "These rules are active. To update, tick “enable” again and preview; saving only a draft stops the new rules.",
    inactive:
      "No rules active yet. Tick enable and confirm the preview to use them for newly prepared publications. Existing notes are migrated separately under Organize & migrate.",
    stale: "Previously chosen folders changed. Pick them again and preview.",
    pending:
      "Last folder creation didn't finish. Check the created empty folders locally, then end that operation and preview again.",
    abandon: "Keep folders and end the pending operation",
    abandoned:
      "Folders kept and the old operation ended. Preview the rules again.",
    readOnly:
      "This vault is read-only: folders are visible, but write rules can't be saved.",
    folders: "Vault folders",
    refreshFolders: "Refresh subfolders",
    layout: "Layout",
    legacy_bundle: "Separate folder per item",
    obsidian_folders: "Titled notes with shared images",
    version: "Folder rule version",
    v1: "Compatible (existing rules)",
    v2: "Unified vault folders",
    preset: "Fill unified-vault preset",
    inbox: "Unsorted notes (compatible rules include expert drafts)",
    archive: "Default sorted folder",
    image: "Shared images folder",
    moc_dir: "Default MOC folder",
    book_dir: "Full expert draft folder",
    attachment_root: "Attachment root (not indexed)",
    original_dir: "Non-image originals and attachments",
    link: "Image link style",
    obsidian: "Obsidian links",
    markdown: "Markdown relative links",
    archiveOnMoc:
      "Prepare archiving after a MOC link is confirmed (compatible rules)",
    mocMode: "Archiving after MOC confirmation",
    off: "Off",
    preview_mode: "Prepare preview only",
    automatic: "Automatic (regular notes)",
    enable: "Enable these rules for newly prepared publications",
    mocs: "MOC archive folders",
    mocField: "MOC “{title}”",
    mocPlaceholder: "Empty uses the default sorted folder",
    mocsTruncated: "Many MOCs; only the first 100 are shown.",
    pickExisting: "Pick an existing subfolder",
    pathPlaceholder:
      "Pick an existing folder, or type a relative path to create",
    preview: "Preview rules",
    save: "Save the previewed rules",
    reload: "Reload rules",
    previewTitle: "Rule preview",
    note: "New note",
    archived_note: "Archived note",
    image_path: "Image",
    expert_package: "Expert draft package",
    moc: "New MOC",
    original: "Non-image original",
    willEnable: "Confirming enables these rules.",
    draftOnly: "Saves a draft only; not enabled.",
    create: "Created on save: {list}",
    exists: "All chosen folders exist.",
    enabled: "Save rules enabled for newly prepared publications.",
    draftSaved: "Draft saved; new publications keep using separate folders.",
    saveFailed: "Use “Reload rules” to check what was saved.",
    groupLayout: "Layout",
    groupDirs: "Folder roles",
    groupMoc: "MOC",
  },
});

export function create(ctx, vault) {
  const root = h("div.a-stack");
  let generation = 0,
    browser = null;
  async function refresh() {
    const epoch = ++generation;
    browser?.clear();
    clear(root, skeleton(6));
    let state;
    try {
      state = await ctx.api.vaultSave.get(vault.id);
    } catch (error) {
      if (epoch === generation) clear(root, failure(error, refresh));
      return;
    }
    if (epoch !== generation) return;
    const s = state.settings;
    const fields = [];
    function dirField(label, value, { required = true, placeholder } = {}) {
      const picker = select([["", t("pickExisting")]], "", {
        "aria-label": label,
        dataset: { draft: "ignore" },
      });
      const path = input({
        value,
        required,
        maxLength: 1024,
        placeholder: placeholder || t("pathPlaceholder"),
        "aria-label": label,
      });
      picker.addEventListener("change", () => {
        if (!picker.value) return;
        path.value = picker.value;
        path.dispatchEvent(new window.Event("input", { bubbles: true }));
      });
      const entry = {
        picker,
        path,
        node: h(
          "div.a-field",
          h("label", label),
          h("div.a-dir-field", picker, path),
        ),
      };
      fields.push(entry);
      return entry;
    }
    const layout = select(
      [
        ["legacy_bundle", t("legacy_bundle")],
        ["obsidian_folders", t("obsidian_folders")],
      ],
      s.layout_mode,
    );
    const version = select(
      [
        ["1", t("v1")],
        ["2", t("v2")],
      ],
      String(s.policy_version || 1),
    );
    const inbox = dirField(t("inbox"), s.inbox_dir);
    const archive = dirField(t("archive"), s.archive_dir);
    const image = dirField(t("image"), s.image_dir);
    const roles = Object.fromEntries(
      [
        ["moc_dir", "Map"],
        ["book_dir", "Book"],
        ["attachment_root", "Attachments"],
        ["original_dir", "Attachments/pristin"],
      ].map(([key, fallback]) => [key, dirField(t(key), s[key] || fallback)]),
    );
    const link = select(
      [
        ["obsidian", t("obsidian")],
        ["markdown", t("markdown")],
      ],
      s.image_link_style,
    );
    const legacyArchive = checkbox(t("archiveOnMoc"), s.archive_on_moc);
    const mocMode = select(
      [
        ["off", t("off")],
        ["preview", t("preview_mode")],
        ["automatic", t("automatic")],
      ],
      s.moc_archive_mode || "off",
    );
    const enable = checkbox(t("enable"), false);
    const mappings = state.mocs.map((moc) => [
      moc.id,
      dirField(
        t("mocField", { title: moc.title }),
        s.moc_destinations[moc.id] || "",
        { required: false, placeholder: t("mocPlaceholder") },
      ),
    ]);
    const unified = () => version.value === "2";
    function updateVersion() {
      legacyArchive.input.disabled = unified();
      for (const entry of Object.values(roles))
        entry.path.disabled = entry.picker.disabled = !unified();
      mocMode.disabled = !unified();
    }
    version.addEventListener("change", updateVersion);
    const preset = button(t("preset"), { variant: "small", iconName: "spark" });
    preset.addEventListener("click", () => {
      version.value = "2";
      layout.value = "obsidian_folders";
      inbox.path.value = "Inbox";
      archive.path.value = "Resource";
      image.path.value = "Attachments/images";
      for (const [key, value] of Object.entries({
        moc_dir: "Map",
        book_dir: "Book",
        attachment_root: "Attachments",
        original_dir: "Attachments/pristin",
      }))
        roles[key].path.value = value;
      mocMode.value = "off";
      updateVersion();
      form.dispatchEvent(new window.Event("input", { bubbles: true }));
    });
    function read() {
      return {
        layout_mode: layout.value,
        inbox_dir: inbox.path.value,
        archive_dir: archive.path.value,
        image_dir: image.path.value,
        image_link_style: link.value,
        archive_on_moc: unified() ? false : legacyArchive.input.checked,
        ...(unified()
          ? {
              policy_version: 2,
              moc_archive_mode: mocMode.value,
              ...Object.fromEntries(
                Object.entries(roles).map(([key, entry]) => [
                  key,
                  entry.path.value,
                ]),
              ),
            }
          : {}),
        moc_destinations: Object.fromEntries(
          mappings
            .filter(([, entry]) => entry.path.value)
            .map(([id, entry]) => [id, entry.path.value]),
        ),
      };
    }
    const form = h(
      "form.a-form",
      { dataset: { draft: "tracked" } },
      h(
        "fieldset.a-fieldset",
        h("legend", t("groupLayout")),
        h(
          "div.a-form-grid",
          field(t("layout"), layout),
          field(t("version"), version),
          field(t("link"), link),
        ),
        h("div", preset),
      ),
      h(
        "fieldset.a-fieldset",
        h("legend", t("groupDirs")),
        h(
          "div.a-form-grid",
          [inbox, archive, image, ...Object.values(roles)].map(
            (entry) => entry.node,
          ),
        ),
      ),
      h(
        "fieldset.a-fieldset",
        h("legend", t("groupMoc")),
        legacyArchive.label,
        field(t("mocMode"), mocMode),
        mappings.length
          ? disclosure(
              t("mocs"),
              h(
                "div.a-form-grid",
                mappings.map(([, entry]) => entry.node),
              ),
              state.mocs_truncated ? h("p.a-subtle", t("mocsTruncated")) : null,
            )
          : null,
      ),
      h("div.a-checks", enable.label),
    );
    form.addEventListener("submit", (event) => event.preventDefault());
    updateVersion();
    let preview = null,
      version_ = state.revision;
    const previewHost = h("div");
    const previewButton = button(t("preview"), { iconName: "eye" });
    const saveButton = button(t("save"), {
      variant: "primary",
      iconName: "check",
    });
    const lock = (node, locked) => {
      node.dataset.locked = String(locked);
      node.disabled = locked;
    };
    const invalid = () => {
      preview = null;
      previewHost.replaceChildren();
      lock(saveButton, true);
    };
    invalid();
    form.addEventListener("input", invalid);
    form.addEventListener("change", invalid);
    previewButton.addEventListener("click", () =>
      run(previewButton, async () => {
        if (!form.reportValidity()) return;
        const value = read(),
          stamp = JSON.stringify([value, enable.input.checked]);
        const result = await ctx.api.vaultSave.preview(vault.id, {
          expected_revision: version_,
          settings: value,
          enable_publication: enable.input.checked,
        });
        if (
          epoch !== generation ||
          stamp !== JSON.stringify([read(), enable.input.checked])
        )
          return;
        preview = result;
        clear(
          previewHost,
          card(
            { title: t("previewTitle"), className: "is-inset" },
            facts(
              Object.entries(result.paths).map(([kind, path]) => [
                t(kind === "image" ? "image_path" : kind),
                h("code", path),
              ]),
              "is-rows",
            ),
            notice(
              result.publication_ready ? t("willEnable") : t("draftOnly"),
              result.publication_ready ? "success" : "info",
            ),
            h(
              "p.a-subtle",
              result.create_directories.length
                ? t("create", { list: result.create_directories.join("、") })
                : t("exists"),
            ),
          ),
        );
        lock(saveButton, false);
      }),
    );
    saveButton.addEventListener("click", () =>
      run(saveButton, async () => {
        if (!preview) return;
        const acknowledge = captureDraftSave(form);
        let saved;
        try {
          saved = await ctx.api.vaultSave.save(vault.id, {
            preview_id: preview.preview_id,
            expected_revision: version_,
          });
        } catch (error) {
          if (epoch === generation) {
            invalid();
            lock(previewButton, true);
          }
          throw new Error(error.message + " " + t("saveFailed"), {
            cause: error,
          });
        }
        if (epoch !== generation) return;
        version_ = saved.revision;
        acknowledge();
        invalid();
        toast(saved.publication_ready ? t("enabled") : t("draftSaved"), {
          tone: "success",
        });
        await browser.refresh();
      }),
    );
    const reload = button(t("reload"), {
      variant: "ghost",
      iconName: "refresh",
    });
    reload.addEventListener("click", () => run(reload, refresh));
    const treeHost = h("div");
    browser = createDirectoryTree(
      treeHost,
      (parent, cursor) =>
        ctx.api.vaultSave.directories(vault.id, parent, cursor),
      {
        onNodes: (nodes) => {
          for (const entry of fields) {
            setOptions(entry.picker, [
              ["", t("pickExisting")],
              ...nodes.map((n) => ({
                value: n.relative_path,
                label: n.relative_path,
                disabled: !n.selectable,
              })),
            ]);
            entry.picker.value = nodes.some(
              (n) => n.relative_path === entry.path.value,
            )
              ? entry.path.value
              : "";
          }
        },
      },
    );
    const refreshFolders = button(t("refreshFolders"), {
      variant: "small",
      iconName: "refresh",
    });
    refreshFolders.addEventListener("click", () =>
      run(refreshFolders, async () => {
        invalid();
        await browser.refresh();
      }),
    );
    const extras = [];
    if (!state.binding_current) extras.push(notice(t("stale"), "error"));
    if (state.pending_operation) {
      lock(previewButton, true);
      const abandon = button(t("abandon"));
      abandon.addEventListener("click", () =>
        run(abandon, async () => {
          await ctx.api.vaultSave.abandon(vault.id, state.pending_operation.id);
          if (epoch !== generation) return;
          lock(previewButton, !!state.read_only);
          toast(t("abandoned"), { tone: "success" });
        }),
      );
      extras.push(notice(t("pending"), "error", abandon));
    }
    if (state.read_only) {
      lock(previewButton, true);
      extras.push(notice(t("readOnly"), "info"));
    }
    clear(
      root,
      notice(
        state.publication_ready ? t("active") : t("inactive"),
        state.publication_ready ? "success" : "info",
        h("p.a-subtle", t("intro")),
      ),
      ...extras,
      h(
        "div.a-rules-layout",
        card(
          {
            title: t("folders"),
            actions: refreshFolders,
            className: "a-rules-tree",
          },
          treeHost,
        ),
        card(
          { actions: reload },
          form,
          h("div.a-form-actions", previewButton, saveButton),
          previewHost,
        ),
      ),
    );
    rememberDraft(form);
    await browser.refresh();
  }
  return {
    node: root,
    refresh,
    dispose() {
      generation++;
      browser?.clear();
    },
  };
}
