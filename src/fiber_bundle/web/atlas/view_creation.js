// Documents: draft creation from a fixed local query. Files are unverified drafts until saved
// explicitly as an analysis artifact; revisions and scope expansion are confirmed.
import { h, clear, icon } from "./dom.js";
import { strings } from "./i18n.js";
import {
  badge,
  button,
  card,
  checkbox,
  codeBlock,
  disclosure,
  empty,
  facts,
  failure,
  field,
  listRow,
  notice,
  segmented,
  select,
  setOptions,
  skeleton,
  textarea,
} from "./ui.js";
import { run, toast } from "./feedback.js";
import { confirm } from "./dialog.js";
import {
  captureDraftSave,
  hasUnsavedChanges,
  rememberDraft,
} from "../unsaved_changes.js";
import { shortId } from "./format.js";

const t = strings({
  zh: {
    new: "新建文档任务",
    needQuery: "先在“提问与检索”完成或固定一次本地查询，再在此准备文档任务。",
    identity: "任务身份",
    query: "固定查询输入",
    format: "格式",
    md: "Markdown 草稿",
    txt: "文本草稿",
    requirements: "制作要求",
    prepare: "准备文档任务",
    prepared: "文档要求已准备；没有启动模型或交付。",
    tasks: "文档任务",
    none: "暂无文档制作任务。",
    revision: "要求修订 {n}",
    pick: "选择左侧任务查看版本、输入与交付状态。",
    state: "状态",
    rev: "修订",
    plan: "计划摘要",
    raw: "完整任务记录",
    amend: "补充要求",
    addition: "补充内容",
    newInput: "纳入固定查询输入（先预览确认）",
    keepInput: "保留当前输入",
    previewInput: "预览输入变化",
    pickInput: "请选择明确的新查询输入。",
    previewFirst: "请先预览并核对输入变化。",
    inputPreview: "输入变化预览",
    append: "确认补充到下一版本",
    appended: "补充已记录为下一版本。",
    cancel: "取消文档及后续版本",
    cancelTitle: "取消此文档及其后续版本？",
    cancelBody: "草稿、原件和费用保留。",
    keep: "保留任务",
    draft: "未核验草稿",
    download: "取回未核验草稿",
    target: "保存目标知识库",
    consent: "明确保存为分析产物，非独立证据",
    needConsent: "请明确确认保存分析产物。",
    save: "保存分析产物",
    saveState: "分析保存状态：{state}",
    scopeNote: "目标库将扩大读者范围，请核对精确保存意图后批准。",
    approveScope: "批准这份范围扩张并保存",
    scopeTitle: "批准读者范围扩张？",
    scopeBody: "批准当前 SHA 所绑定的读者范围扩张。",
    intent: "保存意图 SHA",
    approveSave: "批准并保存",
    unsavedSwitch: "补充要求尚未提交，确定切换？",
    goAsk: "前往提问与检索",
  },
  en: {
    new: "New document task",
    needQuery:
      "Complete or pin a local query on Ask & search first, then prepare a document task here.",
    identity: "Task identity",
    query: "Pinned query input",
    format: "Format",
    md: "Markdown draft",
    txt: "Text draft",
    requirements: "Requirements",
    prepare: "Prepare document task",
    prepared: "Requirements prepared; no model started and nothing delivered.",
    tasks: "Document tasks",
    none: "No document tasks yet.",
    revision: "Requirement revision {n}",
    pick: "Pick a task to see versions, inputs and delivery state.",
    state: "State",
    rev: "Revision",
    plan: "Plan digest",
    raw: "Full task record",
    amend: "Amend requirements",
    addition: "Addition",
    newInput: "Include a pinned query input (preview first)",
    keepInput: "Keep current input",
    previewInput: "Preview input change",
    pickInput: "Choose an explicit new query input.",
    previewFirst: "Preview and check the input change first.",
    inputPreview: "Input change preview",
    append: "Add to the next revision",
    appended: "Amendment recorded as the next revision.",
    cancel: "Cancel document and later revisions",
    cancelTitle: "Cancel this document and its later revisions?",
    cancelBody: "Drafts, originals and costs are kept.",
    keep: "Keep task",
    draft: "Unverified draft",
    download: "Download unverified draft",
    target: "Save to vault",
    consent:
      "Save explicitly as an analysis artifact, not independent evidence",
    needConsent: "Confirm saving as an analysis artifact first.",
    save: "Save analysis artifact",
    saveState: "Analysis save state: {state}",
    scopeNote:
      "The target vault widens who can read this. Check the exact save intent, then approve.",
    approveScope: "Approve this scope expansion and save",
    scopeTitle: "Approve widening the audience?",
    scopeBody: "Approves the audience expansion bound to this SHA.",
    intent: "Save intent SHA",
    approveSave: "Approve and save",
    unsavedSwitch: "The amendment isn't submitted. Switch anyway?",
    goAsk: "Go to Ask & search",
  },
});

