// Connect a vault: method → location → scope → access → read-only preflight → confirm.
// Browsing folders never starts work; any edit invalidates the preview.
import { chooseDirectory } from "../native_directory_picker.js";
import { h, clear, icon } from "./dom.js";
import { strings } from "./i18n.js";
import {
  button,
  card,
  checkbox,
  codeBlock,
  disclosure,
  facts,
  field,
  input,
  notice,
  select,
  setOptions,
  textarea,
} from "./ui.js";
import { run, toast } from "./feedback.js";
import { createDirectoryTree } from "./dirtree.js";
import { rememberDraft } from "../unsaved_changes.js";
import { bytes, number } from "./format.js";

const t = strings({
  zh: {
    title: "接入笔记仓库",
    subtitle:
      "继承已有目录政策。受保护目录在遍历前排除；预检不调用模型或 OCR。",
    s1: "方式",
    s2: "位置",
    s3: "范围",
    s4: "访问身份",
    s5: "预检并确认",
    attach: "接入现有仓库",
    attachHint: "原地读取已有 Obsidian / Markdown 目录",
    copy: "复制导入",
    copyHint: "复制到新目录，原目录保持不变",
    create: "新建仓库",
    createHint: "在新目录创建空仓库",
    name: "名称",
    source: "现有仓库绝对路径",
    sourcePlaceholder: "/path/to/Obsidian Vault",
    pickSource: "选择目录",
    target: "新目录绝对路径",
    targetPlaceholder: "/absolute/知识库/仓库名",
    targetHelp: "最后一级目录须尚不存在；不会覆盖已有文件。",
    pickTarget: "选择父目录",
    webPath: "网页版请直接填写本机绝对路径。",
    folders: "已有子文件夹（只读发现）",
    foldersHelp: "接入完成后，可在“保存规则”中指定目录用途。",
    rescan: "重新读取子文件夹",
    readOnly: "现有仓库只读接入",
    include: "包含范围",
    includeHelp: "相对路径，每行一个；留空表示整个允许范围。",
    exclude: "排除范围",
    excludeHelp: "每行一个目录或文件。",
    client: "索引与查询身份",
    original: "允许取回保存的原件",
    add: "允许入库与写入（只读库不可授予）",
    noClients: "没有启用的客户端身份。请先在 Agent 与客户端创建身份。",
    preflight: "预检接入范围",
    confirm: "确认接入并建立索引",
    preview: "接入预览",
    notes: "笔记",
    attachments: "附件",
    size: "大小",
    location: "位置",
    destination: "落点",
    warnings: "引用问题 {n} 项；源文件保持原样，复制不读取库外资源。",
    warningDetails: "查看缺失、歧义与范围问题",
    scopeDetails: "查看实际包含与排除范围",
    started: "接入与索引任务已启动；语义分析需另外执行。",
    changed: "设置已改变，请重新预检。",
  },
  en: {
    title: "Connect a note vault",
    subtitle:
      "Existing folder policies are inherited. Protected folders are excluded before traversal; preflight never calls a model or OCR.",
    s1: "Method",
    s2: "Location",
    s3: "Scope",
    s4: "Access identity",
    s5: "Preflight & confirm",
    attach: "Attach existing vault",
    attachHint: "Read an existing Obsidian / Markdown folder in place",
    copy: "Copy import",
    copyHint: "Copy into a new folder; the original stays unchanged",
    create: "New vault",
    createHint: "Create an empty vault in a new folder",
    name: "Name",
    source: "Existing vault absolute path",
    sourcePlaceholder: "/path/to/Obsidian Vault",
    pickSource: "Choose folder",
    target: "New folder absolute path",
    targetPlaceholder: "/absolute/Knowledge/VaultName",
    targetHelp: "The last folder must not exist yet; nothing is overwritten.",
    pickTarget: "Choose parent folder",
    webPath: "In the browser, type the absolute local path.",
    folders: "Existing subfolders (read-only discovery)",
    foldersHelp: "After connecting, assign folder roles under Save rules.",
    rescan: "Reload subfolders",
    readOnly: "Attach existing vault read-only",
    include: "Include",
    includeHelp:
      "Relative paths, one per line; empty means everything allowed.",
    exclude: "Exclude",
    excludeHelp: "One folder or file per line.",
    client: "Identity for indexing and queries",
    original: "Allow retrieving saved originals",
    add: "Allow filing and writing (not for read-only vaults)",
    noClients:
      "No active client identity. Create one in Agents & clients first.",
    preflight: "Preflight scope",
    confirm: "Connect and build index",
    preview: "Connection preview",
    notes: "Notes",
    attachments: "Attachments",
    size: "Size",
    location: "Location",
    destination: "Destination",
    warnings:
      "{n} reference issues. Source files stay as they are; copying never reads outside the vault.",
    warningDetails: "Show missing, ambiguous and out-of-scope references",
    scopeDetails: "Show effective include / exclude scope",
    started:
      "Connection and indexing started; semantic analysis is a separate run.",
    changed: "Settings changed — run preflight again.",
  },
});
const lines = (node) =>
  node.value
    .split("\n")
    .map((s) => s.trim())
    .filter(Boolean);

