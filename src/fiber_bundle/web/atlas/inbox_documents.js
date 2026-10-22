// Document and article review. Human source review only: content stays literal, no model
// calls or automatic approval; publishing confirms the exact paths first.
import { h, clear, icon } from "./dom.js";
import { createInboxHistory } from "./inbox_history.js";
import { isInboxComplete } from "../inbox_status.js";
import { strings } from "./i18n.js";
import {
  button,
  card,
  checkbox,
  empty,
  failure,
  field,
  listRow,
  notice,
  select,
  setOptions,
  skeleton,
  textarea,
  disclosure,
} from "./ui.js";
import { run, toast } from "./feedback.js";
import { confirm } from "./dialog.js";
import { confirmPublication } from "./publish.js";
import {
  articleState,
  documentState,
  publicationState,
  runState,
  verdicts,
} from "./labels.js";
import {
  captureDraftSave,
  hasUnsavedChanges,
  rememberDraft,
} from "../unsaved_changes.js";
import { shortId } from "./format.js";

const t = strings({
  zh: {
    tasks: "审核任务",
    docTask: "文档任务",
    articleTask: "文章任务",
    pages: "{n} 页",
    making: "文档制作",
    extraction: "来源提取",
    none: "暂无文档或文章任务。可从已保存的来源准备任务。",
    pick: "从左侧选择一个已提取的任务开始核对。",
    start: "确认执行",
    startTitle: "执行此固定来源任务？",
    startBody: "会使用已配置的模型额度；完成后仍需人工审核。",
    startLabel: "执行任务",
    cancel: "取消任务",
    cancelTitle: "取消此任务？",
    cancelBody: "原件和费用保留。",
    keep: "保留任务",
    review: "来源核对",
    ready: "人工审核已记录；仍未独立核验。可显式发布。",
    notReady: "提取候选尚未通过发布审核。",
    download: "下载原件并核对",
    incomplete: "来源覆盖不完整，补交完整来源后新建任务。当前候选不能发布。",
    missingRefs: "待补充附件或链接映射：{list}",
    unit: "来源单元 {n}",
    verdict: "判断",
    reason: "理由",
    reasonHint: "记录实际核对理由",
    checkSource: "我已核对原件、来源顺序及完整性",
    checkVisual: "我已核对图像、数字、单位、表格与公式",
    needChecks: "请先核对原件与视觉内容。",
    needReasons: "请为每个来源单元填写判断与理由。",
    save: "记录人工审核",
    publish: "发布已审核文档",
    saved: "人工判断已记录。",
    savedReady: "人工审核已记录；可显式发布。独立核验仍未进行。",
    savedNotReady: "审核已记录，仍有未接受单元或不完整来源。",
    unsaved: "审核表单还有未保存判断，请先记录人工审核。",
    published: "已发布：{path}。人工审核记录已冻结，独立核验仍未进行。",
    pageImage: "原页图像，需人工核对",
    embedded: "源文档内嵌图像",
    switchDirty: "审核还有未保存内容，确定切换？",
    author: "作者未知",
    date: "发布日期：{d}",
    unknownDate: "未知",
    articleOriginal: "取回保全 HTML 核对原文",
    articleImage: "原文图片 {n}",
    articlePublished: "发布状态：{state} · {path}",
    checkOriginal: "已核对原件及完整性",
    checkText: "已逐段核对正文",
    checkImages: "已核对全部图片及顺序",
    articleReason: "审核理由",
    articleNeed: "请核对原件、正文、全部图片并填写实际审核理由。",
    articleSave: "记录文章人工审核",
    articlePublish: "确认发布已审核文章",
    articleSaved: "文章人工审核已记录；尚未独立核验或发布。",
    articleDone: "文章已发布，独立核验状态保持未进行。",
    prepare: "处理已保存来源",
    prepareNote: "准备任务仅冻结来源；执行和发布分别确认。",
    identity: "来源身份",
    vault: "知识库",
    source: "已保存来源",
    load: "读取已保存来源",
    loadFirst: "请读取当前范围的已保存来源。",
    noSources: "当前范围没有可处理的本人来源。",
    noGrant: "所选身份没有当前来源的新增/原件权限。",
    kindArticle: "文章链接",
    kindDocument: "文档/文字候选",
    kindOther: "当前类型待处理",
    prepareButton: "准备来源处理任务",
    needPolicy:
      "请先保存提取配置及明确的云端执行模型配置（资料与版本 → 提取配置）。自动执行仍关闭。",
    needSource: "请选择当前支持的明确来源；图片 OCR 尚需单独配置。",
    prepared: "任务已准备，尚未调用模型或发布。",
    startPrepared: "确认执行这项来源任务",
    started: "已提交有限执行；刷新查看候选，未自动发布。",
    taskId: "任务 {id}",
  },
  en: {
    tasks: "Review tasks",
    docTask: "Document task",
    articleTask: "Article task",
    pages: "{n} pages",
    making: "Document creation",
    extraction: "Source extraction",
    none: "No document or article tasks. Prepare one from a saved source.",
    pick: "Pick an extracted task on the left to start reviewing.",
    start: "Run",
    startTitle: "Run this fixed source task?",
    startBody:
      "Uses the configured model budget; the result still needs human review.",
    startLabel: "Run task",
    cancel: "Cancel task",
    cancelTitle: "Cancel this task?",
    cancelBody: "Originals and costs are kept.",
    keep: "Keep task",
    review: "Source review",
    ready:
      "Human review recorded; not independently verified. You can publish explicitly.",
    notReady: "The extraction candidate hasn't passed review yet.",
    download: "Download the original to check",
    incomplete:
      "Source coverage is incomplete. Submit the full source in a new task; this candidate can't be published.",
    missingRefs: "Attachments or link mappings to supply: {list}",
    unit: "Source unit {n}",
    verdict: "Judgment",
    reason: "Reason",
    reasonHint: "Record what you actually checked",
    checkSource: "I checked the original, source order and completeness",
    checkVisual: "I checked images, numbers, units, tables and formulas",
    needChecks: "Check the original and the visual content first.",
    needReasons: "Give a judgment and reason for every source unit.",
    save: "Record human review",
    publish: "Publish reviewed document",
    saved: "Human judgments recorded.",
    savedReady:
      "Human review recorded; you can publish explicitly. Not independently verified.",
    savedNotReady:
      "Review recorded; some units aren't accepted or the source is incomplete.",
    unsaved: "The review form has unsaved judgments. Record the review first.",
    published:
      "Published: {path}. The review record is frozen; not independently verified.",
    pageImage: "Original page image — check manually",
    embedded: "Image embedded in the source document",
    switchDirty: "The review has unsaved input. Switch anyway?",
    author: "Unknown author",
    date: "Published: {d}",
    unknownDate: "unknown",
    articleOriginal: "Fetch the preserved HTML to check the original",
    articleImage: "Article image {n}",
    articlePublished: "Publication: {state} · {path}",
    checkOriginal: "I checked the original and completeness",
    checkText: "I checked the body paragraph by paragraph",
    checkImages: "I checked every image and their order",
    articleReason: "Review reason",
    articleNeed:
      "Check the original, the text and all images, and give a real reason.",
    articleSave: "Record article review",
    articlePublish: "Publish reviewed article",
    articleSaved: "Article review recorded; not verified or published yet.",
    articleDone: "Article published; independent verification still not done.",
    prepare: "Process a saved source",
    prepareNote:
      "Preparing only freezes the source; running and publishing are confirmed separately.",
    identity: "Source identity",
    vault: "Vault",
    source: "Saved source",
    load: "Load saved sources",
    loadFirst: "Load the saved sources for this scope.",
    noSources: "No sources of yours to process in this scope.",
    noGrant: "This identity lacks add/original access for these sources.",
    kindArticle: "Article link",
    kindDocument: "Document / text candidate",
    kindOther: "Type not yet supported",
    prepareButton: "Prepare source task",
    needPolicy:
      "Save the extraction configuration and an explicit cloud model configuration first (Sources & versions → Extraction). Automatic runs stay off.",
    needSource:
      "Choose a supported source; image OCR needs its own configuration.",
    prepared: "Task prepared; no model called and nothing published.",
    startPrepared: "Run this source task",
    started:
      "Bounded run submitted. Refresh to see the candidate; nothing is published automatically.",
    taskId: "Task {id}",
  },
});

