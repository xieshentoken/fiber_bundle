// Whole-vault merge: explicit sources, historical files and audience → read-only preflight →
// frozen plan → verified full backup + confirmation → staged run with resume / rollback.
import { h, clear } from "./dom.js";
import { strings } from "./i18n.js";
import {
  button,
  card,
  checkbox,
  disclosure,
  empty,
  failure,
  field,
  input,
  notice,
  select,
  skeleton,
  steps,
  table,
  facts,
} from "./ui.js";
import { run, toast } from "./feedback.js";
import { confirm } from "./dialog.js";
import { rememberDraft } from "../unsaved_changes.js";
import { bytes, number } from "./format.js";

const t = strings({
  zh: {
    intro:
      "只读预检覆盖全部受管发布、来源与 MOC。历史笔记和附件须明确选择；冻结仅保存方案，执行还需完整备份及确认。源文件保留。",
    flow: ["选择", "预检", "冻结", "备份并执行"],
    owner: "执行身份",
    ownerNone: "选择具有源库和目标库查询、原件、新增权限的身份",
    sources: "源库",
    noSources: "没有其他可写的启用知识库。",
    history: "历史笔记与附件",
    loadHistory: "选择历史文件",
    historyCount: "共 {n} 个历史文件；已选 {k} 个。受管资料始终纳入。",
    selectAll: "选中全部",
    clearAll: "清除选择",
    prev: "上一页",
    next: "下一页",
    audience: "合并后目标库权限",
    audienceNote: "未选择的身份会暂停目标库权限。",
    query: "查询",
    original: "原件",
    add: "新增",
    channels: "入口切换",
    channelsNote: "相关通道会暂停，完成后由你重新启动。",
    noChannels: "没有已登记的消息入口。",
    makeDefault: "设为默认查询库",
    preflight: "只读预检合并方案",
    changed: "选择已改变，请重新预检。",
    ready: "预检通过，尚未执行",
    conflicts: "存在冲突，尚未执行",
    summary: "{n} 个文件，含原件共 {size}；保留 {k} 个未选择历史文件。",
    mapping: "路径映射",
    mappingMore: "这里只展示前 200 项；完整冻结方案保留全部清单。",
    grants: "合并后权限与入口",
    allow: "允许",
    pause: "暂停",
    pausedChannels:
      "将暂停 {p} 个相关通道，切换 {s} 个通道；相关 Agent 需重新应用配置。未列出的目标身份权限将暂停。",
    freeze: "冻结当前完整方案（不复制）",
    frozen: "方案已冻结：{id}。请在下方核对完整备份后执行。",
    operations: "合并进度与恢复",
    noOps: "此库尚无合并操作。",
    st_prepared: "方案已冻结，等待完整备份与执行确认",
    st_pausing: "暂停相关入口",
    st_copying: "复制并校验文件",
    st_originals: "复制来源原件",
    st_rebinding: "绑定笔记身份、来源与 MOC",
    st_switching: "切换权限与入口",
    st_index: "更新目标库索引",
    st_complete: "合并完成，相关通道与 Agent 保持暂停",
    st_reconcile: "已停止，等待核对或回退",
    st_rolling_back: "正在回退",
    st_rolled_back: "已回退，来源及审计保留",
    st_cancelled: "方案已取消",
    st_other: "等待核对",
    progress: "文件 {c}/{n}；源文件保留；完整备份{b}。",
    verified: "已校验",
    unverified: "尚未校验",
    opDetails: "操作标识与核对信息",
    backupNote:
      "先在“备份与恢复”创建包含全部源库、目标库和应用状态的完整加密备份。密码不会保存到方案。",
    backupPath: "完整加密备份目录",
    backupPassword: "备份密码（仅本次校验）",
    startConsent: "我已核对冻结的文件、目标权限和入口变更；执行此方案",
    start: "校验备份并开始合并",
    needConsent: "请先核对并勾选执行确认。",
    stop: "停止后续阶段",
    stopTitle: "停止合并的后续阶段？",
    stopBody: "已完成的阶段保留，可之后核对、继续或回退。",
    resume: "校验现场并继续",
    rollbackConsent:
      "回退此操作：仅删除本次创建且未修改的副本，保留源文件与审计",
    rollback: "校验并回退",
    needRollback: "请先勾选回退确认。修改过的文件会阻止回退。",
    refresh: "刷新进度",
  },
  en: {
    intro:
      "The read-only preflight covers every managed publication, source and MOC. Historical notes and attachments must be chosen explicitly. Freezing only saves the plan; running also needs a verified full backup and confirmation. Source files are kept.",
    flow: ["Select", "Preflight", "Freeze", "Back up & run"],
    owner: "Run as",
    ownerNone:
      "Pick an identity with query, original and add access on source and target",
    sources: "Source vaults",
    noSources: "No other writable active vaults.",
    history: "Historical notes and attachments",
    loadHistory: "Choose historical files",
    historyCount:
      "{n} historical files; {k} selected. Managed material is always included.",
    selectAll: "Select all",
    clearAll: "Clear",
    prev: "Previous",
    next: "Next",
    audience: "Target vault grants after merging",
    audienceNote: "Identities not selected have their target grants paused.",
    query: "Query",
    original: "Originals",
    add: "Add",
    channels: "Channel switch",
    channelsNote:
      "Related channels are paused; you restart them after completion.",
    noChannels: "No registered channels.",
    makeDefault: "Make it the default query vault",
    preflight: "Run read-only preflight",
    changed: "Selection changed — preflight again.",
    ready: "Preflight passed; nothing has run",
    conflicts: "Conflicts found; nothing has run",
    summary:
      "{n} files, {size} including originals; {k} unselected historical files kept.",
    mapping: "Path mapping",
    mappingMore:
      "Only the first 200 are shown; the frozen plan keeps the full list.",
    grants: "Grants and channels after merging",
    allow: "allowed",
    pause: "paused",
    pausedChannels:
      "{p} related channels will pause and {s} will switch; related agents must re-apply configuration. Unlisted target identities are paused.",
    freeze: "Freeze this full plan (no copying)",
    frozen: "Plan frozen: {id}. Verify a full backup below, then run it.",
    operations: "Merge progress and recovery",
    noOps: "No merge operations for this vault.",
    st_prepared: "Plan frozen; waiting for a full backup and confirmation",
    st_pausing: "Pausing related channels",
    st_copying: "Copying and verifying files",
    st_originals: "Copying source originals",
    st_rebinding: "Binding note identities, sources and MOCs",
    st_switching: "Switching grants and channels",
    st_index: "Updating target index",
    st_complete: "Merge complete; related channels and agents stay paused",
    st_reconcile: "Stopped; waiting to reconcile or roll back",
    st_rolling_back: "Rolling back",
    st_rolled_back: "Rolled back; sources and audit kept",
    st_cancelled: "Plan cancelled",
    st_other: "Waiting to reconcile",
    progress: "Files {c}/{n}; source files kept; full backup {b}.",
    verified: "verified",
    unverified: "not verified",
    opDetails: "Operation identifiers",
    backupNote:
      "First create a full encrypted backup in Backup & restore covering all source vaults, the target and app state. The password is never stored in the plan.",
    backupPath: "Full encrypted backup folder",
    backupPassword: "Backup password (for this check only)",
    startConsent:
      "I checked the frozen files, target grants and channel changes; run this plan",
    start: "Verify backup and start merge",
    needConsent: "Tick the run confirmation first.",
    stop: "Stop later stages",
    stopTitle: "Stop the remaining merge stages?",
    stopBody:
      "Completed stages are kept; you can reconcile, resume or roll back later.",
    resume: "Verify state and resume",
    rollbackConsent:
      "Roll back: only delete unmodified copies this operation created; keep sources and audit",
    rollback: "Verify and roll back",
    needRollback:
      "Tick the rollback confirmation first. Modified files block rollback.",
    refresh: "Refresh progress",
  },
});
const labels = [
  "prepared",
  "pausing",
  "copying",
  "originals",
  "rebinding",
  "switching",
  "index",
  "complete",
  "reconcile",
  "rolling_back",
  "rolled_back",
  "cancelled",
];