export function create(ctx, params, onStarted) {
  let mode = "attach",
    preview = null,
    revision = 0,
    directoryToken = null,
    discovery = 0;
  const name = input({ id: "vault-name", required: true, maxLength: 100 });
  const source = input({
    id: "vault-source",
    placeholder: t("sourcePlaceholder"),
  });
  const target = input({
    id: "vault-target",
    placeholder: t("targetPlaceholder"),
  });
  const readOnly = checkbox(t("readOnly"), true);
  const include = textarea({ id: "vault-include", rows: 2 });
  const exclude = textarea({ id: "vault-exclude", rows: 2 });
  const client = select([], undefined, { required: true });
  const original = checkbox(t("original"));
  const add = checkbox(t("add"));
  const previewHost = h("div.a-stack", { "aria-live": "polite" });
  const confirmButton = button(t("confirm"), {
    variant: "primary",
    iconName: "check",
  });
  confirmButton.disabled = true;
  confirmButton.dataset.locked = "true";
  const methodCards = h("div.a-choice-grid", {
    role: "radiogroup",
    "aria-label": t("s1"),
  });
  for (const [value, iconName] of [
    ["attach", "link"],
    ["copy", "copy"],
    ["create", "plus"],
  ]) {
    const option = h(
      "button.a-choice",
      {
        type: "button",
        role: "radio",
        "aria-checked": String(value === mode),
        dataset: { value },
      },
      icon(iconName),
      h("strong", t(value)),
      h("span", t(value + "Hint")),
    );
    option.addEventListener("click", () => {
      mode = value;
      for (const other of methodCards.children)
        other.setAttribute("aria-checked", String(other === option));
      layout();
      invalidate();
      void discover();
    });
    methodCards.append(option);
  }
  const tree = h("div");
  const browser = createDirectoryTree(tree, async (parent, cursor) => {
    const current = discovery;
    const result = await ctx.api.vaultSave.discover(
      directoryToken
        ? {
            directory_preview_id: directoryToken,
            relative_parent: parent,
            cursor,
          }
        : { root: source.value.trim(), scope: scopeValue() },
    );
    if (current === discovery) directoryToken = result.directory_preview_id;
    return result;
  });
  const rescan = button(t("rescan"), { variant: "small", iconName: "refresh" });
  rescan.addEventListener("click", () => run(rescan, discover));
  const folders = disclosure(
    t("folders"),
    h("p.a-subtle", t("foldersHelp")),
    rescan,
    tree,
  );
  folders.hidden = true;
  async function pickDirectory(into, append = "") {
    if (!window.fiberDesktop?.selectDirectory) {
      toast(t("webPath"), { tone: "info" });
      return;
    }
    const before = into.value;
    const path = await chooseDirectory(before);
    if (
      path &&
      into.isConnected &&
      !into.closest("[hidden]") &&
      into.value === before
    ) {
      into.value = append ? path.replace(/\/$/, "") + "/" + append : path;
      into.dispatchEvent(new window.Event("input", { bubbles: true }));
      into.dispatchEvent(new window.Event("change", { bubbles: true }));
    }
  }
  const pickSource = button(t("pickSource"), { iconName: "folder" });
  pickSource.addEventListener("click", () =>
    run(pickSource, () => pickDirectory(source)),
  );
  const pickTarget = button(t("pickTarget"), { iconName: "folder" });
  pickTarget.addEventListener("click", () =>
    run(pickTarget, () => pickDirectory(target, name.value)),
  );
  const sourceField = h(
    "div.a-field-row",
    field(t("source"), source),
    pickSource,
  );
  const targetField = h(
    "div.a-field-row",
    field(t("target"), target, { help: t("targetHelp") }),
    pickTarget,
  );
  const scopeValue = () => ({
    include: lines(include).length ? lines(include) : [""],
    exclude: lines(exclude),
  });
  function clearDirectories() {
    discovery++;
    directoryToken = null;
    browser.clear();
    folders.hidden = true;
  }
  async function discover() {
    clearDirectories();
    if (mode === "create" || !source.value.trim()) return;
    folders.hidden = false;
    try {
      await browser.refresh();
    } catch (error) {
      tree.replaceChildren(h("p.a-inline-error", error.message));
    }
  }
  for (const node of [source, include, exclude]) {
    node.addEventListener("input", clearDirectories);
    node.addEventListener("change", discover);
  }
  function layout() {
    sourceField.hidden = mode === "create";
    targetField.hidden = mode === "attach";
    readOnly.label.hidden = mode !== "attach";
    source.required = mode !== "create";
    target.required = mode !== "attach";
    const locked = mode === "attach" && readOnly.input.checked;
    add.input.disabled = locked;
    if (locked) add.input.checked = false;
  }
  readOnly.input.addEventListener("change", layout);
  function invalidate() {
    revision++;
    if (preview) clear(previewHost, notice(t("changed"), "info"));
    preview = null;
    confirmButton.disabled = true;
    confirmButton.dataset.locked = "true";
  }
  const preflight = button(t("preflight"), { iconName: "search" });
  const form = h(
    "form.a-form",
    { dataset: { draft: "tracked" } },
    h(
      "section.a-wizard-step",
      h("h3", h("span.a-step-mark", "1"), t("s1")),
      methodCards,
    ),
    h(
      "section.a-wizard-step",
      h("h3", h("span.a-step-mark", "2"), t("s2")),
      field(t("name"), name),
      sourceField,
      folders,
      targetField,
    ),
    h(
      "section.a-wizard-step",
      h("h3", h("span.a-step-mark", "3"), t("s3")),
      readOnly.label,
      h(
        "div.a-form-grid",
        field(t("include"), include, {
          help: t("includeHelp"),
          optional: true,
        }),
        field(t("exclude"), exclude, {
          help: t("excludeHelp"),
          optional: true,
        }),
      ),
    ),
    h(
      "section.a-wizard-step",
      h("h3", h("span.a-step-mark", "4"), t("s4")),
      field(t("client"), client),
      h("div.a-checks", original.label, add.label),
    ),
    h(
      "section.a-wizard-step",
      h("h3", h("span.a-step-mark", "5"), t("s5")),
      h("div.a-form-actions", preflight, confirmButton),
      previewHost,
    ),
  );
  form.addEventListener("submit", (event) => event.preventDefault());
  form.addEventListener("input", invalidate);
  form.addEventListener("change", (event) => {
    if (!event.target.closest(".a-tree")) invalidate();
  });
  preflight.addEventListener("click", () =>
    run(preflight, async () => {
      if (!form.reportValidity()) return;
      const current = ++revision;
      const result =
        mode === "create"
          ? await ctx.api.vaultCreationPreview({
              name: name.value.trim(),
              destination: target.value.trim(),
            })
          : await ctx.api.vaultPreview({
              name: name.value.trim(),
              root: source.value.trim(),
              read_only: readOnly.input.checked,
              scope: scopeValue(),
            });
      if (current !== revision) return;
      preview = result;
      clear(
        previewHost,
        card(
          { title: t("preview"), className: "is-inset" },
          facts([
            [t("notes"), number(result.notes || 0)],
            [t("attachments"), number(result.attachments || 0)],
            [t("size"), bytes(result.bytes || 0)],
            [t("location"), result.root || result.destination],
            mode !== "attach" ? [t("destination"), target.value.trim()] : null,
          ]),
          result.warning_count
            ? notice(t("warnings", { n: result.warning_count }), "warning")
            : null,
          result.warning_count
            ? disclosure(
                t("warningDetails"),
                codeBlock(JSON.stringify(result.warnings, null, 2)),
              )
            : null,
          result.effective_scope
            ? disclosure(
                t("scopeDetails"),
                codeBlock(JSON.stringify(result.effective_scope, null, 2)),
              )
            : null,
        ),
      );
      confirmButton.dataset.locked = "false";
      confirmButton.disabled = false;
    }),
  );
  confirmButton.addEventListener("click", () =>
    run(confirmButton, async () => {
      if (!preview || !form.reportValidity()) return;
      const current = ++revision;
      const result = await ctx.api.vaultPrepare({
        mode,
        name: name.value.trim(),
        preview_id: preview.preview_id || null,
        destination: mode === "attach" ? null : target.value.trim(),
        client_id: client.value,
        query: true,
        original: original.input.checked,
        add: add.input.checked,
      });
      if (current !== revision) return;
      await ctx.api.vaultStart(result.id);
      ctx.store.invalidate("vaults", "clients");
      preview = null;
      rememberDraft(form);
      toast(t("started"), { tone: "success" });
      onStarted();
    }),
  );
  layout();
  return {
    node: card({ title: t("title"), subtitle: t("subtitle") }, form),
    async refresh() {
      const [clients, vaults] = await Promise.all([
        ctx.store.clients({ force: true }),
        ctx.store.vaults(),
      ]);
      const active = clients.filter((c) => c.active);
      setOptions(
        client,
        active.map((c) => [c.id, c.name]),
        client.value || undefined,
      );
      if (!active.length) form.prepend(notice(t("noClients"), "warning"));
      const vault = params.reattach
        ? vaults.find((v) => v.id === params.reattach)
        : null;
      if (vault && !name.value) {
        mode = "attach";
        name.value = vault.name;
        source.value = vault.absolute_root || "";
        readOnly.input.checked = !!vault.read_only;
        const scope = JSON.parse(
          vault.scope_json || '{"include":[""],"exclude":[]}',
        );
        include.value = scope.include.join("\n");
        exclude.value = scope.exclude.join("\n");
        layout();
        void discover();
      }
      rememberDraft(form);
    },
    dispose() {
      revision++;
      clearDirectories();
    },
  };
}
