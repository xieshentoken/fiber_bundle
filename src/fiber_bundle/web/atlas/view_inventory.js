// Sources & versions: source versions with original retrieval per identity + vault, recent
// receipts, and the one administrator processing configuration (affects new tasks only).
import { mountInventoryExtension } from "./inventory_extensions.js";
import { h, clear } from "./dom.js";
import { strings } from "./i18n.js";
import {
  badge,
  button,
  card,
  empty,
  failure,
  field,
  input,
  listRow,
  notice,
  select,
  skeleton,
  tabs,
  textarea,
} from "./ui.js";
import { run, toast } from "./feedback.js";
import {
  captureDraftSave,
  hasUnsavedChanges,
  rememberDraft,
} from "../unsaved_changes.js";

const t = strings({
  zh: {
    skills: "专业资料 Skill",
    video: "视频提取",
    versions: "来源版本与原件",
    receipts: "最近收件",
    policy: "资料处理设置",
    identity: "取回身份",
    vault: "知识库",
    show: "查看来源版本",
    pickFirst: "选择身份和知识库后查看来源版本。",
    page: "第 {n} 页",
    prev: "上一页",
    next: "下一页",
    none: "此页无可访问版本。",
    denied:
      "当前身份无法访问该知识库或来源。请核对客户端授权；刷新不会授予权限。",
    version: "版本 {n} · {retention}",
    download: "取回原件",
    unresolved: "存在无法唯一关联的图片引用，需核对 ZIP 原件。",
    noReceipts: "暂无收件记录。",
    receiptState: "收件：{state}；发布须另行核对。",
    document_image: "固定文档 Worker 镜像摘要",
    article_image: "固定文章 Worker 镜像摘要",
    timeout_seconds: "任务超时秒数",
    source_bytes: "单文件字节上限",
    pdf_pages: "PDF 页数上限",
    markdown_bytes: "单 Markdown 字节上限",
    markdown_total_bytes: "Markdown 总字节上限",
    text_bytes: "提取文本字节上限",
    result_bytes: "候选结果字节上限",
    image_pixels: "图片总像素上限",
    image_dimension: "图片边长上限",
    batch_bytes: "单批字节上限",
    ocr_runtime: "OCR 资格凭据 JSON",
    conversion_runtime: "DOC 转换资格凭据 JSON",
    expert_runtime: "完整 fake-expert 资格凭据 JSON",
    oversize: "单 Markdown 超限处理",
    pause: "暂停（保留旧任务语义）",
    split: "按完整结构分片，无法安全拆分则暂停",
    workers: "Worker",
    limits: "限额",
    runtimes: "运行资格",
    policyNote:
      "总量限额仍生效。分片保留正文并生成目录；不可拆分结构和跨段引用会暂停。设置只影响新任务。",
    save: "保存资料处理设置",
    saved: "资料处理设置已保存，只影响新任务。",
    badJson: "运行资格凭据需要有效 JSON。",
    configured: "已配置",
    notConfigured: "未配置（仅可归档）",
    capacity: "上限 {n}",
  },
  en: {
    skills: "Project skills",
    video: "Video extraction",
    versions: "Source versions & originals",
    receipts: "Recent receipts",
    policy: "Processing settings",
    identity: "Retrieve as",
    vault: "Vault",
    show: "Show source versions",
    pickFirst: "Pick an identity and vault to see source versions.",
    page: "Page {n}",
    prev: "Previous",
    next: "Next",
    none: "No accessible versions on this page.",
    denied:
      "This identity can't access the vault or source. Check the client grants; refreshing never grants access.",
    version: "Version {n} · {retention}",
    download: "Retrieve original",
    unresolved:
      "Some image references can't be matched uniquely; check the ZIP original.",
    noReceipts: "No receipts yet.",
    receiptState: "Receipt: {state}; publishing is checked separately.",
    document_image: "Pinned document worker image digest",
    article_image: "Pinned article worker image digest",
    timeout_seconds: "Task timeout (seconds)",
    source_bytes: "Max bytes per file",
    pdf_pages: "Max PDF pages",
    markdown_bytes: "Max bytes per Markdown",
    markdown_total_bytes: "Max total Markdown bytes",
    text_bytes: "Max extracted text bytes",
    result_bytes: "Max candidate result bytes",
    image_pixels: "Max total image pixels",
    image_dimension: "Max image side",
    batch_bytes: "Max bytes per batch",
    ocr_runtime: "OCR runtime receipt JSON",
    conversion_runtime: "DOC conversion runtime receipt JSON",
    expert_runtime: "Full fake-expert runtime receipt JSON",
    oversize: "Oversized Markdown",
    pause: "Pause (keep previous task semantics)",
    split: "Split by complete structure; pause if unsafe",
    workers: "Workers",
    limits: "Limits",
    runtimes: "Runtime receipts",
    policyNote:
      "Total limits still apply. Splitting keeps the text and adds a table of contents; unsplittable structures and cross-part references pause. Affects new tasks only.",
    save: "Save processing settings",
    saved: "Processing settings saved; only new tasks are affected.",
    badJson: "Runtime receipts must be valid JSON.",
    configured: "Configured",
    notConfigured: "Not configured (archive only)",
    capacity: "max {n}",
  },
});
const LIMIT_KEYS = [
  "source_bytes",
  "pdf_pages",
  "markdown_bytes",
  "markdown_total_bytes",
  "text_bytes",
  "result_bytes",
  "image_pixels",
  "image_dimension",
  "batch_bytes",
];
const RUNTIMES = ["ocr_runtime", "conversion_runtime", "expert_runtime"];

