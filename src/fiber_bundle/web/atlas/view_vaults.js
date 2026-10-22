// Vaults: list, connection jobs, and per-vault detail (rules, automation, organizing,
// merge). Default query participation and access grants stay separate settings.
import { h, clear, icon } from "./dom.js";
import { lang } from "./i18n.js";
import { strings } from "./i18n.js";
import {
  badge,
  button,
  card,
  empty,
  failure,
  listRow,
  notice,
  skeleton,
  toggle,
  facts,
  tabs,
} from "./ui.js";
import { run, toast } from "./feedback.js";
import { confirm } from "./dialog.js";
import { number, bytes } from "./format.js";
import { create as createVaultIndex } from "./vault_index.js";

export const t = strings({
  zh: {
    connect: "接入笔记仓库",
    title: "知识库",
    none: "暂无启用的知识库。接入现有 Obsidian / Markdown 目录或新建仓库。",
    readOnly: "只读接入",
    readWrite: "可读写",
    active: "已启用",
    defaultQuery: "默认参与查询",
    defaultSaved: "默认查询设置已保存，客户端权限保持独立。",
    open: "打开",
    detached: "已解除接入",
    trashed: "已移到系统废纸篓",
    reconcile: "待核对系统恢复结果",
    trash_pending: "废纸篓操作待完成",
    inactive: "需要核对的知识库",
    reattach: "重新接入并重建索引",
    restoreFirst:
      "请先在系统废纸篓将目录恢复到原位置，再重新预检；不会恢复旧授权。",
    checkRestore: "核对系统恢复结果",
    noJob: "没有可核对的操作记录。",
    restoreTitle: "确认已恢复原目录？",
    restoreBody:
      "先在系统废纸篓核对并恢复原目录。此步骤只核对目录身份，不重试移动。",
    restoreConfirm: "确认已恢复原目录",
    restoreDone: "目录身份已核对，仍需显式重新接入和设置权限。",
    folder: "目录",
    trash: "移到废纸篓",
    trashTitle: "将“{name}”移到系统废纸篓？",
    trashSize: "{files} 个文件 · {size}。访问将撤销；系统恢复后须重新接入。",
    trashSync: "此目录处于同步位置，移动会影响同步副本。",
    trashDone: "已移到系统废纸篓，旧授权保持撤销。",
    unlink: "解除接入",
    unlinkTitle: "解除接入“{name}”？",
    unlinkBody1: "原文件保留；旧访问将撤销。",
    unlinkBody2: "之后需显式重新接入、授权与建立索引，旧授权不会自动恢复。",
    unlinkConfirm: "解除接入（保留原文件）",
    unlinkDone: "已解除接入，原文件保留；旧访问已撤销。",
    jobs: "接入任务",
    noJobs: "尚无接入任务。",
    job_prepared: "待执行",
    job_launch_requested: "启动中",
    job_running: "执行中",
    job_complete: "完成",
    job_cancelled: "已取消",
    job_failed: "失败",
    job_interrupted: "已中断",
    kind_attach: "接入仓库",
    kind_copy: "复制导入",
    kind_create: "新建仓库",
    jobFailed:
      "任务未完成。已创建的部分目录保留，请核对后重新预检；不会自动重试。",
    runJob: "执行",
    cancelJob: "取消",
    back: "全部知识库",
    tab_overview: "概览",
    tab_rules: "保存规则",
    tab_automation: "自动审核与发布",
    tab_organize: "整理与迁移",
    tab_merge: "合并其他库",
    notFound: "找不到此知识库，可能已被移除或权限已变化。",
    path: "路径",
    mode: "接入方式",
    id: "标识",
    danger: "危险操作",
    dangerNote: "以下操作会撤销访问或移动文件，均需确认。",
  },
  en: {
    connect: "Connect a vault",
    title: "Vaults",
    none: "No active vaults yet. Connect an existing Obsidian / Markdown folder or create one.",
    readOnly: "Read-only",
    readWrite: "Read & write",
    active: "Active",
    defaultQuery: "Use in default queries",
    defaultSaved: "Default query setting saved; client grants are unchanged.",
    open: "Open",
    detached: "Detached",
    trashed: "Moved to system Trash",
    reconcile: "Restore result to reconcile",
    trash_pending: "Trash operation pending",
    inactive: "Vaults needing attention",
    reattach: "Reconnect and rebuild index",
    restoreFirst:
      "Restore the folder from the system Trash to its original place first, then preflight again. Old grants are not restored.",
    checkRestore: "Reconcile restore",
    noJob: "No operation record to reconcile.",
    restoreTitle: "Confirm the folder was restored?",
    restoreBody:
      "Restore the original folder from the system Trash first. This only verifies the folder identity; it doesn't retry the move.",
    restoreConfirm: "Folder is restored",
    restoreDone:
      "Folder identity verified. You still need to reconnect and set grants explicitly.",
    folder: "Folder",
    trash: "Move to Trash",
    trashTitle: "Move “{name}” to the system Trash?",
    trashSize:
      "{files} files · {size}. Access is revoked; after a restore you must reconnect.",
    trashSync:
      "This folder is in a synced location; moving it affects synced copies.",
    trashDone: "Moved to the system Trash; old grants stay revoked.",
    unlink: "Disconnect",
    unlinkTitle: "Disconnect “{name}”?",
    unlinkBody1: "Files stay where they are; existing access is revoked.",
    unlinkBody2:
      "Reconnecting, granting and indexing must be done again explicitly; old grants are not restored.",
    unlinkConfirm: "Disconnect (keep files)",
    unlinkDone: "Disconnected; files kept and old access revoked.",
    jobs: "Connection jobs",
    noJobs: "No connection jobs yet.",
    job_prepared: "Ready to run",
    job_launch_requested: "Starting",
    job_running: "Running",
    job_complete: "Complete",
    job_cancelled: "Cancelled",
    job_failed: "Failed",
    job_interrupted: "Interrupted",
    kind_attach: "Attach vault",
    kind_copy: "Copy import",
    kind_create: "New vault",
    jobFailed:
      "Job didn't finish. Partially created folders are kept; check them and preflight again. No automatic retry.",
    runJob: "Run",
    cancelJob: "Cancel",
    back: "All vaults",
    tab_overview: "Overview",
    tab_rules: "Save rules",
    tab_automation: "Automatic review & publish",
    tab_organize: "Organize & migrate",
    tab_merge: "Merge other vaults",
    notFound:
      "This vault can't be found; it may have been removed or permissions changed.",
    path: "Path",
    mode: "Access",
    id: "ID",
    danger: "Danger zone",
    dangerNote:
      "These actions revoke access or move files and always ask for confirmation.",
  },
});