export function create(ctx, target) {
  const root = h("div.a-stack");
  const opsHost = h("div.a-stack");
  let generation = 0;
  async function refreshOps() {
    const values = (await ctx.api.vaultMerges.list()).filter(
      (v) => v.target_vault_id === target.id,
    );
    clear(
      opsHost,
      values.length
        ? values.map(operation)
        : empty(t("noOps"), { iconName: "layers" }),
    );
  }
  function operation(value) {
    const node = h(
      "div.a-op",
      h(
        "strong",
        labels.includes(value.state) ? t("st_" + value.state) : t("st_other"),
      ),
      h(
        "p.a-subtle",
        t("progress", {
          c: number(value.copied),
          n: number(value.files),
          b: value.backup_verified ? t("verified") : t("unverified"),
        }),
      ),
      disclosure(
        t("opDetails"),
        facts(
          [
            ["ID", h("code", value.id)],
            ["SHA-256", h("code", value.plan_sha256)],
            value.reason ? ["", value.reason] : null,
          ],
          "is-rows",
        ),
      ),
    );
    if (value.files)
      node.append(
        h(
          "div.a-progress",
          h("i", {
            style: `width:${Math.round((value.copied / value.files) * 100)}%`,
          }),
        ),
      );
    if (value.state === "prepared") {
      const path = input({ autocomplete: "off" });
      const password = input({
        type: "password",
        autocomplete: "new-password",
      });
      const agree = checkbox(t("startConsent"));
      const start = button(t("start"), {
        variant: "primary",
        iconName: "play",
      });
      start.addEventListener("click", () =>
        run(start, async () => {
          if (!agree.input.checked) throw new Error(t("needConsent"));
          const backup_password = password.value;
          password.value = "";
          await ctx.api.vaultMerges.start(value.id, {
            plan_sha256: value.plan_sha256,
            backup_path: path.value,
            backup_password,
          });
          await refreshOps();
        }),
      );
      node.append(
        h(
          "form.a-form",
          {
            onsubmit: (event) => event.preventDefault(),
            dataset: { draft: "ignore" },
          },
          notice(t("backupNote"), "warning"),
          h(
            "div.a-form-grid",
            field(t("backupPath"), path),
            field(t("backupPassword"), password),
          ),
          agree.label,
          h("div.a-form-actions", start),
        ),
      );
    }
    const actions = h("div.a-form-actions");
    if (
      !["complete", "cancelled", "rolled_back", "rolling_back"].includes(
        value.state,
      )
    ) {
      const stop = button(t("stop"), { variant: "danger-outline" });
      stop.addEventListener("click", () =>
        run(stop, async () => {
          if (
            !(await confirm({
              title: t("stopTitle"),
              body: t("stopBody"),
              confirmLabel: t("stop"),
              tone: "danger",
            }))
          )
            return false;
          await ctx.api.vaultMerges.action(
            value.id,
            "cancel",
            value.plan_sha256,
          );
          await refreshOps();
        }),
      );
      actions.append(stop);
    }
    if (
      value.state === "reconcile" &&
      !value.cancel_requested &&
      !value.rollback_started
    ) {
      const resume = button(t("resume"));
      resume.addEventListener("click", () =>
        run(resume, async () => {
          await ctx.api.vaultMerges.action(
            value.id,
            "resume",
            value.plan_sha256,
          );
          await refreshOps();
        }),
      );
      actions.append(resume);
    }
    if (["reconcile", "complete", "rolling_back"].includes(value.state)) {
      const consent = checkbox(t("rollbackConsent"));
      const rollback = button(t("rollback"), { variant: "danger-outline" });
      rollback.addEventListener("click", () =>
        run(rollback, async () => {
          if (!consent.input.checked) throw new Error(t("needRollback"));
          await ctx.api.vaultMerges.action(
            value.id,
            "rollback",
            value.plan_sha256,
          );
          await refreshOps();
        }),
      );
      node.append(consent.label);
      actions.append(rollback);
    }
    if (actions.childElementCount) node.append(actions);
    return node;
  }
  async function refresh() {
    const epoch = ++generation;
    clear(root, skeleton(5));
    let vaults, clients, channels;
    try {
      [vaults, clients, channels] = await Promise.all([
        ctx.store.vaults({ force: true }),
        ctx.store.clients({ force: true }),
        ctx.api.channels(),
      ]);
    } catch (error) {
      if (epoch === generation) clear(root, failure(error, refresh));
      return;
    }
    if (epoch !== generation) return;
    let selectionRevision = 0;
    const owner = select(
      [
        ["", t("ownerNone")],
        ...clients
          .filter((c) => c.active && !c.managed_connection_id)
          .map((c) => [c.id, c.name]),
      ],
      "",
    );
    const sources = {},
      history = {},
      audience = {},
      switches = {};
    const historyState = h("input", {
      type: "hidden",
      name: "historical_selection",
      value: "{}",
    });
    const output = h("div.a-stack", { "aria-live": "polite" });
    const form = h(
      "form.a-form",
      {
        dataset: { draft: "tracked" },
        onsubmit: (event) => event.preventDefault(),
      },
      historyState,
    );
    function historyChanged() {
      historyState.value = JSON.stringify(
        Object.fromEntries(
          Object.entries(history).map(([k, v]) => [k, [...v].sort()]),
        ),
      );
      form.dispatchEvent(new window.Event("change", { bubbles: true }));
    }
    form.addEventListener("change", () => {
      selectionRevision++;
      clear(output, notice(t("changed"), "info"));
    });
    const sourceList = h("div.a-stack");
    for (const vault of vaults.filter(
      (v) => v.id !== target.id && v.status === "active" && !v.read_only,
    )) {
      const box = checkbox(vault.name);
      sources[vault.id] = box.input;
      history[vault.id] = new Set();
      const area = h("div.a-stack");
      const load = button(t("loadHistory"), {
        variant: "small",
        iconName: "file",
      });
      load.addEventListener("click", () =>
        run(load, async () => {
          const result = await ctx.api.vaultMerges.inventory(
            vault.id,
            owner.value,
          );
          if (epoch !== generation) return;
          const files = result.files.filter((f) => !f.managed);
          let page = 0;
          const list = h("div.a-stack");
          function render() {
            clear(
              list,
              h(
                "p.a-subtle",
                t("historyCount", {
                  n: number(files.length),
                  k: number(history[vault.id].size),
                }),
              ),
              h(
                "div.a-file-list",
                files.slice(page * 100, (page + 1) * 100).map((file) => {
                  const pick = checkbox(
                    file.path,
                    history[vault.id].has(file.path),
                  );
                  pick.input.addEventListener("change", () => {
                    if (pick.input.checked) history[vault.id].add(file.path);
                    else history[vault.id].delete(file.path);
                    historyChanged();
                  });
                  return pick.label;
                }),
              ),
              h(
                "div.a-toolbar",
                page > 0
                  ? button(t("prev"), {
                      variant: "small",
                      onClick: () => (page--, render()),
                    })
                  : null,
                (page + 1) * 100 < files.length
                  ? button(t("next"), {
                      variant: "small",
                      onClick: () => (page++, render()),
                    })
                  : null,
              ),
            );
          }
          clear(
            area,
            h(
              "div.a-toolbar",
              button(t("selectAll"), {
                variant: "small",
                onClick: () => (
                  files.forEach((f) => history[vault.id].add(f.path)),
                  historyChanged(),
                  render()
                ),
              }),
              button(t("clearAll"), {
                variant: "small",
                onClick: () => (
                  history[vault.id].clear(),
                  historyChanged(),
                  render()
                ),
              }),
            ),
            list,
          );
          render();
        }),
      );
      sourceList.append(
        h(
          "div.a-source-pick",
          h("div.a-toolbar", box.label, h("span.a-spacer"), load),
          area,
        ),
      );
    }
    const rows = clients.filter((c) => c.active);
    const grantTable = table(
      [
        { label: "", value: (c) => c.name },
        ...["query", "original", "add"].map((key) => ({
          label: t(key),
          value: (c) => {
            const box = h("input", {
              type: "checkbox",
              "aria-label": `${c.name} ${t(key)}`,
              disabled: key !== "query" && !!c.managed_connection_id,
            });
            (audience[c.id] ||= {})[key] = box;
            return box;
          },
        })),
      ],
      rows,
    );
    owner.addEventListener("change", () => {
      const row = audience[owner.value];
      if (row)
        for (const box of Object.values(row))
          if (!box.disabled) box.checked = true;
    });
    const channelList = h(
      "div.a-stack",
      channels.length
        ? channels.map((channel) => {
            const box = checkbox(
              channel.settings?.name || channel.value?.name || channel.id,
            );
            switches[channel.id] = box.input;
            return box.label;
          })
        : h("p.a-subtle", t("noChannels")),
    );
    const makeDefault = checkbox(t("makeDefault"), true);
    form.append(
      field(t("owner"), owner),
      h(
        "fieldset.a-fieldset",
        h("legend", t("sources")),
        Object.keys(sources).length
          ? sourceList
          : h("p.a-subtle", t("noSources")),
      ),
      h(
        "fieldset.a-fieldset",
        h("legend", t("audience")),
        h("p.a-subtle", t("audienceNote")),
        grantTable,
      ),
      h(
        "fieldset.a-fieldset",
        h("legend", t("channels")),
        h("p.a-subtle", t("channelsNote")),
        channelList,
        makeDefault.label,
      ),
    );
    const preflight = button(t("preflight"), {
      variant: "primary",
      iconName: "search",
    });
    preflight.addEventListener("click", () =>
      run(preflight, async () => {
        const observed = selectionRevision;
        const selection = {
          client_id: owner.value,
          source_vault_ids: Object.keys(sources).filter(
            (k) => sources[k].checked,
          ),
          target_vault_id: target.id,
          historical_files: Object.fromEntries(
            Object.entries(history).map(([k, v]) => [k, [...v].sort()]),
          ),
          target_grants: Object.entries(audience)
            .filter(([, v]) => Object.values(v).some((b) => b.checked))
            .map(([client_id, v]) => ({
              client_id,
              ...Object.fromEntries(
                Object.entries(v).map(([k, b]) => [k, b.checked]),
              ),
            })),
          switch_channel_ids: Object.keys(switches).filter(
            (k) => switches[k].checked,
          ),
          switch_default: makeDefault.input.checked,
        };
        const packet = await ctx.api.vaultMerges.preview(selection);
        if (epoch !== generation || observed !== selectionRevision) return;
        const name = (id) => clients.find((c) => c.id === id)?.name || id;
        const freeze = packet.ready
          ? button(t("freeze"), { variant: "primary", iconName: "check" })
          : null;
        freeze?.addEventListener("click", () =>
          run(freeze, async () => {
            const frozen = await ctx.api.vaultMerges.freeze(
              selection,
              packet.plan_sha256,
            );
            if (epoch !== generation) return;
            toast(t("frozen", { id: frozen.id }), { tone: "success" });
            output.append(notice(t("frozen", { id: frozen.id }), "success"));
            await refreshOps();
          }),
        );
        clear(
          output,
          notice(
            h("strong", packet.ready ? t("ready") : t("conflicts")),
            packet.ready ? "success" : "error",
            h(
              "p",
              t("summary", {
                n: number(packet.files.length),
                size: bytes(packet.copy_bytes),
                k: number(packet.excluded.length),
              }),
            ),
          ),
          ...packet.conflicts
            .slice(0, 200)
            .map((c) =>
              h(
                "p.a-inline-error",
                `${c.source_path || c.path || ""} · ${c.reason}${c.reference ? " · " + c.reference : ""}`,
              ),
            ),
          disclosure(
            t("mapping"),
            h(
              "ul.a-plain-list.a-mono",
              packet.files
                .slice(0, 200)
                .map((f) => h("li", `${f.source_path} → ${f.target_path}`)),
            ),
            packet.files.length > 200
              ? h("p.a-subtle", t("mappingMore"))
              : null,
          ),
          disclosure(
            t("grants"),
            h(
              "ul.a-plain-list",
              packet.target_grants.map((g) =>
                h(
                  "li",
                  `${name(g.client_id)}: ${t("query")} ${g.query ? t("allow") : t("pause")} · ${t("original")} ${g.original ? t("allow") : t("pause")} · ${t("add")} ${g.add ? t("allow") : t("pause")}`,
                ),
              ),
            ),
            h(
              "p.a-subtle",
              t("pausedChannels", {
                p: packet.paused_channels.length,
                s: packet.switch_channel_ids.length,
              }),
            ),
          ),
          freeze ? h("div.a-form-actions", freeze) : null,
        );
      }),
    );
    const refreshButton = button(t("refresh"), {
      variant: "ghost",
      iconName: "refresh",
    });
    refreshButton.addEventListener("click", () =>
      run(refreshButton, refreshOps),
    );
    clear(
      root,
      notice(t("intro"), "warning"),
      steps(t("flow"), 0),
      card({}, form, h("div.a-form-actions", preflight), output),
      card({ title: t("operations"), actions: refreshButton }, opsHost),
    );
    rememberDraft(form);
    await refreshOps();
  }
  return { node: root, refresh, dispose: () => generation++ };
}
