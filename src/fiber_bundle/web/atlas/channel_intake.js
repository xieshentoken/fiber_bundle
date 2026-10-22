// Channel auto-filing: allow-listed materials and sites, cumulative quotas, recent records,
// explicit batch processing and link review. History is never replayed automatically.
import { h, clear, icon } from "./dom.js";
import { strings } from "./i18n.js";
import {
  button,
  card,
  checkbox,
  codeBlock,
  disclosure,
  field,
  input,
  notice,
  select,
  skeleton,
  textarea,
  failure,
} from "./ui.js";
import { run, toast } from "./feedback.js";
import { confirmPublication } from "./publish.js";
import { sheet } from "./dialog.js";
import {
  captureDraftSave,
  hasUnsavedChanges,
  rememberDraft,
} from "../unsaved_changes.js";
import { intakeLabel } from "./channel_labels.js";

const t = strings({
  zh: {
    summary: "{on} · 目标库：{target} · 累计归档 {s}，下载 {d}，飞书回执 {o}",
    on: "自动入库已开启",
    off: "自动入库已关闭",
    noTarget: "尚未选择 / 授权不可用",
    unsupported: "此入口的自动归档尚不可用；当前支持独立飞书知识入口。",
    changedElsewhere: "策略已在其他窗口改变，当前草稿保留；保存前请核对版本。",
    policy: "自动入库策略",
    enabled: "自动归档新消息",
    target: "目标知识库（须已有可写授权）",
    pickTarget: "请选择目标库",
    materials: "自动归档的资料类型",
    text: "文字",
    link: "链接",
    image: "图片",
    file: "文件",
    sites: "允许自动处理的网站",
    allSites: "全选网站",
    noSites: "清空网站",
    domains: "自定义域名",
    domainsHelp:
      "每行一个；删除对应行即可移除。空白名单不会允许所有网站；没有提取器时正文待补全。",
    grouping: "同批消息保存方式",
    batch: "同批合并（保留消息顺序）",
    message: "每条消息分别保存",
    store_limit: "累计归档批次上限",
    download_limit: "累计附件 / 网页 / 图片 / 字幕获取上限",
    outbound_limit: "累计飞书回执上限",
    fetch_articles: "归档链接后，提取可取得的 X 长文章 / 公众号正文及原图",
    fetch_subtitles:
      "归档视频链接后，获取可取得的元数据 / 已有字幕（不超过 60 秒）",
    receipt: "归档完成后同时发送飞书回执（消耗独立回执额度）",
    policyNote:
      "只对之后的新消息生效，不处理历史收件。额度累计保留，暂停或重新保存不会清零。原件归档后，正文及字幕仍须审核发布；不调用模型、OCR、转录或下载整段视频。",
    save: "保存自动入库策略",
    saved: "策略已保存；历史消息未回放。入口运行时将处理之后的新消息。",
    records: "最近入库记录",
    noRecords: "暂无入库记录。",
    choose: "选择此批次（待确认 / 明确恢复）",
    stop: "停止此批次的自动处理",
    cards: "视频号卡片已归档；正文 / 字幕待补全，不会自动转录。",
    storedNote: "原件已保留；审核发布后才有派生笔记可检索。",
    originals: "查看本批原件",
    retrieve: "取回原件：{name}",
    copySaved: "{name} · 原件副本已保存 · {path}",
    copyPending: "原件已归档，目录副本待核对",
    otherNote:
      "文字、图片和文件可在“收件箱 → 文档与文章”中选择已保存来源继续处理；图片不会自动 OCR。",
    reviewLink: "核对正文 / 图片 / 字幕并审核",
    retry: "明确重试此链接的提取",
    receiptCheck: "请先暂停入口并核对手机；以下按钮只记录结果，不会重发。",
    gotReceipt: "手机已收到回执",
    noReceipt: "手机确认未收到回执",
    diagnostics: "入库诊断详情",
    history: "历史待确认批次（最近 50 条）",
    historyNote: "仅明确选择的批次会进入处理；不会自动补跑历史。",
    historyPick: "选择历史批次 {id} · {time}",
    selectAll: "全选待确认批次",
    clearAll: "清空批次选择",
    process: "处理所选批次 / 明确恢复",
    pickBatch: "请先选择批次。",
    tooMany: "每次最多处理 20 个所选批次。",
    processed: "所选批次已提交；后台按当前授权与累计额度执行。",
    reviewTitle: "入库提取审核",
    sourceHtml: "取回抓取原件 HTML",
    imageAlt: "原文图片 {name}",
    gaps: "字幕语言：{lang}；未覆盖区间 {n} 段。现有字幕没有转录或补写。",
    c1: "已核对原件与来源",
    c2: "已核对正文 / 字幕及其覆盖范围",
    c3: "已核对图片及顺序（没有图片也须确认）",
    reason: "人工核对理由",
    record: "记录人工审核",
    recorded: "人工审核已保存，可以确认发布。",
    publish: "确认发布到所选知识库",
    published: "已发布：{path}",
  },
  en: {
    summary:
      "{on} · target: {target} · {s} archived, {d} fetched, {o} Feishu receipts so far",
    on: "Auto-filing on",
    off: "Auto-filing off",
    noTarget: "not chosen / access unavailable",
    unsupported:
      "Auto-filing isn't available for this channel yet; dedicated Feishu knowledge channels are supported.",
    changedElsewhere:
      "The policy changed in another window; your draft is kept. Check the revision before saving.",
    policy: "Auto-filing policy",
    enabled: "Archive new messages automatically",
    target: "Target vault (needs write access)",
    pickTarget: "Choose a target vault",
    materials: "Material types to archive",
    text: "Text",
    link: "Links",
    image: "Images",
    file: "Files",
    sites: "Sites allowed for automatic processing",
    allSites: "Select all sites",
    noSites: "Clear sites",
    domains: "Custom domains",
    domainsHelp:
      "One per line; delete a line to remove it. An empty allow-list never allows every site; without an extractor the text stays to complete.",
    grouping: "How a batch is saved",
    batch: "Merge the batch (keep message order)",
    message: "Save each message separately",
    store_limit: "Max archived batches (cumulative)",
    download_limit:
      "Max attachment / page / image / subtitle fetches (cumulative)",
    outbound_limit: "Max Feishu receipts (cumulative)",
    fetch_articles:
      "After archiving a link, extract available X long posts / WeChat articles with images",
    fetch_subtitles:
      "After archiving a video link, fetch available metadata / existing subtitles (≤ 60 s)",
    receipt:
      "Send a Feishu receipt after archiving (uses its own receipt quota)",
    policyNote:
      "Applies to new messages only; history is never processed. Quotas are cumulative and never reset by pausing or re-saving. Archived text and subtitles still need review and publishing; no model, OCR, transcription or full-video download.",
    save: "Save auto-filing policy",
    saved:
      "Policy saved; history wasn't replayed. New messages are processed while the channel runs.",
    records: "Recent filing records",
    noRecords: "No filing records yet.",
    choose: "Select this batch (confirm / recover explicitly)",
    stop: "Stop automatic processing for this batch",
    cards:
      "Channels card archived; text / subtitles to complete — no automatic transcription.",
    storedNote:
      "Originals kept; derived notes become searchable after review and publishing.",
    originals: "Show this batch's originals",
    retrieve: "Retrieve original: {name}",
    copySaved: "{name} · original copy saved · {path}",
    copyPending: "Original archived; folder copy to reconcile",
    otherNote:
      "Text, images and files can be processed from Inbox → Documents & articles as saved sources; images are never OCR'd automatically.",
    reviewLink: "Check text / images / subtitles and review",
    retry: "Retry extraction for this link",
    receiptCheck:
      "Pause the channel and check the phone first; these buttons only record the result and never resend.",
    gotReceipt: "Phone received the receipt",
    noReceipt: "Phone did not receive the receipt",
    diagnostics: "Filing diagnostics",
    history: "Historical batches awaiting confirmation (latest 50)",
    historyNote:
      "Only explicitly selected batches are processed; history is never back-filled automatically.",
    historyPick: "Select historical batch {id} · {time}",
    selectAll: "Select all pending batches",
    clearAll: "Clear selection",
    process: "Process selected batches / recover explicitly",
    pickBatch: "Select batches first.",
    tooMany: "Up to 20 selected batches at a time.",
    processed:
      "Selected batches submitted; the backend runs them within current grants and quotas.",
    reviewTitle: "Filing extraction review",
    sourceHtml: "Retrieve the fetched source HTML",
    imageAlt: "Article image {name}",
    gaps: "Subtitle language: {lang}; {n} uncovered ranges. Existing subtitles were not transcribed or filled in.",
    c1: "I checked the original and source",
    c2: "I checked the text / subtitles and their coverage",
    c3: "I checked the images and order (confirm even with no images)",
    reason: "Review reason",
    record: "Record human review",
    recorded: "Review saved; you can publish now.",
    publish: "Publish to the chosen vault",
    published: "Published: {path}",
  },
});