export function createView(ctx, route) {
  let epoch = 0,
    clients = [],
    queries = [],
    vaults = [],
    tasks = [],
    selected = route.params?.id || null,
    format = "md";
  const formHost = h("div");
  const listHost = h("div.a-rows");
  const detail = h("div.a-stack");
  async function refresh() {
    const mine = ++epoch;
    if (!tasks.length) clear(listHost, skeleton(3));
    let data, c, q, v;
    try {
      [data, c, q, v] = await Promise.all([
        ctx.api.creationTasks(),
        ctx.store.clients({ force: true }),
        ctx.api.queryTasks(),
        ctx.store.vaults(),
      ]);
    } catch (error) {
      clear(listHost, failure(error, refresh));
      return;
    }
    if (mine !== epoch) return;
    clients = c.filter((x) => x.active);
    queries = q.tasks.filter(
      (x) => x.role === "local" && ["queued", "succeeded"].includes(x.state),
    );
    vaults = v.filter((x) => x.status === "active");
    tasks = data.tasks;
    if (!hasUnsavedChanges(formHost)) drawForm();
    drawList();
    if (selected && !hasUnsavedChanges(detail)) await openTask(selected);
    else if (!selected)
      clear(detail, card({}, empty(t("pick"), { iconName: "creation" })));
  }
  function drawForm() {
    if (!queries.length) {
      clear(
        formHost,
        notice(
          t("needQuery"),
          "info",
          button(t("goAsk"), {
            variant: "small",
            onClick: () => ctx.navigate("ask", "model"),
          }),
        ),
      );
      return;
    }
    const who = select(clients.map((x) => [x.id, x.name]));
    const input = select([]);
    const match = () =>
      setOptions(
        input,
        queries
          .filter((x) => x.client_id === who.value)
          .map((x) => [x.task_id, x.question || x.task_id]),
      );
    who.addEventListener("change", match);
    match();
    const requirements = textarea({
      required: true,
      maxLength: 16384,
      rows: 4,
    });
    const submit = h(
      "button.a-button.is-primary",
      { type: "submit" },
      icon("plus"),
      t("prepare"),
    );
    const form = h(
      "form.a-form",
      { dataset: { draft: "tracked" } },
      h("div.a-form-grid", field(t("identity"), who), field(t("query"), input)),
      h(
        "div.a-field",
        h("label", t("format")),
        segmented(
          [
            { value: "md", label: t("md") },
            { value: "txt", label: t("txt") },
          ],
          format,
          (value) => (format = value),
          t("format"),
        ),
      ),
      field(t("requirements"), requirements),
      h("div.a-form-actions", submit),
    );
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      void run(submit, async () => {
        const acknowledge = captureDraftSave(form);
        await ctx.api.submitCreation({
          client_id: who.value,
          query_task_id: input.value,
          submission_id: crypto.randomUUID(),
          requirements: requirements.value,
          requested_format: format,
        });
        acknowledge();
        requirements.value = "";
        toast(t("prepared"), { tone: "success" });
        await refresh();
      });
    });
    clear(formHost, form);
    rememberDraft(form);
  }
  function drawList() {
    clear(
      listHost,
      tasks.length
        ? tasks.map((task) =>
            listRow({
              iconName: "creation",
              title: shortId(task.task_id),
              detail: t("revision", { n: task.revision }),
              status: [
                task.state,
                task.state === "cancelled" ? "info" : "waiting",
              ],
              selected: selected === task.task_id,
              onClick: () => {
                if (
                  hasUnsavedChanges(detail) &&
                  !window.confirm(t("unsavedSwitch"))
                )
                  return;
                selected = task.task_id;
                drawList();
                void openTask(task.task_id);
              },
            }),
          )
        : empty(t("none"), { iconName: "creation" }),
    );
  }
  async function openTask(id) {
    const mine = epoch;
    clear(detail, skeleton(4));
    let value;
    try {
      value = await ctx.api.creationStatus(id);
    } catch (error) {
      clear(
        detail,
        failure(error, () => openTask(id)),
      );
      return;
    }
    if (mine !== epoch) return;
    const addition = textarea({
      maxLength: 16384,
      rows: 3,
      dataset: { draft: "tracked" },
    });
    const updated = select(
      [
        ["", t("keepInput")],
        ...queries.map((q) => [q.task_id, q.question || q.task_id]),
      ],
      "",
      { dataset: { draft: "tracked" } },
    );
    const previewHost = h("div");
    let updateHash = null;
    const previewButton = button(t("previewInput"), {
      variant: "small",
      iconName: "eye",
    });
    previewButton.addEventListener("click", () =>
      run(previewButton, async () => {
        if (!updated.value) throw new Error(t("pickInput"));
        const preview = await ctx.api.creationInputUpdate(id, updated.value);
        if (mine !== epoch) return;
        updateHash = { id: updated.value, sha: preview.preview_sha256 };
        clear(
          previewHost,
          disclosure(
            t("inputPreview"),
            codeBlock(JSON.stringify(preview, null, 2)),
          ),
        );
        previewHost.querySelector("details").open = true;
      }),
    );
    const amendBox = h(
      "div.a-form",
      { dataset: { draft: "tracked" } },
      field(t("addition"), addition),
      h("div.a-field-row", field(t("newInput"), updated), previewButton),
      previewHost,
    );
    const append = button(t("append"), { variant: "primary" });
    append.addEventListener("click", () =>
      run(append, async () => {
        if (
          updated.value &&
          (updateHash?.id !== updated.value || !updateHash.sha)
        )
          throw new Error(t("previewFirst"));
        const acknowledge = captureDraftSave(amendBox);
        await ctx.api.appendCreation(id, {
          amendment_id: crypto.randomUUID(),
          addition: addition.value,
          expected_revision: value.revision,
          expected_sha256: value.plan_sha256,
          ...(updated.value
            ? {
                updated_query_task_id: updated.value,
                expected_update_sha256: updateHash.sha,
              }
            : {}),
        });
        acknowledge();
        toast(t("appended"), { tone: "success" });
        await refresh();
      }),
    );
    const cancel = button(t("cancel"), { variant: "danger-outline" });
    cancel.addEventListener("click", () =>
      run(cancel, async () => {
        if (
          !(await confirm({
            title: t("cancelTitle"),
            body: t("cancelBody"),
            confirmLabel: t("cancel"),
            cancelLabel: t("keep"),
            tone: "danger",
          }))
        )
          return false;
        await ctx.api.cancelCreation(id, {
          expected_revision: value.revision,
          expected_sha256: value.plan_sha256,
        });
        await refresh();
      }),
    );
    const sections = [
      card(
        {
          title: shortId(id),
          subtitle: id,
          actions: badge(value.state, "info"),
        },
        facts([
          [t("state"), value.state],
          [t("rev"), String(value.revision)],
          [t("plan"), h("code", value.plan_sha256)],
        ]),
        disclosure(t("raw"), codeBlock(JSON.stringify(value, null, 2))),
      ),
      card(
        { title: t("amend") },
        amendBox,
        h("div.a-form-actions", append, cancel),
      ),
    ];
    const artifact = value.execution?.draft_version?.artifact;
    if (artifact) {
      const download = button(t("download"), { iconName: "download" });
      download.addEventListener("click", () =>
        run(download, () => ctx.api.downloadCreation(id)),
      );
      const target = select(vaults.map((v) => [v.id, v.name]));
      const consent = checkbox(t("consent"));
      const outcome = h("div.a-stack");
      const save = button(t("save"), {
        variant: "primary",
        iconName: "upload",
      });
      save.addEventListener("click", () =>
        run(save, async () => {
          if (!consent.input.checked) throw new Error(t("needConsent"));
          const body = {
            target_vault_id: target.value,
            expected_artifact_sha256: artifact.sha256,
            save_analysis: true,
          };
          const result = await ctx.api.saveCreation(id, body);
          if (mine !== epoch) return;
          if (result.state === "scope_approval_required") {
            const approve = button(t("approveScope"), { variant: "primary" });
            approve.addEventListener("click", () =>
              run(approve, async () => {
                const ok = await confirm({
                  title: t("scopeTitle"),
                  body: [
                    t("scopeBody"),
                    { label: t("intent"), items: [result.intent_sha256] },
                  ],
                  confirmLabel: t("approveSave"),
                });
                if (!ok) return false;
                await ctx.api.approveCreationSave(id, result.intent_sha256);
                await ctx.api.saveCreation(id, body);
                await refresh();
              }),
            );
            clear(
              outcome,
              notice(t("scopeNote"), "warning", approve),
              codeBlock(JSON.stringify(result, null, 2)),
            );
          } else {
            toast(t("saveState", { state: result.state }), { tone: "success" });
            clear(outcome, codeBlock(JSON.stringify(result, null, 2)));
          }
        }),
      );
      sections.push(
        card(
          { title: t("draft") },
          h("div.a-toolbar", download),
          field(t("target"), target),
          consent.label,
          h("div.a-form-actions", save),
          outcome,
        ),
      );
    }
    clear(detail, ...sections);
    rememberDraft(detail);
  }
  return {
    node: h(
      "div.a-stack",
      card({ title: t("new") }, formHost),
      h(
        "div.a-split",
        h(
          "div.a-split-list",
          card({ title: t("tasks"), className: "is-flush" }, listHost),
        ),
        detail,
      ),
    ),
    refresh,
    dirty: () => hasUnsavedChanges(detail),
    dispose: () => epoch++,
  };
}