export function createView(ctx, route) {
  let params = route.params || {};
  let tab = params.tab || "versions",
    epoch = 0,
    offset = 0,
    extension = null;
  const tabHost = h("div");
  const body = h("div.a-stack");
  const root = h("div.a-stack", tabHost, body);
  function drawTabs() {
    clear(
      tabHost,
      tabs(
        ["versions", "receipts", "policy", "skills", "video"].map((value) => ({
          value,
          label: t(value),
        })),
        tab,
        (value) => ctx.navigate("inventory", "", { tab: value }),
        t("versions"),
      ),
    );
  }
  async function versionsTab() {
    const mine = epoch;
    const [clients, vaults] = await Promise.all([
      ctx.store.clients(),
      ctx.store.vaults(),
    ]);
    if (mine !== epoch) return;
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
    const list = h(
      "div.a-stack",
      empty(t("pickFirst"), { iconName: "inventory" }),
    );
    const reset = () => {
      ((offset = 0), (extension = null));
      clear(list, empty(t("pickFirst"), { iconName: "inventory" }));
    };
    who.addEventListener("change", reset);
    where.addEventListener("change", reset);
    async function load() {
      const fixed = { client: who.value, vault: where.value, offset },
        stamp = ++epoch;
      clear(list, skeleton(4));
      let rows;
      try {
        rows = await ctx.api.inventoryVersions(
          fixed.client,
          fixed.vault,
          fixed.offset,
        );
      } catch (error) {
        if (stamp === epoch)
          clear(
            list,
            error.status === 404
              ? notice(t("denied"), "error")
              : failure(error, load),
          );
        return;
      }
      if (stamp !== epoch) return;
      clear(
        list,
        h("p.a-subtle", t("page", { n: fixed.offset / 20 + 1 })),
        rows.length
          ? card(
              { className: "is-flush" },
              h(
                "div.a-rows",
                rows.map((version) => {
                  const downloads = version.assets.map((asset) => {
                    const control = button(
                      asset.download_name || asset.original_name,
                      { variant: "small", iconName: "download" },
                    );
                    control.addEventListener("click", () =>
                      run(control, () =>
                        ctx.api.downloadOriginal(
                          fixed.client,
                          fixed.vault,
                          version.id,
                          asset.id,
                        ),
                      ),
                    );
                    return control;
                  });
                  return h(
                    "div.a-version",
                    listRow({
                      iconName: "layers",
                      title: version.id,
                      detail: t("version", {
                        n: version.version_no,
                        retention: version.retention,
                      }),
                    }),
                    h(
                      "div.a-version-assets",
                      downloads,
                      version.archive_projection?.references?.some(
                        (r) => r.state !== "resolved",
                      )
                        ? h("p.a-inline-error", t("unresolved"))
                        : null,
                    ),
                  );
                }),
              ),
            )
          : empty(t("none"), { iconName: "inventory" }),
        h(
          "div.a-toolbar",
          button(t("prev"), {
            variant: "small",
            iconName: "back",
            disabled: fixed.offset === 0,
            onClick: () => ((offset = Math.max(0, offset - 20)), load()),
          }),
          button(t("next"), {
            variant: "small",
            disabled: rows.length < 20,
            onClick: () => ((offset += 20), load()),
          }),
        ),
      );
    }
    const show = button(t("show"), { variant: "primary", iconName: "search" });
    show.addEventListener("click", () =>
      run(show, () => {
        ((offset = 0), (extension = null));
        return load();
      }),
    );
    clear(
      body,
      card(
        {},
        h(
          "div.a-scope-row",
          field(t("identity"), who),
          field(t("vault"), where),
          show,
        ),
      ),
      list,
    );
  }
  async function receiptsTab() {
    const mine = epoch;
    const receipts = await ctx.api.inventoryReceipts();
    if (mine !== epoch) return;
    clear(
      body,
      card(
        { className: "is-flush" },
        receipts.length
          ? h(
              "div.a-rows",
              receipts.map((receipt) =>
                listRow({
                  iconName: "inbox",
                  title: receipt.job_id,
                  detail:
                    receipt.error_code ||
                    t("receiptState", { state: receipt.state }),
                  status: [
                    receipt.state,
                    receipt.error_code ? "error" : "info",
                  ],
                }),
              ),
            )
          : empty(t("noReceipts"), { iconName: "inbox" }),
      ),
    );
  }
  async function policyTab() {
    const mine = epoch;
    const policy = await ctx.api.sourcePolicy();
    if (mine !== epoch) return;
    const p = policy.effective_policy;
    const fields = {
      document_image: input({ value: p.document_image ?? "" }),
      article_image: input({ value: p.article_image ?? "" }),
      timeout_seconds: input({
        type: "number",
        min: 1,
        max: 300,
        required: true,
        value: p.timeout_seconds,
      }),
    };
    const limits = Object.fromEntries(
      LIMIT_KEYS.map((key) => [
        key,
        input({
          type: "number",
          min: 1,
          required: true,
          value: p.limits[key],
          max: policy.capacity[key],
        }),
      ]),
    );
    const receipts = Object.fromEntries(
      RUNTIMES.map((key) => [
        key,
        textarea({
          maxLength: 16384,
          rows: 3,
          value: p[key] ? JSON.stringify(p[key]) : "",
          spellcheck: false,
        }),
      ]),
    );
    const oversize = select(
      [
        ["pause", t("pause")],
        ["split", t("split")],
      ],
      p.oversize_action,
    );
    const submit = h(
      "button.a-button.is-primary",
      { type: "submit" },
      t("save"),
    );
    const form = h(
      "form.a-form",
      { dataset: { draft: "tracked" } },
      h(
        "fieldset.a-fieldset",
        h("legend", t("workers")),
        h(
          "div.a-toolbar",
          badge(
            p.document_image ? t("configured") : t("notConfigured"),
            p.document_image ? "success" : "idle",
          ),
        ),
        h(
          "div.a-form-grid",
          Object.entries(fields).map(([key, node]) =>
            field(t(key), node, { optional: key === "article_image" }),
          ),
        ),
      ),
      h(
        "fieldset.a-fieldset",
        h("legend", t("limits")),
        h(
          "div.a-form-grid",
          LIMIT_KEYS.map((key) =>
            field(t(key), limits[key], {
              help: t("capacity", { n: policy.capacity[key] }),
            }),
          ),
        ),
        field(t("oversize"), oversize),
      ),
      h(
        "fieldset.a-fieldset",
        h("legend", t("runtimes")),
        RUNTIMES.map((key) => field(t(key), receipts[key], { optional: true })),
      ),
      notice(t("policyNote")),
      h("div.a-form-actions", submit),
    );
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      if (!form.reportValidity()) return;
      void run(submit, async () => {
        const acknowledge = captureDraftSave(form);
        const value = {
          ...p,
          revision: policy.value === null ? 1 : p.revision + 1,
          document_image: fields.document_image.value.trim() || null,
          article_image: fields.article_image.value.trim() || null,
          timeout_seconds: Number(fields.timeout_seconds.value),
          oversize_action: oversize.value,
          limits: Object.fromEntries(
            LIMIT_KEYS.map((key) => [key, Number(limits[key].value)]),
          ),
        };
        let parsed;
        try {
          parsed = Object.fromEntries(
            RUNTIMES.map((key) => [
              key,
              receipts[key].value.trim()
                ? JSON.parse(receipts[key].value)
                : null,
            ]),
          );
        } catch {
          throw new Error(t("badJson"));
        }
        if (p.schema >= 3 || Object.values(parsed).some(Boolean))
          Object.assign(value, parsed, {
            schema: parsed.expert_runtime || p.schema === 4 ? 4 : 3,
          });
        if (value.schema === 3) delete value.expert_runtime;
        await ctx.api.saveSourcePolicy({
          value,
          expected_sha256: policy.configuration_sha256,
        });
        if (mine !== epoch) return;
        acknowledge();
        toast(t("saved"), { tone: "success" });
      });
    });
    clear(body, card({ title: t("policy") }, form));
    rememberDraft(form);
  }
  async function refresh() {
    if (tab === "policy" && hasUnsavedChanges(body)) return;
    epoch++;
    extension?.dispose();
    extension = null;
    drawTabs();
    clear(body, skeleton(4));
    try {
      if (["skills", "video"].includes(tab)) {
        extension = await mountInventoryExtension(ctx, tab, body, params);
        return;
      }
      await { versions: versionsTab, receipts: receiptsTab, policy: policyTab }[
        tab
      ]();
    } catch (error) {
      clear(body, failure(error, refresh));
    }
  }
  return {
    node: root,
    refresh,
    update(next) {
      params = next.params || {};
      tab = params.tab || "versions";
      void refresh();
      return true;
    },
    dispose: () => {
      epoch++;
      extension?.dispose();
    },
  };
}