export function assessment(packet, sourceChecked, visualChecked, decisions) {
  if (!sourceChecked || !visualChecked) throw new Error(t("needChecks"));
  if (
    decisions.length !== packet.units.length ||
    decisions.some((d) => !d.reason.trim())
  )
    throw new Error(t("needReasons"));
  return {
    source_sha256: packet.source_sha256,
    result_sha256: packet.result_sha256,
    source_reviewed: true,
    visual_reviewed: true,
    judgments: decisions,
  };
}

export function create(ctx, params) {
  const listHost = h("div.a-rows");
  const history = createInboxHistory(() => drawList(), params.id);
  const detail = h("div.a-stack");
  const prepareHost = h("div");
  const root = h(
    "div.a-stack",
    prepareHost,
    h(
      "div.a-split",
      h(
        "div.a-split-list",
        card(
          { title: t("tasks"), className: "is-flush" },
          history.node,
          listHost,
        ),
      ),
      detail,
    ),
  );
  let generation = 0,
    epoch = 0,
    selected = params.id || null,
    tasks = [];
  const confirmSwitch = () =>
    !hasUnsavedChanges(detail) || window.confirm(t("switchDirty"));

  async function startTask(id) {
    if (
      !(await confirm({
        title: t("startTitle"),
        body: t("startBody"),
        confirmLabel: t("startLabel"),
      }))
    )
      return false;
    await ctx.api.startSource(id);
    await refresh();
  }
  async function cancelTask(id) {
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
    await ctx.api.cancelExecution(id);
    await refresh();
  }
  function rowFor(task) {
    const isArticle = task.kind === "article";
    const [run_, runTone] = runState(task.run_state);
    const [state, stateTone] = isArticle
      ? articleState(task.state)
      : documentState(task.state);
    const pub = task.publication_state
      ? publicationState(task.publication_state)
      : null;
    const reviewable =
      task.run_state === "succeeded" &&
      (isArticle ? task.state === "captured" : task.state === "extracted");
    const status =
      task.run_state !== "succeeded"
        ? [run_, runTone]
        : pub && task.publication_state !== "not_published"
          ? pub
          : [state, stateTone];
    const actions = [];
    if (task.run_state === "queued" && task.binding_kind !== "document") {
      const start = button(t("start"), { variant: "small", iconName: "play" });
      start.addEventListener("click", () =>
        run(start, () => startTask(task.run_id)),
      );
      actions.push(start);
    }
    if (!isInboxComplete(task)) {
      const cancel = h(
        "button.a-icon-button",
        { type: "button", title: t("cancel"), "aria-label": t("cancel") },
        icon("trash"),
      );
      cancel.addEventListener("click", () =>
        run(cancel, () => cancelTask(task.run_id)),
      );
      actions.push(cancel);
    }
    return listRow({
      iconName: isArticle ? "article" : "document",
      title: isArticle
        ? task.title || t("articleTask")
        : task.filename || t("docTask"),
      detail: [
        isArticle
          ? null
          : task.binding_kind === "document"
            ? t("making")
            : t("extraction"),
        Number.isInteger(task.page_count)
          ? t("pages", { n: task.page_count })
          : null,
        t("taskId", { id: shortId(task.run_id) }),
      ]
        .filter(Boolean)
        .join(" · "),
      status,
      selected: selected === task.run_id,
      onClick: reviewable
        ? () => {
            if (!confirmSwitch()) return;
            selected = task.run_id;
            drawList();
            void (isArticle ? openArticle(task) : openDocument(task));
          }
        : null,
      actions,
      dataset: { id: task.run_id },
    });
  }
  function drawList() {
    const shown = history.filter(tasks);
    clear(
      listHost,
      shown.length
        ? shown.map(rowFor)
        : empty(t("none"), { iconName: "document" }),
    );
  }

  async function openDocument(task) {
    const mine = ++epoch;
    clear(detail, skeleton(6));
    let packet;
    try {
      packet = await ctx.api.documentPacket(task.run_id);
    } catch (error) {
      if (mine === epoch)
        clear(
          detail,
          failure(error, () => openDocument(task)),
        );
      return;
    }
    if (mine !== epoch) return;
    const gate = h(
      "p",
      { role: "status" },
      packet.review_gate.publication_ready ? t("ready") : t("notReady"),
    );
    const original = h(
      "a.a-button",
      {
        href: `/admin/documents/${encodeURIComponent(task.run_id)}/source`,
        download: "",
      },
      icon("download"),
      t("download"),
    );
    const texts = packet.candidate.pages || packet.candidate.segments;
    const decisions = [];
    const form = h("form.a-form", { dataset: { draft: "tracked" } });
    packet.units.forEach((unit, index) => {
      const verdict = select(verdicts(), "uncertain", {
        "aria-label": t("unit", { n: index + 1 }) + " " + t("verdict"),
      });
      const reason = textarea({
        required: true,
        maxLength: 2000,
        rows: 2,
        placeholder: t("reasonHint"),
        "aria-label": t("unit", { n: index + 1 }) + " " + t("reason"),
      });
      const locator = texts[index].locator || {
        physical_page: texts[index].physical_page,
      };
      decisions.push({ unit, verdict, reason });
      form.append(
        h(
          "fieldset.a-unit",
          h("legend", t("unit", { n: index + 1 })),
          h("div.a-unit-text", h("pre.a-pre", texts[index].text)),
          h(
            "div.a-unit-meta",
            h("code", JSON.stringify(locator)),
            unit.review_reasons?.length
              ? h("p.a-subtle", unit.review_reasons.join(" · "))
              : null,
          ),
          h(
            "div.a-form-grid",
            field(t("verdict"), verdict),
            field(t("reason"), reason),
          ),
        ),
      );
    });
    for (const item of packet.candidate.attachments || [])
      form.append(
        h(
          "figure.a-figure",
          h("img", {
            src: `/admin/documents/${encodeURIComponent(task.run_id)}/images/${encodeURIComponent(item.sha256)}`,
            alt: item.role === "page_render" ? t("pageImage") : t("embedded"),
            loading: "lazy",
          }),
        ),
      );
    const source = checkbox(t("checkSource"));
    const visual = checkbox(t("checkVisual"));
    const save = h("button.a-button", { type: "submit" }, t("save"));
    const publish = h(
      "button.a-button.is-primary",
      { type: "button", disabled: !packet.review_gate.publication_ready },
      icon("upload"),
      t("publish"),
    );
    form.append(
      h("div.a-checks", source.label, visual.label),
      h("div.a-form-actions.is-sticky", save, publish),
    );
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      void run(save, async () => {
        const acknowledge = captureDraftSave(form);
        const body = assessment(
          packet,
          source.input.checked,
          visual.input.checked,
          decisions.map(({ unit, verdict, reason }) => ({
            unit_id: unit.unit_id,
            verdict: verdict.value,
            reason: reason.value,
          })),
        );
        const result = await ctx.api.reviewDocument(task.run_id, body);
        if (mine !== epoch) return;
        acknowledge();
        publish.disabled =
          !result.review_gate.publication_ready || hasUnsavedChanges(form);
        gate.textContent = result.review_gate.publication_ready
          ? t("savedReady")
          : t("savedNotReady");
        toast(t("saved"), { tone: "success" });
      });
    });
    publish.addEventListener("click", () =>
      run(publish, async () => {
        if (hasUnsavedChanges(form)) throw new Error(t("unsaved"));
        if (!(await confirmPublication(ctx.api, "document", task.run_id)))
          return false;
        if (mine !== epoch) return;
        const result = await ctx.api.publishDocument(task.run_id);
        if (mine !== epoch) return;
        gate.textContent = t("published", {
          path: result.entry_note_path || result.relative_path,
        });
        publish.dataset.locked = "true";
        save.dataset.locked = "true";
        save.disabled = true;
        toast(gate.textContent, { tone: "success" });
      }),
    );
    rememberDraft(form);
    clear(
      detail,
      card(
        {
          title: task.filename || t("docTask"),
          subtitle: t("taskId", { id: task.run_id }),
          actions: original,
        },
        h("div.a-review-gate", icon("shield"), gate),
        packet.candidate.complete === false
          ? notice(t("incomplete"), "error")
          : null,
        packet.missing_references?.length
          ? notice(
              t("missingRefs", { list: packet.missing_references.join("、") }),
              "error",
            )
          : null,
        form,
      ),
    );
  }

  async function openArticle(task) {
    const mine = ++epoch;
    clear(detail, skeleton(6));
    let packet;
    try {
      packet = await ctx.api.articlePacket(task.run_id);
    } catch (error) {
      if (mine === epoch)
        clear(
          detail,
          failure(error, () => openArticle(task)),
        );
      return;
    }
    if (mine !== epoch) return;
    const images = packet.images.map((image) =>
      h(
        "figure.a-figure",
        h("img", {
          src: `/admin/articles/${encodeURIComponent(task.run_id)}/images/${encodeURIComponent(image.sha256)}`,
          alt: t("articleImage", { n: image.index }),
          loading: "lazy",
        }),
      ),
    );
    const original = h(
      "a.a-button",
      {
        href: `/admin/articles/${encodeURIComponent(task.run_id)}/source`,
        download: "",
      },
      icon("download"),
      t("articleOriginal"),
    );
    const body = [h("pre.a-pre.is-reading", packet.markdown), ...images];
    if (packet.publication) {
      clear(
        detail,
        card(
          {
            title: packet.title,
            subtitle: `${packet.author || t("author")} · ${t("date", { d: packet.published || t("unknownDate") })}`,
            actions: original,
          },
          notice(
            t("articlePublished", {
              state: packet.publication.state,
              path: packet.publication.relative_path,
            }),
            "success",
          ),
          ...body,
        ),
      );
      return;
    }
    const checks = [
      checkbox(t("checkOriginal")),
      checkbox(t("checkText")),
      checkbox(t("checkImages")),
    ];
    const reason = textarea({ required: true, maxLength: 2000, rows: 3 });
    const save = h("button.a-button", { type: "submit" }, t("articleSave"));
    const publish = h(
      "button.a-button.is-primary",
      { type: "button", disabled: !packet.review_ready },
      icon("upload"),
      t("articlePublish"),
    );
    const gate = h("p", { role: "status" });
    let reviewId = packet.review_id;
    const form = h(
      "form.a-form",
      { dataset: { draft: "tracked" } },
      h(
        "div.a-checks",
        checks.map((c) => c.label),
      ),
      field(t("articleReason"), reason),
      gate,
      h("div.a-form-actions.is-sticky", save, publish),
    );
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      void run(save, async () => {
        const acknowledge = captureDraftSave(form);
        if (!checks.every((c) => c.input.checked) || !reason.value.trim())
          throw new Error(t("articleNeed"));
        const result = await ctx.api.reviewArticle(task.run_id, {
          manifest_sha256: packet.manifest_sha256,
          source_reviewed: true,
          text_reviewed: true,
          images_reviewed: true,
          reason: reason.value,
        });
        if (mine !== epoch) return;
        acknowledge();
        reviewId = result.review_id;
        publish.disabled = !result.review_ready || hasUnsavedChanges(form);
        gate.textContent = t("articleSaved");
        toast(t("articleSaved"), { tone: "success" });
      });
    });
    publish.addEventListener("click", () =>
      run(publish, async () => {
        if (hasUnsavedChanges(form)) throw new Error(t("unsaved"));
        if (
          !(await confirmPublication(ctx.api, "article", task.run_id, {
            review_id: reviewId,
            manifest_sha256: packet.manifest_sha256,
          }))
        )
          return false;
        if (mine !== epoch) return;
        const result = await ctx.api.publishArticle(task.run_id, {
          review_id: reviewId,
          manifest_sha256: packet.manifest_sha256,
          confirm: true,
        });
        if (mine !== epoch) return;
        publish.dataset.locked = "true";
        save.dataset.locked = "true";
        save.disabled = true;
        gate.textContent = t("published", { path: result.note_path });
        toast(t("articleDone"), { tone: "success" });
      }),
    );
    rememberDraft(form);
    clear(
      detail,
      card(
        {
          title: packet.title,
          subtitle: `${packet.author || t("author")} · ${t("date", { d: packet.published || t("unknownDate") })}`,
          actions: original,
        },
        ...body,
        form,
      ),
    );
  }

  // Prepare a source task from saved originals (owner/vault scoped).
  let sources = [],
    requestId = null,
    policy = null;
  function prepareCard(clients, vaults) {
    const who = select(
      clients.filter((c) => c.active).map((c) => [c.id, c.name]),
      undefined,
      { dataset: { draft: "ignore" } },
    );
    const where = select(
      vaults.filter((v) => v.status === "active").map((v) => [v.id, v.name]),
      undefined,
      { dataset: { draft: "ignore" } },
    );
    const which = select([], undefined, { dataset: { draft: "ignore" } });
    const state = h("p.a-subtle", { role: "status" }, t("loadFirst"));
    const reset = () => {
      sources = [];
      requestId = null;
      setOptions(which, []);
      state.textContent = t("loadFirst");
    };
    who.addEventListener("change", reset);
    where.addEventListener("change", reset);
    which.addEventListener("change", () => (requestId = crypto.randomUUID()));
    const load = button(t("load"), { iconName: "refresh" });
    load.addEventListener("click", () =>
      run(load, async () => {
        reset();
        try {
          sources = await ctx.api.sources(who.value, where.value);
        } catch (error) {
          state.textContent =
            error.status === 404 ? t("noGrant") : error.message;
          throw error;
        }
        requestId = crypto.randomUUID();
        setOptions(
          which,
          sources.map((item, index) => [
            String(index),
            `${item.name} · ${item.kind === "article" ? t("kindArticle") : item.kind === "document" ? t("kindDocument") : t("kindOther")}`,
          ]),
        );
        state.textContent = sources.length ? t("prepareNote") : t("noSources");
      }),
    );
    const startHost = h("div");
    const prepare = button(t("prepareButton"), { variant: "primary" });
    prepare.addEventListener("click", () =>
      run(prepare, async () => {
        if (!policy?.configured) throw new Error(t("needPolicy"));
        const item = sources[Number(which.value)];
        if (!item || !["document", "article"].includes(item.kind))
          throw new Error(t("needSource"));
        const result = await ctx.api.submitSource({
          client_id: who.value,
          vault_id: where.value,
          receipt_id: item.receipt_id,
          asset_id: item.asset_id,
          source_sha256: item.sha256,
          kind: item.kind,
          submission_id: requestId,
          configuration_sha256: policy.configuration_sha256,
          model_configuration_sha256: policy.model_configuration_sha256,
        });
        toast(t("prepared"), { tone: "success" });
        await refresh();
        const start = button(t("startPrepared"), { iconName: "play" });
        start.addEventListener("click", () =>
          run(start, async () => {
            if (
              !(await confirm({
                title: t("startTitle"),
                body: t("startBody"),
                confirmLabel: t("startLabel"),
              }))
            )
              return false;
            await ctx.api.startSource(result.run_id);
            toast(t("started"), { tone: "success" });
            start.remove();
            await refresh();
          }),
        );
        clear(startHost, start);
      }),
    );
    return disclosure(
      h("span", icon("plus"), " ", t("prepare")),
      h("div.a-form-grid", field(t("identity"), who), field(t("vault"), where)),
      h("div.a-toolbar", load, state),
      field(t("source"), which),
      h("div.a-form-actions", prepare, startHost),
    );
  }

  async function refresh() {
    const mine = ++generation;
    if (!tasks.length) clear(listHost, skeleton(4));
    try {
      const [documents, articles, clients, vaults, nextPolicy] =
        await Promise.all([
          ctx.api.documents(),
          ctx.api.articles(),
          ctx.store.clients(),
          ctx.store.vaults(),
          ctx.api.sourcePolicy(),
        ]);
      if (mine !== generation) return;
      policy = nextPolicy;
      tasks = [
        ...documents.tasks.map((task) => ({ ...task, kind: "document" })),
        ...articles.tasks.map((task) => ({ ...task, kind: "article" })),
      ];
      drawList();
      if (!prepareHost.childElementCount)
        prepareHost.append(prepareCard(clients, vaults));
      if (
        !detail.childElementCount ||
        (!hasUnsavedChanges(detail) && !selected)
      )
        clear(detail, card({}, empty(t("pick"), { iconName: "document" })));
      if (
        selected &&
        !hasUnsavedChanges(detail) &&
        !detail.querySelector("form")
      ) {
        const task = tasks.find((item) => item.run_id === selected);
        if (task)
          void (task.kind === "article"
            ? openArticle(task)
            : openDocument(task));
      }
    } catch (error) {
      if (mine === generation) clear(listHost, failure(error, refresh));
    }
  }
  return {
    node: root,
    refresh,
    dirty: () => hasUnsavedChanges(detail),
    dispose() {
      generation++;
      epoch++;
    },
  };
}
