// Import: resumable bounded uploads into an explicit identity + vault scope. The backend owns
// originals, permissions and processing; drafts and publishing are separate stages.
import { h, clear, icon } from "./dom.js";
import { strings } from "./i18n.js";
import {
  button,
  card,
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
} from "./ui.js";
import { run, toast } from "./feedback.js";
import { fileSha } from "../file_digest.js";
import {
  captureDraftSave,
  hasUnsavedChanges,
  rememberDraft,
} from "../unsaved_changes.js";
import { expertControls } from "./import_expert.js";
import { bytes, number } from "./format.js";

const t = strings({
  zh: {
    ordinary: "普通资料导入",
    ordinaryHint:
      "原生文本、Markdown、PDF、Office 与已支持的 OCR；已知微信聊天 ZIP 走普通解析，未知 ZIP 只归档。",
    expert: "fake-expert 专用导入",
    expertHint:
      "固定 PDF 与完整专家 runtime；workpack 单独绑定，只有完整 Draft 进入 Book。",
    capOrdinary:
      "固定使用 ordinary。普通笔记进入 Inbox；未知 ZIP 仅保存原件，不作为专家 workpack。",
    capExpertReady:
      "固定使用完整 fake-expert；扫描须选择合格 profile。仅完整 Draft 进入 Book，语义 Review 仍 deferred。",
    capExpertMissing:
      "完整 fake-expert 运行环境未配置；可保存原件，处理不会由普通引擎代跑。",
    scope: "保存到",
    identity: "来源身份",
    vault: "目标知识库",
    noScope:
      "没有具有“新增”与“原件”权限的身份。请先在 Agent 与客户端授予权限。",
    action: "导入方式",
    archive: "仅保存原件",
    process: "导入并处理",
    drop: "拖放文件到这里",
    dropOr: "或",
    browse: "选择文件",
    pdfOnly: "仅接受 PDF",
    attachments: "Markdown 相对附件",
    attachmentsHelp:
      "选择配套文件并填写正文中的原引用路径；缺件不会自动读取或下载。",
    refPath: "{name} 在正文中的引用路径",
    advanced: "提取模式与上限（进阶）",
    profile: "提取模式",
    native: "原生文本 / Office XML",
    ocr: "图片 / 扫描 PDF OCR（需要资格凭据）",
    conversion: "旧版 DOC 转换（需要资格凭据）",
    expertProfile: "完整 Skill 模式",
    backend: "明确后端（扫描/视觉须有对应运行条件）",
    nativeDefault: "原生默认",
    workpack: "workpack ZIP 导入编号",
    workpackHelp: "可先保存 ZIP 原件；不自动生成语义材料。",
    limit_source_bytes: "单文件字节上限",
    limit_pdf_pages: "PDF 页数上限",
    limit_markdown_bytes: "单 Markdown 字节上限",
    limit_markdown_total_bytes: "Markdown 总字节上限",
    submit: "开始导入",
    clearFiles: "清除",
    needScope: "请先选择具有新增和原件权限的来源身份与知识库。",
    tooLarge: "文件或本批总量超过当前有效限制。",
    uniqueRef: "每个附件需要唯一的正文引用路径。",
    attachTooLarge: "附件超过当前大小限制。",
    badLimit: "单次限制必须在管理员配置范围内。",
    mismatch: "续传文件与已冻结原件不一致。",
    hashing: "{name} · 校验文件摘要…",
    uploading: "{name} · 上传中 {done}/{total}",
    saved: "{name} · 原件已保存{extra}",
    submitted: "，提取已提交",
    cancelUpload: "取消当前上传",
    recorded:
      "导入已记录。按本库已启用的审核与发布策略处理；异常见待处理状态。",
    resumeHint: "已接收的分块保留，可刷新后选择相同文件续传。",
    busy: "正在上传，请等待完成或取消后再切换。",
    discard: "当前导入草稿尚未提交，切换入口会清除草稿。确定切换？",
    index: "知识库索引",
    indexReady: "持久索引已就绪",
    indexStale: "索引待更新（发布、编辑、移动或删除后显式更新）",
    indexPick: "先选择来源身份和知识库。",
    updateIndex: "更新索引",
    indexUpdating: "正在更新索引；不会调用模型或再次发布。",
    indexDone: "索引已就绪 · 更新 {u} 篇，复用 {r} 篇",
    recent: "当前范围的导入",
    none: "当前范围暂无导入记录。",
    taskId: "编号 {id}",
    start: "开始确定性提取",
    cancel: "取消",
    review: "前往审核",
    resume: "续传",
    automatic: "系统处理 · {phase} · {state}",
    projected: "原件副本已保存",
    projectPending: "原件已归档，目录副本待核对",
    notWritten: "尚未写入目录",
    zeroBudget: "模型额度 0",
    commands: "飞书专家指令",
    settings: "资料处理设置",
    st_uploading: "上传中",
    st_archived: "原件已保存",
    st_prepared: "待处理",
    st_processing: "处理中",
    st_running: "完整 Skill 处理中",
    st_paused: "完整 Skill 暂停，等待补充",
    st_completed: "完整 Draft 已生成，待确认",
    st_needs_review: "待审核",
    st_needs_split: "发布暂停（结构或额度）",
    st_needs_supplement: "待补充附件或链接映射",
    st_published: "已发布 · 索引待核对",
    st_failed: "处理失败 · 原件保留",
    st_cancelled: "已取消",
    st_interrupted: "已中断",
    pv_expertReady: "完整 Skill 已配置，语义材料和运行条件仍须核对",
    pv_expertMissing: "可保存原件，完整 Skill 能力未就绪",
    pv_ocrReady: "图片 OCR 已配置，须显式选择 OCR 模式",
    pv_ocrMissing: "可保存原件，图片 OCR 未就绪",
    pv_zip: "ZIP 须识别为微信文本导出后处理；未知格式只保存原件",
    pv_docReady: "DOC 转换已配置，须显式选择转换模式",
    pv_docMissing: "可保存原件，DOC 转换未就绪",
    pv_ready: "普通提取 Worker 已配置，运行待核对",
    pv_missing: "可保存原件，提取能力未就绪",
  },
  en: {
    ordinary: "Regular import",
    ordinaryHint:
      "Native text, Markdown, PDF, Office and supported OCR. Known WeChat chat ZIPs are parsed; unknown ZIPs are archived only.",
    expert: "fake-expert import",
    expertHint:
      "PDF with the full expert runtime; workpacks are bound separately and only complete drafts go to Book.",
    capOrdinary:
      "Uses the ordinary engine. Notes land in Inbox; unknown ZIPs are kept as originals, never as expert workpacks.",
    capExpertReady:
      "Uses the full fake-expert runtime; scans need an eligible profile. Only complete drafts go to Book; semantic review stays deferred.",
    capExpertMissing:
      "The full fake-expert runtime isn't configured. Originals can be saved; the ordinary engine never substitutes.",
    scope: "Save into",
    identity: "Source identity",
    vault: "Target vault",
    noScope:
      "No identity has both “add” and “originals” access. Grant it in Agents & clients first.",
    action: "Mode",
    archive: "Save originals only",
    process: "Import and process",
    drop: "Drop files here",
    dropOr: "or",
    browse: "Choose files",
    pdfOnly: "PDF only",
    attachments: "Markdown relative attachments",
    attachmentsHelp:
      "Choose companion files and give the path the note uses. Missing files are never fetched automatically.",
    refPath: "Path used for {name} in the note",
    advanced: "Extraction mode and limits (advanced)",
    profile: "Extraction mode",
    native: "Native text / Office XML",
    ocr: "Image / scanned PDF OCR (needs credentials)",
    conversion: "Legacy DOC conversion (needs credentials)",
    expertProfile: "Full Skill mode",
    backend: "Explicit backend (scan/visual need matching runtime)",
    nativeDefault: "Native default",
    workpack: "workpack ZIP import ID",
    workpackHelp:
      "You can archive the ZIP first; no semantic material is generated automatically.",
    limit_source_bytes: "Max bytes per file",
    limit_pdf_pages: "Max PDF pages",
    limit_markdown_bytes: "Max bytes per Markdown file",
    limit_markdown_total_bytes: "Max total Markdown bytes",
    submit: "Start import",
    clearFiles: "Clear",
    needScope:
      "Choose an identity and vault with add and original access first.",
    tooLarge: "A file or the batch total exceeds the current limits.",
    uniqueRef: "Each attachment needs a unique reference path.",
    attachTooLarge: "An attachment exceeds the size limit.",
    badLimit: "Per-import limits must stay within the admin configuration.",
    mismatch: "The resumed file doesn't match the frozen original.",
    hashing: "{name} · computing digest…",
    uploading: "{name} · uploading {done}/{total}",
    saved: "{name} · original saved{extra}",
    submitted: ", extraction submitted",
    cancelUpload: "Cancel upload",
    recorded:
      "Import recorded. It follows this vault's active review and publishing policy; exceptions appear as pending.",
    resumeHint:
      "Received chunks are kept; refresh and pick the same file to resume.",
    busy: "Uploading — wait for it to finish or cancel before switching.",
    discard:
      "The import draft isn't submitted; switching clears it. Switch anyway?",
    index: "Vault index",
    indexReady: "Persistent index ready",
    indexStale:
      "Index needs updating (after publishing, editing, moving or deleting)",
    indexPick: "Choose an identity and vault first.",
    updateIndex: "Update index",
    indexUpdating: "Updating the index; no model calls and no re-publishing.",
    indexDone: "Index ready · {u} updated, {r} reused",
    recent: "Imports in this scope",
    none: "No imports in this scope yet.",
    taskId: "ID {id}",
    start: "Start deterministic extraction",
    cancel: "Cancel",
    review: "Go to review",
    resume: "Resume",
    automatic: "System processing · {phase} · {state}",
    projected: "Original copy saved",
    projectPending: "Original archived; folder copy to reconcile",
    notWritten: "not written to a folder yet",
    zeroBudget: "Model budget 0",
    commands: "Feishu expert commands",
    settings: "Processing settings",
    st_uploading: "Uploading",
    st_archived: "Original saved",
    st_prepared: "Ready to process",
    st_processing: "Processing",
    st_running: "Full Skill running",
    st_paused: "Full Skill paused, needs input",
    st_completed: "Full draft ready to confirm",
    st_needs_review: "Needs review",
    st_needs_split: "Publishing paused (structure or quota)",
    st_needs_supplement: "Needs attachments or link mapping",
    st_published: "Published · check index",
    st_failed: "Failed · original kept",
    st_cancelled: "Cancelled",
    st_interrupted: "Interrupted",
    pv_expertReady:
      "Full Skill configured; semantic material and runtime still need checking",
    pv_expertMissing: "Original can be saved; full Skill not ready",
    pv_ocrReady: "Image OCR configured; choose OCR mode explicitly",
    pv_ocrMissing: "Original can be saved; image OCR not ready",
    pv_zip:
      "ZIPs must be recognized as WeChat text exports; unknown formats are archived only",
    pv_docReady: "DOC conversion configured; choose conversion explicitly",
    pv_docMissing: "Original can be saved; DOC conversion not ready",
    pv_ready: "Ordinary extraction worker configured; run still to verify",
    pv_missing: "Original can be saved; extraction not ready",
  },
});
const LIMITS = [
  "source_bytes",
  "pdf_pages",
  "markdown_bytes",
  "markdown_total_bytes",
];
const IMAGES = [
  "png",
  "jpg",
  "jpeg",
  "webp",
  "bmp",
  "gif",
  "tif",
  "tiff",
  "heic",
  "heif",
];
export function previewKey(ext, policy, engine = "ordinary") {
  if (engine === "fake-expert")
    return ext === "pdf" && policy.expert_runtime
      ? "pv_expertReady"
      : "pv_expertMissing";
  if (IMAGES.includes(ext))
    return policy.ocr_runtime ? "pv_ocrReady" : "pv_ocrMissing";
  if (ext === "zip") return "pv_zip";
  if (ext === "doc")
    return policy.conversion_runtime ? "pv_docReady" : "pv_docMissing";
  return ["md", "markdown", "txt", "pdf", "docx", "xlsx", "pptx"].includes(
    ext,
  ) && policy.document_image
    ? "pv_ready"
    : "pv_missing";
}
export function encodeChunk(data) {
  let encoded = "";
  for (const byte of data) encoded += String.fromCharCode(byte);
  return window.btoa(encoded);
}