export function createView(ctx, route) {
  let sub = route.sub || "",
    params = route.params || {},
    generation = 0,
    child = null,
    jobTimer = null,
    observeUntil = 0;
  const root = h("div.a-stack");

  async function trashVault(vault) {
    const preview = await ctx.api.vaultTrashPreview(vault.id);
    const body = [
      { label: t("folder"), items: [preview.root] },
      t("trashSize", {
        files: number(preview.files),
        size: bytes(preview.bytes),
      }),
    ];
    if (preview.synchronized) body.push(t("trashSync"));
    if (
      !(await confirm({
        title: t("trashTitle", { name: vault.name }),
        body,
        confirmLabel: t("trash"),
        tone: "danger",
      }))
    )
      return false;
    await ctx.api.vaultTrashConfirm(preview.id, preview.root);
    ctx.store.invalidate("vaults", "clients");
    toast(t("trashDone"), { tone: "success" });
    ctx.navigate("vaults");
    await refresh();
  }
  async function unlinkVault(vault) {
    const ok = await confirm({
      title: t("unlinkTitle", { name: vault.name }),
      body: [t("unlinkBody1"), t("unlinkBody2")],
      confirmLabel: t("unlinkConfirm"),
      tone: "danger",
    });
    if (!ok) return false;
    await ctx.api.vaultUnlink(vault.id);
    ctx.store.invalidate("vaults", "clients");
    toast(t("unlinkDone"), { tone: "success" });
    ctx.navigate("vaults");
    await refresh();
  }
  async function reconcileRestore(vault) {
    const jobs = await ctx.api.vaultTrashJobs();
    const job = jobs.find(
      (j) =>
        j.vault_id === vault.id && ["reconcile", "running"].includes(j.state),
    );
    if (!job) throw new Error(t("noJob"));
    const ok = await confirm({
      title: t("restoreTitle"),
      body: [{ label: t("folder"), items: [job.root] }, t("restoreBody")],
      confirmLabel: t("restoreConfirm"),
    });
    if (!ok) return false;
    await ctx.api.vaultRecovery(job.id, job.root);
    ctx.store.invalidate("vaults");
    toast(t("restoreDone"), { tone: "success" });
    await refresh();
  }
  function defaultSwitch(vault) {
    const control = toggle(t("defaultQuery"), vault.default_query, {
      dataset: { draft: "ignore" },
    });
    control.input.addEventListener("change", async () => {
      control.input.disabled = true;
      try {
        await ctx.api.setDefaultQuery(vault.id, control.input.checked);
        vault.default_query = control.input.checked;
        ctx.store.invalidate("vaults");
        toast(t("defaultSaved"), { tone: "success" });
      } catch (error) {
        control.input.checked = !!vault.default_query;
        toast(error.message, { tone: "error" });
      } finally {
        control.input.disabled = false;
      }
    });
    return control.label;
  }
  function vaultCard(vault) {
    return h(
      "article.a-vault-card",
      { dataset: { id: vault.id } },
      h(
        "button.a-vault-open",
        {
          type: "button",
          onclick: () => ctx.navigate("vaults", "detail", { vault: vault.id }),
        },
        h("span.a-vault-icon", icon("vaults")),
        h("span.a-vault-name", vault.name),
        h("span.a-vault-path", vault.absolute_root || vault.id),
      ),
      h(
        "div.a-vault-foot",
        badge(
          vault.read_only ? t("readOnly") : t("readWrite"),
          vault.read_only ? "info" : "success",
        ),
        defaultSwitch(vault),
        button(lang() === "en" ? "Update index" : "更新索引", {
          variant: "small",
          iconName: "refresh",
          onClick: () =>
            ctx.navigate("vaults", "detail", { vault: vault.id, index: "1" }),
        }),
      ),
    );
  }
  function inactiveRow(vault) {
    const actions = [];
    if (["detached", "trashed"].includes(vault.attachment_state))
      actions.push(
        button(t("reattach"), {
          variant: "small",
          onClick: () =>
            ctx.navigate("vaults", "connect", { reattach: vault.id }),
        }),
      );
    if (vault.attachment_state === "detached") {
      const trash = button(t("trash"), { variant: "small" });
      trash.classList.add("is-danger-outline");
      trash.addEventListener("click", () =>
        run(trash, () => trashVault(vault)),
      );
      actions.push(trash);
    }
    if (["reconcile", "trash_pending"].includes(vault.attachment_state)) {
      const check = button(t("checkRestore"), { variant: "small" });
      check.addEventListener("click", () =>
        run(check, () => reconcileRestore(vault)),
      );
      actions.push(check);
    }
    return listRow({
      iconName: "vaults",
      title: vault.name,
      detail:
        vault.attachment_state === "trashed"
          ? t("restoreFirst")
          : vault.absolute_root || "",
      status: [
        t(vault.attachment_state) || vault.attachment_state,
        ["reconcile", "trash_pending"].includes(vault.attachment_state)
          ? "waiting"
          : "info",
      ],
      actions,
    });
  }
  function jobRow(job) {
    const actions = [];
    if (job.state === "prepared") {
      const start = button(t("runJob"), { variant: "small", iconName: "play" });
      start.addEventListener("click", () =>
        run(start, async () => {
          await ctx.api.vaultStart(job.id);
          observeUntil = Date.now() + 180000;
          await refreshJobs();
        }),
      );
      actions.push(start);
    }
    if (["prepared", "launch_requested", "running"].includes(job.state)) {
      const cancel = button(t("cancelJob"), { variant: "small" });
      cancel.addEventListener("click", () =>
        run(cancel, async () => {
          await ctx.api.vaultCancel(job.id);
          await refreshJobs();
        }),
      );
      actions.push(cancel);
    }
    const progress = job.total
      ? Math.round(((job.completed || 0) / job.total) * 100)
      : 0;
    const tones = {
      complete: "success",
      failed: "error",
      interrupted: "error",
      cancelled: "info",
    };
    return h(
      "div.a-job",
      listRow({
        iconName: "layers",
        title: `${t("kind_" + job.kind) === "kind_" + job.kind ? job.kind : t("kind_" + job.kind)} · ${job.destination || ""}`,
        detail: `${number(job.completed || 0)} / ${number(job.total || 0)}`,
        status: [t("job_" + job.state), tones[job.state] || "waiting"],
        actions,
      }),
      ["running", "launch_requested"].includes(job.state)
        ? h("div.a-progress", h("i", { style: `width:${progress}%` }))
        : null,
      job.error_code ? h("p.a-inline-error.a-job-error", t("jobFailed")) : null,
    );
  }
  const jobsHost = h("div.a-rows");
  async function refreshJobs() {
    const jobs = await ctx.api.vaultJobs();
    clear(
      jobsHost,
      jobs.length
        ? jobs.map(jobRow)
        : empty(t("noJobs"), { iconName: "layers" }),
    );
    clearTimeout(jobTimer);
    if (
      Date.now() < observeUntil &&
      jobs.some((j) => ["launch_requested", "running"].includes(j.state))
    )
      jobTimer = setTimeout(() => refreshJobs().catch(() => {}), 1500);
  }
  async function showList() {
    const mine = generation;
    clear(root, skeleton(4));
    let vaults;
    try {
      vaults = await ctx.store.vaults();
    } catch (error) {
      if (mine === generation) clear(root, failure(error, refresh));
      return;
    }
    if (mine !== generation) return;
    const active = vaults.filter(
      (v) =>
        v.status === "active" &&
        (v.attachment_state ?? "attached") === "attached",
    );
    const inactive = vaults.filter((v) =>
      ["reconcile", "trash_pending"].includes(v.attachment_state),
    );
    clear(
      root,
      active.length
        ? h("div.a-vault-grid", active.map(vaultCard))
        : card(
            {},
            empty(t("none"), {
              iconName: "vaults",
              action: button(t("connect"), {
                variant: "primary",
                onClick: () => ctx.navigate("vaults", "connect"),
              }),
            }),
          ),
      inactive.length
        ? card(
            { title: t("inactive"), className: "is-flush" },
            h("div.a-rows", inactive.map(inactiveRow)),
          )
        : null,
      card({ title: t("jobs"), className: "is-flush" }, jobsHost),
    );
    await refreshJobs();
  }

  // Per-vault detail with tabs; tab modules load lazily.
  const tabLoaders = {
    rules: () => import("./vault_rules.js"),
    automation: () => import("./vault_automation.js"),
    organize: () => import("./vault_organize.js"),
    merge: () => import("./vault_merge.js"),
  };
  async function showDetail() {
    const mine = generation;
    clear(root, skeleton(4));
    let vaults;
    try {
      vaults = await ctx.store.vaults();
    } catch (error) {
      if (mine === generation) clear(root, failure(error, refresh));
      return;
    }
    if (mine !== generation) return;
    const vault = vaults.find((v) => v.id === params.vault);
    if (!vault) {
      clear(
        root,
        notice(t("notFound"), "warning"),
        button(t("back"), {
          iconName: "back",
          onClick: () => ctx.navigate("vaults"),
        }),
      );
      return;
    }
    const tab = params.tab || "overview";
    const body = h("div.a-stack");
    clear(
      root,
      h("a.a-back", { href: "#/vaults" }, icon("back"), t("back")),
      h(
        "div.a-detail-head",
        h("span.a-vault-icon.is-large", icon("vaults")),
        h(
          "div",
          h("h2", vault.name),
          h("p.a-subtle", vault.absolute_root || vault.id),
        ),
        badge(
          vault.read_only ? t("readOnly") : t("readWrite"),
          vault.read_only ? "info" : "success",
        ),
      ),
      tabs(
        ["overview", "rules", "automation", "organize", "merge"].map(
          (value) => ({ value, label: t("tab_" + value) }),
        ),
        tab,
        (value) => {
          ctx.navigate("vaults", "detail", { vault: vault.id, tab: value });
        },
        vault.name,
      ),
      button(lang() === "en" ? "Move vault location" : "迁移仓库位置", {
        iconName: "folder",
        onClick: () => ctx.navigate("vaults", "relocate", { vault: vault.id }),
      }),
      body,
    );
    if (tab === "overview") {
      child = createVaultIndex(ctx, vault);
      const indexView = child;
      const unlink = button(t("unlink"), { variant: "danger-outline" });
      unlink.addEventListener("click", () =>
        run(unlink, () => unlinkVault(vault)),
      );
      const trash = button(t("trash"), {
        variant: "danger-outline",
        iconName: "trash",
      });
      trash.addEventListener("click", () =>
        run(trash, () => trashVault(vault)),
      );
      clear(
        body,
        card(
          {},
          facts([
            [t("path"), vault.absolute_root || "—"],
            [t("mode"), vault.read_only ? t("readOnly") : t("readWrite")],
            [t("id"), h("code", vault.id)],
          ]),
          defaultSwitch(vault),
        ),
        indexView.node,
        card(
          {
            title: t("danger"),
            subtitle: t("dangerNote"),
            className: "a-danger-zone",
          },
          h("div.a-form-actions", unlink, trash),
        ),
      );
      await indexView.refresh();
      if (mine === generation && params.index === "1")
        indexView.node.scrollIntoView({ block: "nearest" });
      return;
    }
    const module = await tabLoaders[tab]();
    if (mine !== generation) return;
    child = module.create(ctx, vault);
    clear(body, child.node);
    await child.refresh?.();
  }
  async function refresh() {
    generation++;
    child?.dispose?.();
    child = null;
    clearTimeout(jobTimer);
    if (sub === "relocate") {
      const mine = generation,
        module = await import("./vault_relocate.js");
      if (mine !== generation) return;
      child = module.create(ctx, params);
      clear(root, h("a.a-back", { href: "#/vaults" }, t("back")), child.node);
      await child.refresh();
      return;
    }
    if (sub === "connect") {
      const module = await import("./vault_connect.js");
      const mine = generation;
      child = module.create(ctx, params, () => {
        observeUntil = Date.now() + 180000;
        ctx.navigate("vaults");
      });
      if (mine !== generation) return;
      clear(
        root,
        h("a.a-back", { href: "#/vaults" }, icon("back"), t("back")),
        child.node,
      );
      await child.refresh();
      return;
    }
    if (sub === "detail") return showDetail();
    if (params.vault) {
      sub = "detail";
      return showDetail();
    }
    return showList();
  }
  return {
    node: root,
    header: {
      actions: [
        button(t("connect"), {
          variant: "primary",
          iconName: "plus",
          onClick: () => ctx.navigate("vaults", "connect"),
        }),
      ],
    },
    refresh,
    update(next) {
      sub = next.sub || "";
      params = next.params || {};
      void refresh();
      return true;
    },
    dirty: () => Boolean(child?.dirty?.()),
    dispose() {
      generation++;
      clearTimeout(jobTimer);
      child?.dispose?.();
    },
  };
}
