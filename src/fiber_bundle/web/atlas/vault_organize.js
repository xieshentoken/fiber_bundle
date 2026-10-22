// Same-vault organizing: MOC association and archiving, location rebind after external
// moves, and optional one-at-a-time historical layout conversion. Every change is previewed
// and confirmed; old packages and attachments are kept.
import { featureControls } from "./feature_controls.js";
import { renderExternalChanges } from "../vault_external_changes.js";
import { h, clear } from "./dom.js";
import { strings } from "./i18n.js";
import {
  button,
  card,
  codeBlock,
  disclosure,
  empty,
  failure,
  field,
  input,
  listRow,
  notice,
  segmented,
  select,
  setOptions,
  skeleton,
  facts,
} from "./ui.js";
import { run, toast } from "./feedback.js";
import { confirm } from "./dialog.js";

const t = strings({
  zh: {
    intro:
      "MOC 关联与归档分别记录。历史转换仅处理你逐项选定的已发布内容，并保留旧包及附件。",
    identity: "整理使用的身份",
    needs: "需要可查询且可新增的客户端，以及可写的仓库。",
    automaticTitle: "MOC 自动归档任务",
    stopArchive: "停止此归档",
    stopTitle: "停止此归档？",
    stopBody: "已确认的 MOC 关联保留。",
    continueFrozen: "核对冻结版本并继续",
    needsSelection: "多个 MOC 对应不同目录，请在下方归档预览中明确选择。",
    pendingTitle: "待处理操作",
    kind_layout_migration: "历史布局转换",
    kind_publication_rebind: "位置重新绑定",
    kind_other: "归档或关联",
    cancelPending: "取消待确认操作",
    reconcilePending: "核对并恢复此操作",
    applyPending: "确认执行此预览",
    association: "关联状态",
    toFix: "待修复：{note} → {target}",
    mapping: "文件位置映射",
    onlyLink: "仅关联",
    rebindRequired: "笔记位置或文件身份已变化，请先在下方重新绑定。",
    blocked:
      "存在归档待核对操作，请先恢复。关联可能已完成，文件保留在操作记录中。",
    needLayout: "请先在“保存规则”中启用标题笔记布局。",
    organize: "整理笔记",
    note: "笔记",
    moc: "明确关联的 MOC",
    mocNone: "选择明确关联的 MOC",
    action: "操作",
    archive: "预览归档",
    associate_only: "仅关联 MOC",
    link_archive: "关联并归档",
    destination: "归档目录（多个 MOC 冲突时）",
    destinationNone: "选择归档目录",
    preview: "生成预览",
    current: "当前位置",
    target: "归档位置",
    associateOnly: "仅建立人工 MOC 关联，正文与图片位置保持。",
    conflict: "多个 MOC 的归档目录冲突，请选择后重新预览。",
    rewritten: "需改写的链接",
    none: "无",
    apply: "确认执行此预览",
    noNote: "没有可整理的笔记。",
    noMoc: "请选择明确关联的 MOC。",
    associated: "MOC 已关联，正文仍在当前位置。",
    archived: "归档已完成，索引等待更新。",
    noNotes: "暂无采用新布局的已发布笔记。旧目录包可在下方单独转换。",
    rebind: "外部移动或替换后重新绑定位置",
    rebindIntro:
      "只更新文件位置和身份记录，保留来源与 MOC；不修改正文。外部移动后的链接仍需核对。",
    publication: "需要重新绑定的发布内容",
    entry: "入口相对路径",
    rebindPreview: "预览重新绑定",
    rebindConfirm: "确认重新绑定",
    entrySha: "当前正文摘要",
    kept: "保留人工改动",
    rebound: "位置已重新绑定。",
    legacy: "可选：历史布局转换",
    legacyIntro:
      "每次只转换一个明确选中的已发布包。执行前生成元数据备份，旧包与图片原件全部保留。",
    loadLegacy: "读取可转换的旧包",
    legacyPick: "旧发布包",
    legacyPreview: "预览转换",
    legacyApply: "确认备份并转换所选旧包",
    legacyNone: "没有尚未转换的已发布包。",
    legacyFirst: "请先读取并选择一个旧包。",
    modified: "保留的人工修改",
    converted: "所选旧包已转换；原包与附件保留，索引需要更新。",
    busy: "当前操作尚未完成，请等待结果。",
  },
  en: {
    intro:
      "MOC association and archiving are recorded separately. Historical conversion only touches published items you pick one by one; old packages and attachments are kept.",
    identity: "Identity used for organizing",
    needs: "Needs a client with query and add access, and a writable vault.",
    automaticTitle: "Automatic MOC archiving",
    stopArchive: "Stop this archive",
    stopTitle: "Stop this archive?",
    stopBody: "Confirmed MOC links are kept.",
    continueFrozen: "Reconcile frozen version and continue",
    needsSelection:
      "Several MOCs map to different folders; choose one in the archive preview below.",
    pendingTitle: "Pending operations",
    kind_layout_migration: "Historical layout conversion",
    kind_publication_rebind: "Location rebind",
    kind_other: "Archive or link",
    cancelPending: "Cancel pending operation",
    reconcilePending: "Reconcile and recover",
    applyPending: "Apply this preview",
    association: "Association",
    toFix: "To fix: {note} → {target}",
    mapping: "File location mapping",
    onlyLink: "link only",
    rebindRequired:
      "A note location or file identity changed. Rebind it below first.",
    blocked:
      "An archive operation needs reconciling first. The link may be done; files are kept in the operation record.",
    needLayout: "Enable the titled-note layout under Save rules first.",
    organize: "Organize a note",
    note: "Note",
    moc: "MOC to link",
    mocNone: "Choose a MOC to link",
    action: "Action",
    archive: "Archive",
    associate_only: "Link MOC only",
    link_archive: "Link and archive",
    destination: "Archive folder (when MOCs conflict)",
    destinationNone: "Choose archive folder",
    preview: "Preview",
    current: "Current location",
    target: "Archive location",
    associateOnly:
      "Only records a human MOC link; note and images stay in place.",
    conflict: "MOC archive folders conflict. Choose one and preview again.",
    rewritten: "Links to rewrite",
    none: "none",
    apply: "Apply this preview",
    noNote: "No note to organize.",
    noMoc: "Choose a MOC to link.",
    associated: "MOC linked; the note stays where it is.",
    archived: "Archived; the index will update.",
    noNotes:
      "No published notes use the new layout yet. Old folder packages can be converted below.",
    rebind: "Rebind after an external move or replacement",
    rebindIntro:
      "Updates only file location and identity records; keeps sources and MOCs and never edits the note. Check links after external moves.",
    publication: "Publication to rebind",
    entry: "Entry relative path",
    rebindPreview: "Preview rebind",
    rebindConfirm: "Confirm rebind",
    entrySha: "Current note digest",
    kept: "Human edits kept",
    rebound: "Location rebound.",
    legacy: "Optional: historical layout conversion",
    legacyIntro:
      "Converts one explicitly chosen published package at a time. A metadata backup is made first; old packages and image originals are all kept.",
    loadLegacy: "Load convertible packages",
    legacyPick: "Old package",
    legacyPreview: "Preview conversion",
    legacyApply: "Back up and convert the chosen package",
    legacyNone: "No unconverted published packages.",
    legacyFirst: "Load and pick a package first.",
    modified: "Human edits kept",
    converted:
      "Package converted; originals and attachments kept. The index needs updating.",
    busy: "An operation is still running; wait for the result.",
  },
});