function settingsOf(form) {
  const field_ = (key) => form.elements.namedItem(key);
  return {
    enabled: field_("enabled").checked,
    target_vault_id: field_("target_vault_id").value || null,
    grouping: field_("grouping").value,
    materials: [...form.querySelectorAll('[name="material"]:checked')].map(
      (n) => n.value,
    ),
    sites: [...form.querySelectorAll('[name="site"]:checked')].map(
      (n) => n.value,
    ),
    custom_domains: field_("custom_domains")
      .value.split(/\n/)
      .map((s) => s.trim())
      .filter(Boolean),
    store_limit: Number(field_("store_limit").value),
    download_limit: Number(field_("download_limit").value),
    outbound_limit: Number(field_("outbound_limit").value),
    receipt: field_("receipt").checked,
    fetch_articles: field_("fetch_articles").checked,
    fetch_subtitles: field_("fetch_subtitles").checked,
  };
}

export function createIntake(ctx, entryOf, refreshOverview) {
  const api = ctx.api;
  const status = h("div.a-stack");
  const policyHost = h("div");
  const recordsHost = h("div.a-stack");
  let snapshot = null,
    form = null;
  async function load() {
    const value = await api.channelIntake(entryOf().id);
    clear(
      status,
      notice(
        t("summary", {
          on: value.settings.enabled ? t("on") : t("off"),
          target:
            value.targets.find((v) => v.id === value.target_vault_id)?.name ||
            t("noTarget"),
          s: value.used.store,
          d: value.used.download,
          o: value.used.outbound,
        }),
        value.settings.enabled ? "success" : "info",
        ...(value.blockers || []).map((reason) =>
          h("p.a-subtle", intakeLabel(reason)),
        ),
      ),
    );
    if (!value.supported) {
      clear(policyHost, notice(t("unsupported"), "info"));
      recordsHost.replaceChildren();
      return;
    }
    if (!form || !hasUnsavedChanges(form)) {
      snapshot = value;
      build(value);
    } else if (value.revision !== snapshot.revision)
      status.append(notice(t("changedElsewhere"), "warning"));
    records(value);
  }
  function build(value) {
    const s = value.settings;
    const check = (key) => {
      const box = checkbox(t(key), s[key], { name: key });
      return box.label;
    };
    const target = select(
      [["", t("pickTarget")], ...value.targets.map((v) => [v.id, v.name])],
      s.target_vault_id || "",
      { name: "target_vault_id" },
    );
    const materials = h(
      "div.a-toolbar",
      ["text", "link", "image", "file"].map(
        (key) =>
          checkbox(t(key), s.materials.includes(key), {
            name: "material",
            value: key,
          }).label,
      ),
    );
    const siteBoxes = value.sites.map((site) =>
      checkbox(site.name, s.sites.includes(site.id), {
        name: "site",
        value: site.id,
      }),
    );
    const setSites = (checked) => () => {
      for (const box of siteBoxes) {
        box.input.checked = checked;
        box.input.dispatchEvent(new window.Event("change", { bubbles: true }));
      }
    };
    const domains = textarea({
      name: "custom_domains",
      value: s.custom_domains.join("\n"),
      placeholder: "papers.org",
      maxLength: 5200,
      rows: 3,
    });
    const sites = h(
      "fieldset.a-fieldset",
      h("legend", t("sites")),
      h(
        "div.a-toolbar",
        siteBoxes.map((b) => b.label),
      ),
      h(
        "div.a-toolbar",
        button(t("allSites"), { variant: "small", onClick: setSites(true) }),
        button(t("noSites"), { variant: "small", onClick: setSites(false) }),
      ),
      field(t("domains"), domains, { help: t("domainsHelp") }),
    );
    const showSites = () =>
      (sites.hidden = !materials.querySelector('[value="link"]').checked);
    materials.addEventListener("change", showSites);
    const grouping = select(
      [
        ["batch", t("batch")],
        ["message", t("message")],
      ],
      s.grouping,
      { name: "grouping" },
    );
    const limits = ["store_limit", "download_limit", "outbound_limit"].map(
      (key) =>
        field(
          t(key),
          input({
            type: "number",
            name: key,
            min: 0,
            max: 100,
            required: true,
            value: s[key],
          }),
        ),
    );
    const submit = button(t("save"), { variant: "primary" });
    form = h(
      "form.a-form",
      {
        dataset: { draft: "tracked" },
        onsubmit: (event) => event.preventDefault(),
      },
      check("enabled"),
      field(t("target"), target),
      h("fieldset.a-fieldset", h("legend", t("materials")), materials),
      sites,
      field(t("grouping"), grouping),
      h("div.a-form-grid", limits),
      h(
        "div.a-checks",
        check("fetch_articles"),
        check("fetch_subtitles"),
        check("receipt"),
      ),
      notice(t("policyNote")),
      h("div.a-form-actions", submit),
    );
    showSites();
    submit.addEventListener("click", () =>
      run(submit, async () => {
        if (!form.reportValidity()) return;
        const acknowledge = captureDraftSave(form);
        const result = await api.channelIntakeSave(entryOf().id, {
          expected_revision: snapshot.revision,
          settings: settingsOf(form),
        });
        acknowledge();
        snapshot = { ...snapshot, ...result };
        await load();
        await refreshOverview();
        toast(t("saved"), { tone: "success" });
      }),
    );
    clear(policyHost, form);
    rememberDraft(form);
  }
  function records(value) {
    if (hasUnsavedChanges(recordsHost)) return;
    const id = entryOf().id;
    const selected = new Set(),
      selectables = [];
    const history = new Map(value.history.map((b) => [b.batch_id, b]));
    const pick = (label, batchId) => {
      const box = checkbox(label);
      box.input.addEventListener("change", () =>
        box.input.checked ? selected.add(batchId) : selected.delete(batchId),
      );
      selectables.push(box.input);
      return box.label;
    };
    const act = (label, work, variant = "small") => {
      const control = button(label, { variant });
      control.addEventListener("click", () => run(control, work));
      return control;
    };
    const cards = value.records.map((record) => {
      history.delete(record.batch_id);
      const node = h(
        "article.a-op",
        h(
          "div.a-toolbar",
          h("strong", record.batch_id),
          h("span.a-subtle", intakeLabel(record.state)),
        ),
        record.reason ? h("p.a-subtle", intakeLabel(record.reason)) : null,
        (record.value.original_projections || []).map((o) =>
          h(
            "p.a-subtle",
            o.state === "projected"
              ? t("copySaved", { name: o.original_name, path: o.path })
              : t("copyPending"),
          ),
        ),
      );
      const actions = h("div.a-toolbar");
      if (
        ["manual", "needs_review", "archiving", "waiting"].includes(
          record.state,
        )
      )
        node.append(pick(t("choose"), record.batch_id));
      if (["queued", "waiting"].includes(record.state))
        actions.append(
          act(t("stop"), async () => {
            await api.channelIntakeCancel(id, {
              expected_revision: value.revision,
              batch_ids: [record.batch_id],
            });
            await load();
          }),
        );
      if (record.state === "stored") {
        if (record.value.cards?.length)
          node.append(h("p.a-subtle", t("cards")));
        node.append(h("p.a-subtle", t("storedNote")));
        const list = h("div.a-stack");
        actions.append(
          act(t("originals"), async () => {
            const result = await api.channelIntakeOriginals(
              id,
              record.batch_id,
            );
            clear(
              list,
              result.assets.map((asset) =>
                h(
                  "a.a-link",
                  {
                    href: api.channelIntakeOriginalURL(
                      id,
                      record.batch_id,
                      asset.asset_id,
                    ),
                    download: asset.original_name,
                  },
                  icon("download"),
                  t("retrieve", { name: asset.original_name }),
                ),
              ),
            );
          }),
        );
        node.append(list);
        if (!record.value.links?.length)
          node.append(h("p.a-subtle", t("otherNote")));
        (record.value.links || []).forEach((link, index) => {
          const line = h(
            "div.a-stack.a-intake-link",
            h("p", h("code", link.original_url)),
            h(
              "p.a-subtle",
              `${intakeLabel(link.state || "link_only")}${link.reason ? " · " + intakeLabel(link.reason) : ""}`,
            ),
          );
          const tools = h("div.a-toolbar");
          if (link.receipt)
            tools.append(
              act(
                t("reviewLink"),
                () => openReview(id, record.batch_id, index),
                "small",
              ),
            );
          if (["needs_review", "extracting", "link_only"].includes(link.state))
            tools.append(
              act(t("retry"), async () => {
                await api.channelIntakeRetry(
                  id,
                  record.batch_id,
                  index,
                  value.revision,
                );
                await load();
              }),
            );
          line.append(tools);
          node.append(line);
        });
        if (record.value.receipt_state)
          node.append(h("p.a-subtle", intakeLabel(record.value.receipt_state)));
        if (["unknown", "sending"].includes(record.value.receipt_state)) {
          node.append(notice(t("receiptCheck"), "warning"));
          for (const [label, state] of [
            [t("gotReceipt"), "sent"],
            [t("noReceipt"), "not_sent"],
          ])
            actions.append(
              act(label, async () => {
                await api.channelIntakeReceiptAck(id, record.batch_id, state);
                await load();
              }),
            );
        }
      }
      node.append(
        actions,
        disclosure(
          t("diagnostics"),
          codeBlock(JSON.stringify(record, null, 2)),
        ),
      );
      return node;
    });
    const historyPicks = [...history.values()].map((row) =>
      pick(
        t("historyPick", { id: row.batch_id, time: row.last_received }),
        row.batch_id,
      ),
    );
    const setAll = (checked) => () => {
      for (const node of selectables) {
        node.checked = checked;
        node.dispatchEvent(new window.Event("change", { bubbles: true }));
      }
    };
    clear(
      recordsHost,
      cards.length ? cards : h("p.a-subtle", t("noRecords")),
      h("h4", t("history")),
      h("p.a-subtle", t("historyNote")),
      h("div.a-stack", historyPicks),
      h(
        "div.a-form-actions",
        button(t("selectAll"), { variant: "small", onClick: setAll(true) }),
        button(t("clearAll"), { variant: "small", onClick: setAll(false) }),
        act(
          t("process"),
          async () => {
            if (!selected.size) throw new Error(t("pickBatch"));
            if (selected.size > 20) throw new Error(t("tooMany"));
            const acks = selectables.map((node) => captureDraftSave(node));
            await api.channelIntakeBatches(id, {
              expected_revision: value.revision,
              batch_ids: [...selected],
            });
            for (const ack of acks) ack();
            toast(t("processed"), { tone: "success" });
            await load();
          },
          "primary",
        ),
      ),
    );
    recordsHost.dataset.draft = "tracked";
    rememberDraft(recordsHost);
  }
  async function openReview(id, batch, index) {
    const packet = await api.channelIntakePacket(id, batch, index);
    const view = sheet({
      title: t("reviewTitle"),
      subtitle: `${batch} · #${index}`,
      wide: true,
    });
    const state = h("p.a-subtle", { role: "status" });
    const checks = [checkbox(t("c1")), checkbox(t("c2")), checkbox(t("c3"))];
    for (const c of checks) c.input.required = true;
    const reason = textarea({
      required: true,
      maxLength: 2000,
      rows: 3,
      value: packet.review?.reason || "",
    });
    let reviewId = packet.review?.id;
    const publish = button(t("publish"), {
      variant: "primary",
      iconName: "upload",
    });
    publish.disabled = !reviewId;
    publish.dataset.locked = String(!reviewId);
    const form = h(
      "form.a-form",
      {
        dataset: { draft: "tracked" },
        onsubmit: (event) => event.preventDefault(),
      },
      h(
        "div.a-checks",
        checks.map((c) => c.label),
      ),
      field(t("reason"), reason),
    );
    const record = button(t("record"));
    record.addEventListener("click", () =>
      run(record, async () => {
        if (!form.reportValidity()) return;
        const acknowledge = captureDraftSave(form);
        const result = await api.channelIntakeReview(id, batch, index, {
          digest: packet.digest,
          reason: reason.value,
          source_reviewed: true,
          text_reviewed: true,
          images_reviewed: true,
        });
        acknowledge();
        reviewId = result.id;
        publish.dataset.locked = "false";
        publish.disabled = false;
        state.textContent = t("recorded");
      }),
    );
    publish.addEventListener("click", () =>
      run(publish, async () => {
        if (!reviewId) return;
        if (
          !(await confirmPublication(api, "intake", id, {
            batch_id: batch,
            index,
            review_id: reviewId,
          }))
        )
          return false;
        const result = await api.channelIntakePublish(
          id,
          batch,
          index,
          reviewId,
        );
        state.textContent = t("published", {
          path: result.entry_note_path || result.relative_path + "/note.md",
        });
        form.hidden = true;
        record.hidden = publish.hidden = true;
      }),
    );
    const images = packet.files
      .filter((n) => /\.(png|jpg|jpeg|gif|webp)$/i.test(n))
      .map((name) =>
        h(
          "figure.a-figure",
          h("img", {
            src: api.channelIntakeAssetURL(id, batch, index, name),
            alt: t("imageAlt", { name }),
            loading: "lazy",
          }),
        ),
      );
    view.body.append(
      h("pre.a-pre.is-reading", packet.markdown),
      h(
        "a.a-link",
        {
          href: api.channelIntakeAssetURL(id, batch, index, "source.html"),
          download: "source.html",
        },
        icon("download"),
        t("sourceHtml"),
      ),
      ...images,
      packet.metadata.gaps
        ? h(
            "p.a-subtle",
            t("gaps", {
              lang: packet.metadata.language,
              n: packet.metadata.gaps.length,
            }),
          )
        : null,
      form,
      h("div.a-form-actions", record, publish),
      state,
    );
    if (packet.publication?.state === "published") {
      state.textContent = t("published", {
        path:
          packet.publication.entry_note_path ||
          packet.publication.relative_path + "/note.md",
      });
      form.hidden = record.hidden = publish.hidden = true;
    }
    rememberDraft(form);
  }
  const node = h(
    "div.a-stack",
    status,
    card({ title: t("policy") }, policyHost),
    card({ title: t("records") }, recordsHost),
  );
  return {
    node,
    async refresh() {
      clear(status, skeleton(2));
      try {
        await load();
      } catch (error) {
        clear(
          status,
          failure(error, () => this.refresh()),
        );
      }
    },
    dirty: () =>
      Boolean(form && hasUnsavedChanges(form)) ||
      hasUnsavedChanges(recordsHost),
  };
}