export function createView(ctx, route) {
  let engine =
    route.params?.engine === "fake-expert" ? "fake-expert" : "ordinary";
  let settings = null,
    clients = [],
    vaults = [],
    epoch = 0,
    running = null,
    files = [];
  const wanted = { client: route.params?.client, vault: route.params?.vault };
  const identity = select([], undefined, { dataset: { draft: "ignore" } });
  const vault = select([], undefined, { dataset: { draft: "ignore" } });
  let mode = "archive";
  const modePicker = h("div");
  const fileInput = h("input.a-sr", {
    type: "file",
    multiple: true,
    id: "atlas-import-files",
  });
  const fileList = h("ul.a-file-picks");
  const attachInput = h("input", { type: "file", multiple: true });
  const attachPaths = h("div.a-stack");
  const profile = select(
    [
      ["native", t("native")],
      ["ocr", t("ocr")],
      ["conversion", t("conversion")],
    ],
    "native",
  );
  const expertProfile = select(
    ["native-text", "scan-text", "scan-structure", "visual"].map((v) => [v, v]),
    "native-text",
  );
  const backend = select(
    [
      "",
      "pypdf-native",
      "paddleocr-ppocrv6",
      "pymupdf-tesseract",
      "docling",
      "paddleocr-ppstructure-v3",
      "pdftoppm",
    ].map((v) => [v, v || t("nativeDefault")]),
    "",
  );
  const workpack = input({ id: "atlas-import-workpack" });
  const limits = Object.fromEntries(
    LIMITS.map((key) => [
      key,
      input({ type: "number", min: 1, id: "atlas-limit-" + key }),
    ]),
  );
  const capability = h("div");
  const progress = h("div.a-upload-progress", { "aria-live": "polite" });
  const submit = h(
    "button.a-button.is-primary",
    { type: "submit" },
    icon("upload"),
    t("submit"),
  );
  const indexState = h("p.a-subtle", { "aria-live": "polite" });
  const listHost = h("div.a-rows");
  const commandsHost = h("div");
  const engineHost = h("div.a-engine-cards", {
    role: "radiogroup",
    "aria-label": t("ordinary"),
  });
  const ordinaryOnly = [];
  const expertOnly = [];

  const scope = () => ({ client_id: identity.value, vault_id: vault.value });
  const current = (mine) => mine === epoch;

  function drawEngines() {
    clear(
      engineHost,
      ["ordinary", "fake-expert"].map((value) =>
        h(
          "button.a-choice.is-wide",
          {
            type: "button",
            role: "radio",
            "aria-checked": String(value === engine),
            onclick: () => switchEngine(value),
          },
          icon(value === "ordinary" ? "imports" : "expert"),
          h("strong", value === "ordinary" ? t("ordinary") : t("expert")),
          h("span", value === "ordinary" ? t("ordinaryHint") : t("expertHint")),
        ),
      ),
    );
  }
  function hasDraft() {
    return (
      hasUnsavedChanges(form) ||
      files.length > 0 ||
      attachInput.files.length > 0
    );
  }
  function switchEngine(value) {
    if (value === engine) return;
    if (running) {
      toast(t("busy"), { tone: "warning" });
      return;
    }
    if (hasDraft() && !window.confirm(t("discard"))) return;
    resetDraft();
    engine = value;
    applyEngine();
  }
  function resetDraft() {
    files = [];
    fileInput.value = "";
    attachInput.value = "";
    attachPaths.replaceChildren();
    workpack.value = "";
    profile.value = "native";
    expertProfile.value = "native-text";
    backend.value = "";
    for (const key of LIMITS)
      if (settings) limits[key].value = settings.effective_policy.limits[key];
    drawFiles();
  }
  function applyEngine() {
    drawEngines();
    const expert = engine === "fake-expert";
    fileInput.accept = expert ? ".pdf,application/pdf" : "";
    for (const node of ordinaryOnly) node.hidden = expert;
    for (const node of expertOnly) node.hidden = !expert;
    clear(
      capability,
      notice(
        expert
          ? settings?.effective_policy.expert_runtime
            ? t("capExpertReady")
            : t("capExpertMissing")
          : t("capOrdinary"),
        expert && !settings?.effective_policy.expert_runtime
          ? "warning"
          : "info",
      ),
    );
    rememberDraft(form);
    void applyDefaults();
  }
  function drawMode() {
    clear(
      modePicker,
      segmented(
        [
          { value: "archive", label: t("archive"), icon: "folder" },
          { value: "process", label: t("process"), icon: "spark" },
        ],
        mode,
        (value) => (mode = value),
        t("action"),
      ),
    );
  }
  function drawFiles() {
    const policy = settings?.effective_policy;
    clear(
      fileList,
      files.map((file) => {
        const ext = file.name.split(".").pop().toLowerCase();
        return h(
          "li",
          icon("file"),
          h("span.a-file-name", file.name),
          h("span.a-subtle", bytes(file.size)),
          policy
            ? h("span.a-subtle.a-file-cap", t(previewKey(ext, policy, engine)))
            : null,
        );
      }),
    );
    clearButton.hidden = !files.length;
  }
  const clearButton = button(t("clearFiles"), {
    variant: "ghost",
    onClick: () => resetDraft(),
  });
  fileInput.addEventListener("change", () => {
    files = [...fileInput.files];
    drawFiles();
  });
  const drop = h(
    "label.a-dropzone",
    { for: fileInput.id },
    icon("upload"),
    h("strong", t("drop")),
    h("span", t("dropOr"), " ", h("span.a-link", t("browse"))),
  );
  for (const name of ["dragenter", "dragover"])
    drop.addEventListener(name, (event) => {
      event.preventDefault();
      drop.classList.add("is-over");
    });
  for (const name of ["dragleave", "drop"])
    drop.addEventListener(name, () => drop.classList.remove("is-over"));
  drop.addEventListener("drop", (event) => {
    event.preventDefault();
    files = [...event.dataTransfer.files];
    drawFiles();
  });
  attachInput.addEventListener("change", () => {
    clear(
      attachPaths,
      [...attachInput.files].map((file) =>
        field(
          t("refPath", { name: file.name }),
          input({ value: file.name, dataset: { filename: file.name } }),
        ),
      ),
    );
  });
  const attachmentsBlock = disclosure(
    t("attachments"),
    h("p.a-subtle", t("attachmentsHelp")),
    attachInput,
    attachPaths,
  );
  ordinaryOnly.push(attachmentsBlock);
  const profileField = field(t("profile"), profile);
  ordinaryOnly.push(profileField);
  const expertFields = [
    field(t("expertProfile"), expertProfile),
    field(t("backend"), backend),
    field(t("workpack"), workpack, { help: t("workpackHelp"), optional: true }),
  ];
  expertOnly.push(...expertFields);
  const form = h(
    "form.a-form",
    { dataset: { draft: "tracked" } },
    capability,
    h(
      "div.a-form-grid",
      field(t("identity"), identity),
      field(t("vault"), vault),
    ),
    h("div.a-field", h("label", t("action")), modePicker),
    fileInput,
    drop,
    fileList,
    attachmentsBlock,
    disclosure(
      t("advanced"),
      h(
        "div.a-form-grid",
        profileField,
        ...expertFields,
        ...LIMITS.map((key) => field(t("limit_" + key), limits[key])),
      ),
    ),
    h("div.a-form-actions", submit, clearButton),
    progress,
  );
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    void send(files);
  });

  function scopedVaults(previous) {
    const client = clients.find((c) => c.id === identity.value);
    const permitted = vaults.filter(
      (v) =>
        v.status === "active" &&
        client?.grants.some(
          (g) => g.vault_id === v.id && g.can_add && g.can_original,
        ),
    );
    setOptions(
      vault,
      permitted.map((v) => [v.id, v.name]),
      permitted.some((v) => v.id === previous) ? previous : undefined,
    );
  }
  identity.addEventListener("change", () => {
    scopedVaults();
    scopeChanged();
  });
  vault.addEventListener("change", scopeChanged);
  function scopeChanged() {
    epoch++;
    void rows().then(applyDefaults);
  }
  // Pre-select processing only when the vault's automatic policy would handle it.
  async function applyDefaults() {
    const owner = scope();
    if (!owner.vault_id || hasUnsavedChanges(form) || running || !settings)
      return;
    const policy = await ctx.api.vaultSave.automaticPolicy(owner.vault_id);
    if (
      scope().vault_id !== owner.vault_id ||
      scope().client_id !== owner.client_id ||
      hasUnsavedChanges(form) ||
      running
    )
      return;
    const kind = engine === "fake-expert" ? "expert" : "document";
    const ready =
      kind === "expert"
        ? !!settings.effective_policy.expert_runtime
        : !!settings.effective_policy.document_image;
    mode =
      ready &&
      policy.settings.review_mode === "automatic" &&
      policy.settings.client_id === owner.client_id &&
      policy.settings.allowed_kinds.includes(kind)
        ? "process"
        : "archive";
    drawMode();
  }

  async function uploadChunks(item, file, owner, mine, onProgress) {
    for (
      let offset = item.received_bytes || 0;
      offset < file.size;
      offset += item.chunk_bytes
    ) {
      if (!current(mine)) return false;
      const blob = file.slice(offset, offset + item.chunk_bytes);
      const data = new Uint8Array(await blob.arrayBuffer());
      const sha256 = await fileSha(blob);
      if (!current(mine)) return false;
      await ctx.api.importChunk(item.id, {
        client_id: owner.client_id,
        ordinal: Math.floor(offset / item.chunk_bytes),
        sha256,
        data: encodeChunk(data),
      });
      onProgress?.(Math.min(file.size, offset + item.chunk_bytes));
    }
    return true;
  }
  function progressRow(text, value, max, cancel) {
    clear(
      progress,
      h("div.a-toolbar", h("span", text), h("span.a-spacer"), cancel),
      max
        ? h(
            "div.a-progress",
            h("i", { style: `width:${Math.round((value / max) * 100)}%` }),
          )
        : null,
    );
  }
  async function send(selected, resume = null) {
    if (running || !selected.length || !selected[0]) return;
    const owner = scope(),
      mine = epoch;
    if (!owner.client_id || !owner.vault_id) {
      toast(t("needScope"), { tone: "error" });
      return;
    }
    const cap = settings.effective_policy.limits;
    const companions = resume ? [] : [...attachInput.files];
    if (
      selected.some((f) => f.size < 1 || f.size > cap.source_bytes) ||
      [...selected, ...companions].reduce((n, f) => n + f.size, 0) >
        cap.batch_bytes
    ) {
      toast(t("tooLarge"), { tone: "error" });
      return;
    }
    const acknowledge = captureDraftSave(form);
    const operation = Symbol("import");
    running = operation;
    submit.disabled = true;
    try {
      const references = {};
      for (const companion of companions) {
        const literal = attachPaths
          .querySelector(`input[data-filename="${CSS.escape(companion.name)}"]`)
          ?.value.trim();
        if (!literal || references[literal]) throw new Error(t("uniqueRef"));
        if (companion.size < 1 || companion.size > cap.source_bytes)
          throw new Error(t("attachTooLarge"));
        const attachment = await ctx.api.createImport({
          ...owner,
          submission_id: crypto.randomUUID(),
          filename: companion.name,
          source_sha256: await fileSha(companion),
          source_bytes: companion.size,
          action: "archive",
          engine: "ordinary",
          configuration_sha256: settings.configuration_sha256,
          overrides: {},
          references: {},
        });
        if (!(await uploadChunks(attachment, companion, owner, mine))) return;
        await ctx.api.finishImport(attachment.id, owner.client_id);
        references[literal] = attachment.id;
      }
      for (const file of selected) {
        if (!current(mine)) return;
        progressRow(t("hashing", { name: file.name }));
        const sha = await fileSha(file);
        if (!current(mine)) return;
        let item = resume;
        if (
          item &&
          (item.source_sha256 !== sha ||
            item.source_bytes !== file.size ||
            item.filename !== file.name)
        )
          throw new Error(t("mismatch"));
        if (!item) {
          const overrides = {};
          for (const key of LIMITS) {
            const value = Number(limits[key].value);
            if (!Number.isSafeInteger(value) || value < 1 || value > cap[key])
              throw new Error(t("badLimit"));
            overrides[key] = value;
          }
          item = await ctx.api.createImport({
            ...owner,
            submission_id: crypto.randomUUID(),
            filename: file.name,
            source_sha256: sha,
            source_bytes: file.size,
            action: mode,
            engine,
            profile: profile.value,
            expert_profile: expertProfile.value,
            expert_backend: backend.value || null,
            workpack_import_id: workpack.value.trim() || null,
            configuration_sha256: settings.configuration_sha256,
            references,
            overrides,
          });
        }
        if (!current(mine)) return;
        // The durable session exists now, so an in-flight upload can be cancelled.
        const created = item;
        const cancel = button(t("cancelUpload"), { variant: "small" });
        cancel.addEventListener("click", () =>
          run(cancel, async () => {
            epoch++;
            await ctx.api.cancelImport(created.id, owner.client_id);
            await rows();
          }),
        );
        const show = (done) =>
          progressRow(
            t("uploading", {
              name: file.name,
              done: bytes(done),
              total: bytes(file.size),
            }),
            done,
            file.size,
            cancel,
          );
        show(item.received_bytes || 0);
        if (!(await uploadChunks(item, file, owner, mine, show))) return;
        item = await ctx.api.finishImport(item.id, owner.client_id);
        if (!current(mine)) return;
        if (item.plan.action === "process" && !item.automatic)
          await ctx.api.startImport(item.id, owner.client_id);
        if (!current(mine)) return;
        progressRow(
          t("saved", {
            name: file.name,
            extra: item.plan.action === "process" ? t("submitted") : "",
          }),
        );
      }
      files = [];
      fileInput.value = "";
      attachInput.value = "";
      attachPaths.replaceChildren();
      drawFiles();
      acknowledge();
      toast(t("recorded"), { tone: "success" });
      await rows();
    } catch (error) {
      if (current(mine)) {
        toast(error.message, { tone: "error", detail: t("resumeHint") });
        await rows();
      }
    } finally {
      if (running === operation) running = null;
      submit.disabled = false;
    }
  }

  const updateIndex = button(t("updateIndex"), {
    variant: "small",
    iconName: "refresh",
  });
  updateIndex.addEventListener("click", () =>
    run(updateIndex, async () => {
      const owner = scope(),
        mine = epoch;
      indexState.textContent = t("indexUpdating");
      const result = await ctx.api.updateNoteIndex(owner);
      if (current(mine))
        indexState.textContent = t("indexDone", {
          u: number(result.updated_notes),
          r: number(result.reused_notes),
        });
    }),
  );
  function itemRow(item) {
    const actions = [];
    if (item.state === "uploading") {
      const resume = h("input.a-sr", { type: "file", id: "resume-" + item.id });
      resume.addEventListener("change", () => send([resume.files[0]], item));
      actions.push(
        resume,
        h(
          "label.a-button.is-small",
          { for: resume.id },
          icon("upload"),
          t("resume"),
        ),
      );
    }
    if (
      item.state === "archived" &&
      item.plan?.action === "process" &&
      !item.automatic
    ) {
      const start = button(t("start"), { variant: "small", iconName: "play" });
      start.addEventListener("click", () =>
        run(start, async () => {
          await ctx.api.startImport(item.id, item.client_id);
          await rows();
        }),
      );
      actions.push(start);
    }
    if (
      ["uploading", "prepared", "processing", "running", "paused"].includes(
        item.state,
      )
    ) {
      const cancel = button(t("cancel"), { variant: "small" });
      cancel.addEventListener("click", () =>
        run(cancel, async () => {
          epoch++;
          await ctx.api.cancelImport(item.id, item.client_id);
          await rows();
        }),
      );
      actions.push(cancel);
    }
    if (
      item.run_id &&
      ["needs_review", "needs_supplement", "needs_split"].includes(item.state)
    )
      actions.push(
        button(t("review"), {
          variant: "small",
          iconName: "chevron",
          onClick: () =>
            ctx.navigate("worklist", "documents", { id: item.run_id }),
        }),
      );
    const tones = {
      published: "success",
      failed: "error",
      interrupted: "error",
      cancelled: "info",
      archived: "info",
      uploading: "info",
    };
    const label = t("st_" + item.state);
    return h(
      "div.a-import-item",
      { dataset: { id: item.id } },
      listRow({
        iconName: item.expert ? "expert" : "file",
        title: item.filename,
        detail: [
          `${bytes(item.received_bytes)} / ${bytes(item.source_bytes)}`,
          item.run_id ? t("zeroBudget") : null,
          t("taskId", { id: item.id.slice(0, 8) }),
        ]
          .filter(Boolean)
          .join(" · "),
        status: [
          label === "st_" + item.state ? item.state : label,
          tones[item.state] || "waiting",
        ],
        actions,
      }),
      h(
        "div.a-import-extra",
        item.automatic
          ? h(
              "p.a-subtle",
              t("automatic", {
                phase: item.automatic.phase,
                state: item.automatic.state,
              }) + (item.automatic.reason ? " · " + item.automatic.reason : ""),
            )
          : null,
        (item.original_projections || []).map((o) =>
          h(
            "p.a-subtle",
            `${o.original_name} · ${o.state === "projected" ? t("projected") : t("projectPending")} · ${o.path || t("notWritten")}`,
          ),
        ),
        expertControls(ctx, item, rows),
      ),
    );
  }
  async function rows() {
    const mine = epoch,
      owner = scope();
    if (!owner.client_id || !owner.vault_id) {
      clear(listHost, empty(t("noScope"), { iconName: "key" }));
      indexState.textContent = t("indexPick");
      return;
    }
    const [items, commands] = await Promise.all([
      ctx.api.imports(owner.client_id, owner.vault_id),
      ctx.api.expertCommands(owner.client_id, owner.vault_id),
    ]);
    if (!current(mine)) return;
    clear(
      listHost,
      items.length
        ? items.map(itemRow)
        : empty(t("none"), { iconName: "imports" }),
    );
    clear(
      commandsHost,
      commands.length
        ? card(
            { title: t("commands"), className: "is-flush" },
            h(
              "div.a-rows",
              commands.map((c) =>
                listRow({
                  iconName: "expert",
                  title: c.event_id,
                  detail: c.next_action,
                  status: [c.state, "waiting"],
                }),
              ),
            ),
          )
        : null,
    );
    try {
      const index = await ctx.api.noteIndexStatus(
        owner.client_id,
        owner.vault_id,
      );
      if (current(mine))
        indexState.textContent =
          index.state === "ready" ? t("indexReady") : t("indexStale");
    } catch (error) {
      if (current(mine)) indexState.textContent = error.message;
    }
  }
  async function refresh() {
    if (running) return;
    const mine = ++epoch;
    let policy;
    try {
      [clients, vaults, policy] = await Promise.all([
        ctx.store.clients({ force: true }),
        ctx.store.vaults({ force: true }),
        ctx.api.sourcePolicy(),
      ]);
    } catch (error) {
      clear(listHost, failure(error, refresh));
      throw error;
    }
    if (!current(mine)) return;
    settings = policy;
    clients = clients.filter(
      (c) => c.active && c.grants.some((g) => g.can_add && g.can_original),
    );
    if (!hasUnsavedChanges(form)) {
      let preferred = {
        client_id: wanted.client || identity.value,
        vault_id: wanted.vault || vault.value,
      };
      if (!preferred.client_id) {
        const target = vaults.find(
          (v) => v.status === "active" && v.default_query,
        );
        if (target) {
          const automatic = await ctx.api.vaultSave
            .automaticPolicy(target.id)
            .catch(() => null);
          const permitted = (c) =>
            c.grants.some(
              (g) => g.vault_id === target.id && g.can_add && g.can_original,
            );
          const client =
            clients.find(
              (c) => c.id === automatic?.settings.client_id && permitted(c),
            ) || clients.find(permitted);
          if (client) preferred = { client_id: client.id, vault_id: target.id };
        }
      }
      setOptions(
        identity,
        clients.map((c) => [c.id, c.name]),
        clients.some((c) => c.id === preferred.client_id)
          ? preferred.client_id
          : undefined,
      );
      scopedVaults(preferred.vault_id);
      for (const key of LIMITS) {
        limits[key].value = policy.effective_policy.limits[key];
        limits[key].max = policy.effective_policy.limits[key];
      }
      rememberDraft(form);
    }
    applyEngine();
    drawFiles();
    await rows();
  }
  drawMode();
  drawEngines();
  const node = h(
    "div.a-stack",
    engineHost,
    h(
      "div.a-import-layout",
      card({}, form),
      h(
        "div.a-stack",
        card({ title: t("index"), actions: updateIndex }, indexState),
        card({ title: t("recent"), className: "is-flush" }, listHost),
        commandsHost,
      ),
    ),
  );
  return {
    node,
    header: {
      actions: [
        button(t("settings"), {
          variant: "ghost",
          iconName: "settings",
          onClick: () => ctx.navigate("inventory", "", { tab: "policy" }),
        }),
      ],
    },
    refresh,
    update(next) {
      const nextEngine =
        next.params?.engine === "fake-expert"
          ? "fake-expert"
          : next.params?.engine === "ordinary"
            ? "ordinary"
            : engine;
      if (nextEngine !== engine && !running) {
        resetDraft();
        engine = nextEngine;
        applyEngine();
      }
      return true;
    },
    dirty: () => files.length > 0 || running !== null,
    dispose() {
      epoch++;
    },
  };
}
