import { phrase, setText, uiText } from "./i18n/index.js";
// Import form presentation only. Uploads and immutable engine requests belong to the view/backend.
import { $, el, action } from "./ui.js";
import { rememberDraft, hasUnsavedChanges } from "./unsaved_changes.js";
import { previewStatus } from "./source_import_preview.js";
import { createSections } from "./view_sections.js";

export function mountImportForm({
  root,
  getPolicy,
  send,
  rows,
  scope,
  onClientChange,
  onScopeChange,
  onEngineChange,
  generation,
  current,
  updateIndexRequest,
  notify,
  isRunning,
}) {
  const entries = el("div", null, "button-row engine-cards"),
    notice = el("div"),
    panel = el("section"),
    form = el("form");
  panel.hidden = true;
  panel.id = "import-panel";
  form.id = "import-form";
  form.className = "uv-import-form";
  form.dataset.draft = "tracked";
  panel.append(
    el("h3", uiText("普通资料导入")),
    el("p", uiText("选择本地文件；原件保留，确定性提取模型额度为 0。")),
  );
  for (const [id, title] of [
    ["import-client", "来源身份"],
    ["import-vault", "目标知识库"],
    ["import-action", "导入方式"],

    ["import-profile", "提取模式"],
  ]) {
    const label = el("label", uiText(title)),
      select = el("select");
    select.id = id;
    if (["import-client", "import-vault"].includes(id))
      select.dataset.draft = "ignore";
    label.append(select);
    form.append(label);
  }
  // Form must be mounted before looking up controls by id.
  panel.append(form);
  root.replaceChildren(entries, notice, panel);
  const actionSelect = $("import-action");
  for (const [value, title] of [
    ["archive", "保存原件"],
    ["process", "导入并处理"],
  ]) {
    const option = el("option", uiText(title));
    option.value = value;
    actionSelect.append(option);
  }
  for (const [value, title] of [
    ["native", "原生文本 / Office XML"],
    ["ocr", "图片 / 扫描 PDF OCR（需要资格凭据）"],
    ["conversion", "旧版 DOC 转换（需要资格凭据）"],
  ]) {
    const option = el("option", uiText(title));
    option.value = value;
    $("import-profile").append(option);
  }
  const engine = el("input");
  engine.id = "import-engine";
  engine.type = "hidden";
  engine.value = "ordinary";
  form.append(engine);
  for (const [id, title, values] of [
    [
      "import-expert-profile",
      "完整 Skill 模式",
      ["native-text", "scan-text", "scan-structure", "visual"],
    ],
    [
      "import-expert-backend",
      "明确后端（扫描/视觉须有对应独立运行条件）",
      [
        "",
        "pypdf-native",
        "paddleocr-ppocrv6",
        "pymupdf-tesseract",
        "docling",
        "paddleocr-ppstructure-v3",
        "pdftoppm",
      ],
    ],
  ]) {
    const label = el("label", uiText(title)),
      select = el("select");
    select.id = id;
    for (const value of values) {
      const option = el("option", value || uiText("原生默认"));
      option.value = value;
      select.append(option);
    }
    label.append(select);
    form.append(label);
  }
  const packLabel = el(
      "label",
      uiText(
        "可选 workpack ZIP 导入编号（Pi 模式按目标库策略生成章节材料；扫描仍需结构审核）",
      ),
    ),
    pack = el("input");
  pack.id = "import-workpack-id";
  packLabel.append(pack);
  form.append(packLabel);
  const input = el("input");
  input.id = "import-files";
  input.type = "file";
  input.multiple = true;
  setText(input, uiText("选择导入文件"), "aria-label");
  const drop = el("div", uiText("拖放文件到这里，或选择文件"), "import-drop");
  drop.tabIndex = 0;
  drop.addEventListener("dragover", (event) => event.preventDefault());
  drop.addEventListener("drop", (event) => {
    event.preventDefault();
    input.files = event.dataTransfer.files;
    input.dispatchEvent(new window.Event("change", { bubbles: true }));
  });
  const selectedFiles = el("div");
  selectedFiles.id = "import-file-previews";
  input.addEventListener("change", () => {
    selectedFiles.replaceChildren();
    for (const file of input.files) {
      const ext = file.name.split(".").pop().toLowerCase();
      selectedFiles.append(
        el(
          "p",
          phrase("{p0} · {p1} · {p2} 字节 · {p3}", {
            p0: file.name,
            p1: ext,
            p2: file.size,
            p3: uiText(
              previewStatus(
                ext,
                getPolicy().effective_policy,
                $("import-engine").value,
                getPolicy().project_skill,
              ),
            ),
          }),
        ),
      );
    }
  });
  form.prepend(
    el("h4", uiText("1 · 选择文件")),
    drop,
    input,
    selectedFiles,
    el("h4", uiText("2 · 选择目标与处理方式")),
  );
  const attachments = el("input");
  attachments.id = "import-attachments";
  attachments.type = "file";
  attachments.multiple = true;
  setText(attachments, uiText("提供 Markdown 附件"), "aria-label");
  const paths = el("div");
  paths.id = "import-attachment-paths";
  attachments.addEventListener("change", () => {
    paths.replaceChildren();
    for (const file of attachments.files) {
      const label = el(
          "label",
          phrase("{p0} 在正文中的引用路径", { p0: file.name }),
        ),
        path = el("input");
      path.value = file.name;
      path.dataset.filename = file.name;
      label.append(path);
      paths.append(label);
    }
  });
  form.append(
    el(
      "p",
      uiText(
        "Markdown 相对附件：选择配套文件并填写正文中的原引用路径；缺件不会自动读取或下载。",
      ),
    ),
    attachments,
    paths,
  );
  for (const [key, title] of [
    ["source_bytes", "单文件字节上限"],
    ["pdf_pages", "PDF 页数上限"],
    ["markdown_bytes", "单 Markdown 字节上限"],
    ["markdown_total_bytes", "Markdown 总字节上限"],
  ]) {
    const label = el("label", uiText(title)),
      field = el("input");
    field.type = "number";
    field.min = "1";
    field.id = "import-limit-" + key;
    label.append(field);
    form.append(label);
  }
  const submit = el("button", uiText("开始导入"), "primary");
  submit.id = "import-submit";
  submit.type = "submit";
  const progress = el("p");
  progress.id = "import-progress";
  progress.setAttribute("aria-live", "polite");
  const list = el("div");
  list.id = "import-list";
  const reload = el("button", uiText("刷新导入状态"));
  reload.type = "button";
  action(reload, rows);
  const shortcut = el("a", uiText("资料处理设置"));
  shortcut.href = "#inventory-panel";
  shortcut.dataset.pageTarget = "inventory";
  shortcut.dataset.openSourcePolicy = "true";
  shortcut.addEventListener("click", (event) => event.preventDefault());
  form.append(submit);
  const indexStatus = el("p"),
    updateIndex = el("button", uiText("更新当前库索引"));
  indexStatus.id = "import-index-state";
  indexStatus.setAttribute("aria-live", "polite");
  updateIndex.type = "button";
  action(updateIndex, async () => {
    const owner = scope(),
      selected = generation();
    setText(indexStatus, uiText("正在更新索引；不会调用模型或再次发布。"));
    const result = await updateIndexRequest(owner);
    if (!current(selected)) return;
    setText(
      indexStatus,
      phrase("持久索引已就绪 · 更新 {p0} 篇，复用 {p1} 篇", {
        p0: result.updated_notes,
        p1: result.reused_notes,
      }),
    );
  });
  const tools = el("div", null, "panel-actions");
  tools.append(shortcut, reload);
  panel.append(tools, progress, indexStatus, updateIndex, list);
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    importViews.select("tasks");
    send(Array.from(input.files));
  });
  for (const id of ["import-client", "import-vault"])
    $(id).addEventListener("change", () => {
      if (id === "import-client") onClientChange();
      onScopeChange();
      rows()
        .then(onEngineChange)
        .catch((error) => notify(error.message, true));
    });

  const basics = el("div", null, "import-basics");
  basics.append(
    $("import-client").closest("label"),
    $("import-vault").closest("label"),
  );
  form.insertBefore(basics, $("import-action").closest("label"));
  const advanced = el("details", null, "import-advanced");
  advanced.append(el("summary", uiText("提取模式与上限（进阶设置）")));
  for (const id of [
    "import-profile",
    "import-expert-profile",
    "import-expert-backend",
    "import-workpack-id",
    "import-limit-source_bytes",
    "import-limit-pdf_pages",
    "import-limit-markdown_bytes",
    "import-limit-markdown_total_bytes",
  ])
    advanced.append($(id).closest("label"));
  form.append(advanced);
  form.append(submit);

  const importViews = createSections(
    panel,
    [
      { id: "prepare", label: "准备导入", nodes: [form] },
      {
        id: "tasks",
        label: "导入进度与结果",
        nodes: [tools, indexStatus, updateIndex, list],
      },
    ],
    { id: "import-work" },
  );

  const hasDraft = () =>
    hasUnsavedChanges(form) ||
    input.files.length > 0 ||
    attachments.files.length > 0;
  const title = panel.querySelector("h3");
  const capability = el("p");
  capability.setAttribute("aria-live", "polite");
  form.prepend(capability);
  const expertIds = [
    "import-expert-profile",
    "import-expert-backend",
    "import-workpack-id",
  ];
  const ordinaryIds = ["import-profile", "import-attachments"];
  function selectEngine(value) {
    notice.replaceChildren();
    engine.value = value;
    const expert = value === "fake-expert";
    setText(
      title,
      expert ? uiText("fake-expert 专用导入") : uiText("普通资料导入"),
    );
    input.accept = expert ? ".pdf,application/pdf" : "";
    for (const id of expertIds) $(id).closest("label").hidden = !expert;
    for (const id of ordinaryIds) {
      const field = $(id);
      (field.closest("label") || field).hidden = expert;
    }
    paths.hidden = expert;
    setText(
      capability,
      expert
        ? getPolicy().effective_policy.expert_runtime
          ? uiText(
              "固定使用完整 fake-expert；扫描须选择合格 profile。新处理策略将有效 Draft 保存到 Inbox；既有策略沿用 Book，语义 Review 仍 deferred。",
            )
          : uiText(
              "完整 fake-expert 运行环境未配置；入口保留，可保存原件，处理不会由普通引擎代跑。",
            )
        : uiText(
            "固定使用 ordinary。普通笔记进入 Inbox；未知 ZIP 仅保存原件，不作为专家 workpack。",
          ),
    );
    panel.hidden = false;
    for (const button of entries.children)
      button.setAttribute(
        "aria-pressed",
        String(button.dataset.engine === value),
      );
    rememberDraft(form);
    onEngineChange();
  }
  for (const [value, text, description] of [
    [
      "ordinary",
      "普通资料导入",
      "文档、文章与音视频按当前提取策略处理；原件、候选和正式入库分别保留。",
    ],
    [
      "fake-expert",
      "fake-expert 专用导入",
      "固定 PDF 与完整专家 runtime；workpack 单独绑定，只有完整 Draft 进入 Book。",
    ],
  ]) {
    const button = el("button", null, "engine-card");
    button.append(el("strong", uiText(text)), el("small", uiText(description)));
    button.type = "button";
    button.dataset.engine = value;
    button.setAttribute("aria-controls", panel.id);
    button.setAttribute("aria-pressed", "false");
    button.addEventListener("click", () => {
      importViews.select("prepare");
      if (engine.value === value && !panel.hidden) return;
      if (isRunning()) {
        setText(
          notice,
          uiText("正在上传，请等待完成或取消当前上传后再切换入口。"),
        );
        return;
      }
      if (hasDraft()) {
        notice.replaceChildren(
          el("p", uiText("当前导入草稿尚未提交。切换入口前请明确清除草稿。")),
        );
        const discard = el("button", uiText("清除当前草稿并切换入口"));
        discard.type = "button";
        discard.onclick = () => {
          input.value = "";
          attachments.value = "";
          paths.replaceChildren();
          selectedFiles.replaceChildren();
          $("import-workpack-id").value = "";
          $("import-profile").value = "native";
          $("import-expert-profile").value = "native-text";
          $("import-expert-backend").value = "";
          $("import-action").value = "archive";
          for (const key of [
            "source_bytes",
            "pdf_pages",
            "markdown_bytes",
            "markdown_total_bytes",
          ])
            $("import-limit-" + key).value =
              getPolicy().effective_policy.limits[key];
          selectEngine(value);
        };
        notice.append(discard);
        return;
      }
      selectEngine(value);
    });
    entries.append(button);
  }
  for (const id of expertIds) $(id).closest("label").hidden = true;
  return {
    refreshCapability: () => {
      if (!panel.hidden && !hasUnsavedChanges(form)) selectEngine(engine.value);
    },
  };
}
