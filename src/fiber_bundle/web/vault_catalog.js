import { chooseDirectory, fillDirectory } from "./native_directory_picker.js";
import { phrase, uiText } from "./i18n/index.js";
// Reviewed directory admission and explicit background jobs. Browsing never starts work.
import { $, el, action, captureMessage } from "./ui.js";
import { control, showJSON, reconcileChildren } from "./record_ui.js";
import { captureDraftSave } from "./unsaved_changes.js";
import { createDirectoryBrowser } from "./vault_directories.js";
import { revealSection } from "./view_sections.js";

export function createVaultCatalogView(api) {
  const form = $("vault-admission"),
    previewRoot = $("vault-preview"),
    jobsRoot = $("vault-jobs");
  let preview = null,
    revision = 0,
    refreshRevision = 0,
    observingUntil = 0,
    timer = null,
    lastJobs = "",
    rows = new Map();
  const invalidate = () => {
    revision++;
    preview = null;
    previewRoot.replaceChildren();
    $("vault-confirm").disabled = true;
  };
  const folders = el("div"),
    tree = el("div", null, "vault-directory-tree");
  let directoryToken = null,
    discoveryRevision = 0;
  const scopeValue = () => ({
    include: lines("vault-include").length ? lines("vault-include") : [""],
    exclude: lines("vault-exclude"),
  });
  const browser = createDirectoryBrowser(tree, async (parent, cursor) => {
    const current = discoveryRevision;
    const result = await api.vaultSave.discover(
      directoryToken
        ? {
            directory_preview_id: directoryToken,
            relative_parent: parent,
            cursor,
          }
        : { root: $("vault-source").value.trim(), scope: scopeValue() },
    );
    if (current === discoveryRevision)
      directoryToken = result.directory_preview_id;
    return result;
  });
  function clearDirectories() {
    discoveryRevision++;
    directoryToken = null;
    browser.clear();
    folders.hidden = true;
  }
  async function discoverDirectories() {
    clearDirectories();
    if ($("vault-mode").value === "create" || !$("vault-source").value.trim())
      return;
    folders.hidden = false;
    const current = discoveryRevision;
    try {
      await browser.refresh();
    } catch (error) {
      if (current === discoveryRevision)
        tree.replaceChildren(el("p", uiText(error.message), "error"));
    }
  }
  folders.append(
    el(
      "p",
      uiText(
        "已有子文件夹（只读发现）。接入完成后，可在“管理 → 保存规则”中指定目录用途。",
      ),
    ),
    control("重新读取子文件夹", discoverDirectories),
    tree,
  );
  folders.hidden = true;
  $("vault-source-field").after(folders);
  for (const id of ["vault-source", "vault-include", "vault-exclude"]) {
    $(id).addEventListener("input", clearDirectories);
    $(id).addEventListener("change", discoverDirectories);
  }
  $("vault-mode").addEventListener("change", discoverDirectories);
  form.addEventListener("input", invalidate);
  form.addEventListener("change", invalidate);
  const modeLayout = () => {
    const create = $("vault-mode").value === "create";
    $("vault-source-field").hidden = create;
    $("vault-target-field").hidden = $("vault-mode").value === "attach";
    $("vault-source").required = !create;
    $("vault-target").required = $("vault-mode").value !== "attach";
    const readOnly =
      $("vault-mode").value === "attach" && $("vault-read-only").checked;
    $("vault-add").disabled = readOnly;
    if (readOnly) $("vault-add").checked = false;
  };
  $("vault-mode").addEventListener("change", modeLayout);
  $("vault-read-only").addEventListener("change", modeLayout);
  modeLayout();
  action(
    $("vault-pick-source"),
    async (notify) => {
      if (!window.fiberDesktop?.selectDirectory) {
        notify("Web 请填写本机绝对目录路径。");
        return;
      }
      await fillDirectory($("vault-source"));
    },
    { restoreFocus: true, feedback: "directory" },
  );
  action(
    $("vault-pick-target"),
    async (notify) => {
      if (!window.fiberDesktop?.selectDirectory) {
        notify("Web 请填写目标绝对路径；最后一级目录必须尚不存在。");
        return;
      }
      const input = $("vault-target"),
        previous = input.value;
      const path = await chooseDirectory($("vault-source").value);
      if (
        path &&
        input.isConnected &&
        !input.closest("[hidden]") &&
        input.value === previous
      ) {
        $("vault-target").value =
          path.replace(/\/$/, "") + "/" + $("vault-name").value;
        $("vault-target").dispatchEvent(
          new window.Event("input", { bubbles: true }),
        );
      }
    },
    { restoreFocus: true, feedback: "directory" },
  );
  form.addEventListener("submit", (event) => event.preventDefault());
  const lines = (id) =>
    $(id)
      .value.split("\n")
      .map((s) => s.trim())
      .filter(Boolean);
  const value = () => ({
    name: $("vault-name").value.trim(),
    root: $("vault-source").value.trim(),
    read_only: $("vault-read-only").checked,
    scope: {
      include: lines("vault-include").length ? lines("vault-include") : [""],
      exclude: lines("vault-exclude"),
    },
  });
  action($("vault-preflight"), async () => {
    if (!form.reportValidity()) return;
    const current = ++revision,
      mode = $("vault-mode").value;
    const result =
      mode === "create"
        ? await api.vaultCreationPreview({
            name: $("vault-name").value.trim(),
            destination: $("vault-target").value.trim(),
          })
        : await api.vaultPreview(value());
    if (current !== revision) return;
    preview = result;
    previewRoot.replaceChildren(
      el("h3", uiText("接入预览")),
      el(
        "p",
        phrase("笔记 {p0} 篇 · 附件 {p1} 个 · {p2} 字节", {
          p0: result.notes || 0,
          p1: result.attachments || 0,
          p2: (result.bytes || 0).toLocaleString(),
        }),
      ),
      el("p", result.root || result.destination),
    );
    if (mode !== "attach")
      previewRoot.append(
        el("p", phrase("落点：{p0}", { p0: $("vault-target").value.trim() })),
      );
    if (result.warning_count) {
      previewRoot.append(
        el(
          "p",
          phrase("引用问题 {p0} 项；源文件保持原样，复制不读取库外资源。", {
            p0: result.warning_count,
          }),
        ),
      );
      showJSON(previewRoot, result.warnings, "查看缺失、歧义与范围问题");
    }
    if (result.effective_scope)
      showJSON(previewRoot, result.effective_scope, "查看实际包含与排除范围");
    $("vault-confirm").disabled = false;
  });
  action($("vault-confirm"), async (notify) => {
    if (!preview || !form.reportValidity()) return;
    const acknowledge = captureDraftSave(form);
    const current = ++revision;
    const result = await api.vaultPrepare({
      mode: $("vault-mode").value,
      name: $("vault-name").value.trim(),
      preview_id: preview.preview_id || null,
      destination:
        $("vault-mode").value === "attach"
          ? null
          : $("vault-target").value.trim(),
      client_id: $("vault-client").value,
      query: true,
      original: $("vault-original").checked,
      add: $("vault-add").checked,
    });
    acknowledge();
    if (current !== revision) return;
    await api.vaultStart(result.id);
    observingUntil = Date.now() + 180000;
    invalidate();
    await refresh();
    notify("接入与索引任务已启动；语义分析需另外执行。");
  });
  function renderJobs(jobs) {
    const stamp = JSON.stringify(jobs);
    if (stamp === lastJobs) return;
    lastJobs = stamp;
    const states = {
      prepared: "待执行",
      launch_requested: "启动中",
      running: "执行中",
      complete: "完成",
      cancelled: "已取消",
      failed: "失败",
      interrupted: "已中断",
    };
    const kinds = { attach: "接入仓库", copy: "复制导入", create: "新建仓库" };
    const nodes = jobs.map((job) => {
      let entry = rows.get(job.id);
      if (entry?.stamp === JSON.stringify(job)) return entry.node;
      const node = el("article", null, "record-card");
      node.append(
        el(
          "h3",
          `${kinds[job.kind] || job.kind} · ${states[job.state] || job.state}`,
        ),
        el("p", `${job.completed || 0} / ${job.total || 0}`),
      );
      if (job.destination) node.append(el("p", job.destination));
      if (job.error_code)
        node.append(
          el(
            "p",
            uiText(
              "任务未完成。已创建的部分目录保留，请核对后重新预检；不会自动重试。",
            ),
          ),
        );
      if (job.state === "prepared")
        node.append(
          control("执行此任务", async () => {
            await api.vaultStart(job.id);
            observingUntil = Date.now() + 180000;
            await refresh();
          }),
        );
      if (["prepared", "launch_requested", "running"].includes(job.state))
        node.append(
          control("取消任务", async () => {
            await api.vaultCancel(job.id);
            await refresh();
          }),
        );
      showJSON(node, job, "任务记录");
      rows.set(job.id, { stamp: JSON.stringify(job), node });
      return node;
    });
    if (!nodes.length) nodes.push(el("p", uiText("尚无接入任务。")));
    reconcileChildren(jobsRoot, nodes);
    rows = new Map(
      jobs.filter((j) => rows.has(j.id)).map((j) => [j.id, rows.get(j.id)]),
    );
  }
  async function refresh() {
    const current = ++refreshRevision;
    const [clients, jobs] = await Promise.all([api.clients(), api.vaultJobs()]);
    if (current !== refreshRevision) return;
    const selected = $("vault-client").value;
    $("vault-client").replaceChildren(
      ...clients
        .filter((c) => c.active)
        .map((c) => {
          const option = el("option", c.name);
          option.value = c.id;
          return option;
        }),
    );
    if ([...$("vault-client").options].some((o) => o.value === selected))
      $("vault-client").value = selected;
    renderJobs(jobs);
    clearTimeout(timer);
    if (
      !$("vaults-panel").hidden &&
      Date.now() < observingUntil &&
      jobs.some((j) => ["launch_requested", "running"].includes(j.state))
    )
      timer = setTimeout(
        () => refresh().catch((e) => captureMessage("vaults")(e.message, true)),
        1500,
      );
  }
  return {
    refresh,
    prefill(vault) {
      $("vault-admission-panel").open = true;
      revealSection(form);
      $("vault-mode").value = "attach";
      $("vault-name").value = vault.name;
      $("vault-source").value = vault.absolute_root || "";
      $("vault-read-only").checked = !!vault.read_only;
      const scope = JSON.parse(
        vault.scope_json || '{"include":[""],"exclude":[]}',
      );
      $("vault-include").value = scope.include.join("\n");
      $("vault-exclude").value = scope.exclude.join("\n");
      modeLayout();
      invalidate();
      void discoverDirectories();
      form.scrollIntoView({ block: "start" });
      $("vault-client").focus();
    },
    clear() {
      clearDirectories();
      revision++;
      refreshRevision++;
      observingUntil = 0;
      clearTimeout(timer);
      preview = null;
      lastJobs = "";
      rows.clear();
      previewRoot.replaceChildren();
      jobsRoot.replaceChildren();
      form.reset();
      modeLayout();
      $("vault-confirm").disabled = true;
    },
  };
}