export function create(ctx, vault) {
  const root = h("div.a-stack");
  const body = h("div.a-stack");
  const external = h("div");
  let externalControls = null;
  let generation = 0,
    load = 0,
    busy = false;
  const api = ctx.api.vaultPlacements;
  const issues = (list) =>
    list.map((issue) =>
      notice(
        t("toFix", { note: issue.note_path, target: issue.target }),
        "error",
      ),
    );
  async function refresh() {
    const epoch = ++generation;
    externalControls?.dispose();
    externalControls = featureControls(external);
    clear(root, skeleton(4));
    let clients;
    try {
      clients = await ctx.store.clients();
    } catch (error) {
      if (epoch === generation) clear(root, failure(error, refresh));
      return;
    }
    if (epoch !== generation) return;
    const eligible = clients.filter(
      (c) =>
        c.active &&
        !c.managed_connection_id &&
        c.grants.some(
          (g) => g.vault_id === vault.id && g.can_query && g.can_add,
        ),
    );
    if (vault.read_only || !eligible.length) {
      clear(root, notice(t("intro")), notice(t("needs"), "warning"));
      return;
    }
    const who = select(
      eligible.map((c) => [c.id, c.name]),
      undefined,
      { dataset: { draft: "ignore" } },
    );
    who.addEventListener("change", () => void loadState());
    clear(
      root,
      notice(t("intro")),
      card({}, field(t("identity"), who)),
      externalControls.node,
      body,
    );
    async function loadState() {
      const stamp = ++load;
      const scope = { client_id: who.value, vault_id: vault.id };
      renderExternalChanges(
        api,
        external,
        scope,
        () => epoch === generation && stamp === load,
      );
      clear(body, skeleton(4));
      let current;
      try {
        current = await api.list(scope.client_id, scope.vault_id);
      } catch (error) {
        if (stamp === load) clear(body, failure(error, loadState));
        return;
      }
      if (epoch !== generation || stamp !== load) return;
      const valid = () => epoch === generation && stamp === load;
      async function mutate(work) {
        if (busy) throw new Error(t("busy"));
        busy = true;
        body.inert = true;
        try {
          const result = await work();
          if (valid()) await loadState();
          return result;
        } finally {
          busy = false;
          body.inert = false;
        }
      }
      const sections = [];
      if (current.automatic_archives?.length)
        sections.push(
          card(
            { title: t("automaticTitle"), className: "is-flush" },
            h(
              "div.a-rows",
              current.automatic_archives.map((task) => {
                const actions = [];
                if (
                  [
                    "queued",
                    "needs_review",
                    "needs_selection",
                    "reconcile",
                    "index",
                  ].includes(task.state)
                ) {
                  const stop = button(t("stopArchive"), { variant: "small" });
                  stop.addEventListener("click", () =>
                    run(stop, async () => {
                      if (
                        !(await confirm({
                          title: t("stopTitle"),
                          body: t("stopBody"),
                          confirmLabel: t("stopArchive"),
                          tone: "danger",
                        }))
                      )
                        return false;
                      await mutate(() =>
                        api.automaticAction(task.id, "cancel", scope),
                      );
                    }),
                  );
                  actions.push(stop);
                  if (!["queued", "needs_selection"].includes(task.state)) {
                    const go = button(t("continueFrozen"), {
                      variant: "small",
                    });
                    go.addEventListener("click", () =>
                      run(go, () =>
                        mutate(() =>
                          api.automaticAction(task.id, "reconcile", scope),
                        ),
                      ),
                    );
                    actions.push(go);
                  }
                }
                return listRow({
                  iconName: "automatic",
                  title: task.note_path,
                  detail:
                    task.state === "needs_selection"
                      ? t("needsSelection")
                      : task.reason || "",
                  status: [
                    task.state,
                    task.state === "reconcile" ? "error" : "waiting",
                  ],
                  actions,
                });
              }),
            ),
          ),
        );
      const pending = current.operations.filter((op) =>
        ["prepared", "applying", "reconcile"].includes(op.state),
      );
      if (pending.length)
        sections.push(
          card(
            { title: t("pendingTitle") },
            pending.map((op) => {
              const perform =
                op.kind === "layout_migration"
                  ? api.migrationAction
                  : op.kind === "publication_rebind"
                    ? api.rebindAction
                    : api.action;
              const kind = ["layout_migration", "publication_rebind"].includes(
                op.kind,
              )
                ? t("kind_" + op.kind)
                : t("kind_other");
              const primary = button(
                op.state === "prepared"
                  ? t("cancelPending")
                  : t("reconcilePending"),
                { variant: "small" },
              );
              primary.addEventListener("click", () =>
                run(primary, () =>
                  mutate(() =>
                    perform(
                      op.id,
                      op.state === "prepared" ? "cancel" : "reconcile",
                      scope,
                    ),
                  ),
                ),
              );
              const apply =
                op.state === "prepared" && op.preview?.ready
                  ? button(t("applyPending"), { variant: "primary" })
                  : null;
              apply?.addEventListener("click", () =>
                run(apply, () => mutate(() => perform(op.id, "apply", scope))),
              );
              return h(
                "div.a-op",
                h(
                  "div.a-toolbar",
                  h("strong", kind),
                  h("span.a-subtle", op.state),
                  h("span.a-spacer"),
                  primary,
                  apply,
                ),
                op.preview
                  ? h(
                      "div.a-stack",
                      facts([
                        [t("current"), h("code", op.preview.source_path)],
                        [
                          t("target"),
                          h("code", op.preview.target_path || t("onlyLink")),
                        ],
                        [t("association"), op.preview.association_state],
                      ]),
                      ...issues(op.preview.unresolved_links),
                      disclosure(
                        t("mapping"),
                        codeBlock(
                          JSON.stringify(op.preview.file_mapping, null, 2),
                        ),
                      ),
                    )
                  : null,
              );
            }),
          ),
        );
      if (!current.blocked && current.locations?.length)
        sections.push(rebindCard(current, scope, mutate, valid));
      if (!current.available) {
        sections.push(
          notice(
            current.rebind_required
              ? t("rebindRequired")
              : current.blocked
                ? t("blocked")
                : t("needLayout"),
            "warning",
          ),
        );
        clear(body, ...sections);
        return;
      }
      sections.push(
        organizeCard(current, scope, mutate, valid),
        legacyCard(scope, mutate, valid),
      );
      clear(body, ...sections);
    }
    await loadState();
  }
  function organizeCard(current, scope, mutate, valid) {
    const notes = select(
      current.notes.map((n) => [n.note_identity, n.note_path]),
    );
    const mocs = select(
      [["", t("mocNone")], ...current.mocs.map((m) => [m.id, m.title])],
      "",
    );
    let action = "archive",
      intent = null,
      ticket = 0;
    const actionPicker = segmented(
      ["archive", "associate_only", "link_archive"].map((value) => ({
        value,
        label: t(value),
      })),
      action,
      (value) => {
        action = value;
        invalidate();
      },
      t("action"),
    );
    const destination = select([["", t("destinationNone")]], "");
    const destinationField = field(t("destination"), destination);
    destinationField.hidden = true;
    const previewHost = h("div.a-stack");
    const apply = button(t("apply"), { variant: "primary", iconName: "check" });
    const invalidate = () => {
      ticket++;
      intent = null;
      previewHost.replaceChildren();
      apply.dataset.locked = "true";
      apply.disabled = true;
    };
    invalidate();
    notes.addEventListener("change", () => {
      destination.value = "";
      invalidate();
    });
    mocs.addEventListener("change", () => {
      destination.value = "";
      invalidate();
    });
    destination.addEventListener("change", invalidate);
    const previewButton = button(t("preview"), { iconName: "eye" });
    previewButton.addEventListener("click", () =>
      run(previewButton, async () => {
        const note = current.notes.find((n) => n.note_identity === notes.value);
        const moc = current.mocs.find((m) => m.id === mocs.value);
        if (!note) throw new Error(t("noNote"));
        if (action !== "archive" && !moc) throw new Error(t("noMoc"));
        const mine = ++ticket;
        const packet = await api.preview({
          ...scope,
          note_identity: note.note_identity,
          expected_sha256: note.sha256,
          action,
          ...(action !== "archive"
            ? { moc_id: moc.id, moc_revision: moc.revision }
            : {}),
          ...(destination.value ? { destination: destination.value } : {}),
        });
        if (!valid() || mine !== ticket) return;
        intent = packet;
        const previous = destination.value;
        setOptions(
          destination,
          [
            ["", t("destinationNone")],
            ...packet.destination_choices.map((p) => [p, p]),
          ],
          previous,
        );
        destinationField.hidden = packet.destination_choices.length < 2;
        clear(
          previewHost,
          facts(
            [
              [t("current"), h("code", packet.source_path)],
              [
                t("target"),
                packet.target_path
                  ? h("code", packet.target_path)
                  : action === "associate_only"
                    ? t("associateOnly")
                    : t("conflict"),
              ],
              [t("rewritten"), packet.rewritten_notes.join("、") || t("none")],
            ],
            "is-rows",
          ),
          ...issues(packet.unresolved_links),
          disclosure(
            t("mapping"),
            codeBlock(JSON.stringify(packet.file_mapping, null, 2)),
          ),
        );
        apply.dataset.locked = String(!packet.ready);
        apply.disabled = !packet.ready;
      }),
    );
    apply.addEventListener("click", () =>
      run(apply, async () => {
        if (!intent?.ready) return;
        const result = await mutate(() =>
          api.action(intent.operation_id, "apply", scope),
        );
        toast(
          result?.action === "associate_only" ? t("associated") : t("archived"),
          { tone: "success" },
        );
      }),
    );
    return card(
      { title: t("organize") },
      current.notes.length
        ? h(
            "div.a-stack",
            h(
              "div.a-form-grid",
              field(t("note"), notes),
              field(t("moc"), mocs),
            ),
            h("div.a-field", h("label", t("action")), actionPicker),
            destinationField,
            h("div.a-form-actions", previewButton, apply),
            previewHost,
          )
        : empty(t("noNotes"), { iconName: "document" }),
    );
  }
  function rebindCard(current, scope, mutate, valid) {
    const pick = select(
      current.locations.map((location, index) => [
        String(index),
        location.entry_note_path,
      ]),
    );
    const entry = input({
      maxLength: 1024,
      value: current.locations[0].entry_note_path,
    });
    const previewHost = h("div.a-stack");
    const apply = button(t("rebindConfirm"), { variant: "primary" });
    let pending = null,
      ticket = 0;
    const reset = () => {
      ticket++;
      pending = null;
      previewHost.replaceChildren();
      apply.dataset.locked = "true";
      apply.disabled = true;
    };
    reset();
    pick.addEventListener("change", () => {
      entry.value = current.locations[Number(pick.value)].entry_note_path;
      reset();
    });
    entry.addEventListener("input", reset);
    const previewButton = button(t("rebindPreview"), { iconName: "eye" });
    previewButton.addEventListener("click", () =>
      run(previewButton, async () => {
        const location = current.locations[Number(pick.value)];
        const mine = ++ticket;
        const packet = await api.rebindPreview({
          ...scope,
          kind: location.kind,
          publication_id: location.publication_id,
          expected_revision: location.revision,
          entry_note_path: entry.value,
        });
        if (!valid() || mine !== ticket) return;
        pending = packet;
        clear(
          previewHost,
          facts(
            [
              [
                t("current"),
                h("code", `${packet.source_path} → ${packet.target_path}`),
              ],
              [t("entrySha"), h("code", packet.entry_sha256)],
              packet.modified_notes.length
                ? [t("kept"), packet.modified_notes.join("、")]
                : null,
            ],
            "is-rows",
          ),
          disclosure(
            t("mapping"),
            codeBlock(JSON.stringify(packet.file_mapping, null, 2)),
          ),
        );
        apply.dataset.locked = "false";
        apply.disabled = false;
      }),
    );
    apply.addEventListener("click", () =>
      run(apply, async () => {
        if (!pending) return;
        await mutate(() =>
          api.rebindAction(pending.operation_id, "apply", scope),
        );
        toast(t("rebound"), { tone: "success" });
      }),
    );
    return disclosure(
      t("rebind"),
      h("p.a-subtle", t("rebindIntro")),
      h(
        "div.a-form-grid",
        field(t("publication"), pick),
        field(t("entry"), entry),
      ),
      h("div.a-form-actions", previewButton, apply),
      previewHost,
    );
  }
  function legacyCard(scope, mutate, valid) {
    let rows = [],
      intent = null,
      ticket = 0;
    const pick = select([]);
    const previewHost = h("div.a-stack");
    const apply = button(t("legacyApply"), { variant: "primary" });
    const reset = () => {
      ticket++;
      intent = null;
      previewHost.replaceChildren();
      apply.dataset.locked = "true";
      apply.disabled = true;
    };
    reset();
    pick.addEventListener("change", reset);
    const loadButton = button(t("loadLegacy"), { iconName: "refresh" });
    loadButton.addEventListener("click", () =>
      run(loadButton, async () => {
        const mine = ++ticket;
        const values = await api.legacy(scope.client_id, scope.vault_id);
        if (!valid() || mine !== ticket) return;
        rows = values;
        setOptions(
          pick,
          rows.map((row, index) => [String(index), row.source_path]),
        );
        if (!rows.length) clear(previewHost, h("p.a-subtle", t("legacyNone")));
      }),
    );
    const previewButton = button(t("legacyPreview"), { iconName: "eye" });
    previewButton.addEventListener("click", () =>
      run(previewButton, async () => {
        const row = rows[Number(pick.value)];
        if (!row) throw new Error(t("legacyFirst"));
        const mine = ++ticket;
        const packet = await api.migrationPreview({
          ...scope,
          kind: row.kind,
          publication_id: row.publication_id,
        });
        if (!valid() || mine !== ticket) return;
        intent = packet;
        clear(
          previewHost,
          facts(
            [
              [
                t("current"),
                h("code", `${packet.source_path} → ${packet.target_path}`),
              ],
              [t("modified"), packet.modified_notes.join("、") || t("none")],
            ],
            "is-rows",
          ),
          ...issues(packet.unresolved_links),
          disclosure(
            t("mapping"),
            codeBlock(JSON.stringify(packet.file_mapping, null, 2)),
          ),
        );
        apply.dataset.locked = String(!packet.ready);
        apply.disabled = !packet.ready;
      }),
    );
    apply.addEventListener("click", () =>
      run(apply, async () => {
        if (!intent?.ready) return;
        await mutate(() =>
          api.migrationAction(intent.operation_id, "apply", scope),
        );
        toast(t("converted"), { tone: "success" });
      }),
    );
    return disclosure(
      t("legacy"),
      h("p.a-subtle", t("legacyIntro")),
      h("div.a-toolbar", loadButton),
      field(t("legacyPick"), pick),
      h("div.a-form-actions", previewButton, apply),
      previewHost,
    );
  }
  return {
    node: root,
    refresh,
    dispose: () => {
      generation++;
      externalControls?.dispose();
      external.replaceChildren();
    },
  };
}
