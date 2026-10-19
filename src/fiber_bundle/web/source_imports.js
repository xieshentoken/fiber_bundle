import { joinText, phrase, setText, uiText } from "./i18n/index.js";
import { mountImportForm } from "./source_import_form.js";
// Resumable bounded uploads; the backend owns originals, permissions and processing.
import { $, el, action, captureMessage } from "./ui.js";
import { addExpertControls } from "./expert_imports.js";
import { showJSON } from "./record_ui.js";
import {
  rememberDraft,
  hasUnsavedChanges,
  captureDraftSave,
} from "./unsaved_changes.js";

const states = {
  uploading: "上传中",
  archived: "原件已保存",
  prepared: "待处理",
  processing: "处理中",
  running: "完整 Skill 处理中",
  paused: "完整 Skill 暂停，等待显式补充",
  completed: "完整 Skill Draft 已生成，待候选导入确认",
  needs_review: "待审核",
  needs_split: "发布暂停 · 候选和审核保留（核对结构或调整新任务额度）",
  needs_supplement: "待补充附件或链接映射（补齐后重新导入）",
  published: "已发布 · 索引待核对",
  failed: "处理失败 · 原件保留",
  cancelled: "已取消",
  interrupted: "已中断",
};
export { fileSha } from "./file_digest.js";
import { fileSha } from "./file_digest.js";
export function encodeChunk(bytes) {
  let encoded = "";
  for (const byte of bytes) encoded += String.fromCharCode(byte);
  return window.btoa(encoded);
}
export { previewStatus } from "./source_import_preview.js";
export function createImportsView(api, refreshTasks) {
  let epoch = 0,
    initialized = false,
    settings = null,
    running = null,
    availableClients = [],
    availableVaults = [],
    formView = null,
    defaultsEpoch = 0,
    rowsEpoch = 0;
  const root = () => $("source-imports");
  const scope = () => ({
    client_id: $("import-client").value,
    vault_id: $("import-vault").value,
  });
  const current = (selected) => selected === epoch;
  async function applyDefaults() {
    const selected = ++defaultsEpoch,
      owner = scope(),
      form = $("import-form");
    if (!owner.vault_id || hasUnsavedChanges(form) || running) return;
    const policy = await api.vaultSave.automaticPolicy(owner.vault_id);
    if (
      selected !== defaultsEpoch ||
      scope().client_id !== owner.client_id ||
      scope().vault_id !== owner.vault_id ||
      hasUnsavedChanges(form) ||
      running
    )
      return;
    const kind =
      $("import-engine").value === "fake-expert" ? "expert" : "document";
    const ready =
      kind === "expert"
        ? !!settings.effective_policy.expert_runtime ||
          !!settings.project_skill?.settings.enabled
        : !!settings.effective_policy.document_image;
    $("import-action").value =
      ready &&
      (policy.settings.policy_version === 2
        ? policy.settings.enabled
        : policy.settings.review_mode === "automatic") &&
      policy.settings.client_id === owner.client_id &&
      policy.settings.allowed_kinds.includes(kind)
        ? "process"
        : "archive";
    rememberDraft(form);
  }
  function scopedVaults(previous = "") {
    const client = availableClients.find(
      (c) => c.id === $("import-client").value,
    );
    const permitted = availableVaults.filter(
      (v) =>
        v.status === "active" &&
        client?.grants.some(
          (g) => g.vault_id === v.id && g.can_add && g.can_original,
        ),
    );
    $("import-vault").replaceChildren();
    for (const item of permitted) {
      const option = el("option", item.name);
      option.value = item.id;
      $("import-vault").append(option);
    }
    if (permitted.some((v) => v.id === previous))
      $("import-vault").value = previous;
  }
  function clear() {
    epoch++;
    defaultsEpoch++;
    initialized = false;
    running = null;
    root().replaceChildren();
    formView = null;
  }
  async function indexState(selected, owner, read) {
    const matches = () =>
      current(selected) &&
      read === rowsEpoch &&
      scope().client_id === owner.client_id &&
      scope().vault_id === owner.vault_id;
    const output = $("import-index-state");
    if (!owner.client_id || !owner.vault_id) {
      setText(output, uiText("先选择来源身份和知识库。"));
      return;
    }
    try {
      const index = await api.noteIndexStatus(owner.client_id, owner.vault_id);
      if (!matches()) return;
      setText(
        output,
        index.state === "ready"
          ? uiText("持久索引已就绪")
          : uiText("索引待更新（发布、编辑、移动或删除后显式更新）"),
      );
    } catch (error) {
      if (matches()) setText(output, uiText(error.message));
    }
  }
  async function rows() {
    const selected = epoch,
      read = ++rowsEpoch,
      owner = scope();
    $("import-list").replaceChildren();
    if (!owner.client_id || !owner.vault_id) return;
    const [items, commands] = await Promise.all([
      api.imports(owner.client_id, owner.vault_id),
      api.expertCommands(owner.client_id, owner.vault_id),
    ]);
    await indexState(selected, owner, read);
    if (!current(selected) || read !== rowsEpoch) return;
    for (const item of items) {
      const row = el("article", null, "row"),
        summary = el(
          "p",
          phrase("{p0} · {p1} · {p2}/{p3} 字节", {
            p0: item.filename,
            p1: states[item.state] || item.state,
            p2: item.received_bytes,
            p3: item.source_bytes,
          }),
        );
      row.dataset.taskKind = "imports";
      row.dataset.taskId = item.id;
      row.append(summary);
      showJSON(
        row,
        { id: item.id, run_id: item.run_id, automatic: item.automatic },
        "导入诊断详情",
      );
      if (item.automatic)
        row.append(
          el(
            "p",
            phrase("系统处理 · {p0} · {p1}{p2}", {
              p0: item.automatic.phase,
              p1: uiText(item.automatic.state),
              p2: item.automatic.reason ? " · " + item.automatic.reason : "",
            }),
          ),
        );
      for (const original of item.original_projections || [])
        row.append(
          el(
            "p",
            joinText([
              "",
              original.original_name,
              " · ",
              original.state === "projected"
                ? uiText("原件副本已保存")
                : uiText("原件已归档，目录副本待核对"),
              " · ",
              original.path || "尚未写入目录",
              "",
            ]),
          ),
        );
      if (item.state === "uploading") {
        const resume = el("input");
        resume.type = "file";
        setText(
          resume,
          phrase("续传 {p0}", { p0: item.filename }),
          "aria-label",
        );
        resume.addEventListener("change", () => send([resume.files[0]], item));
        row.append(resume);
      }
      if (
        item.state === "archived" &&
        item.plan.action === "process" &&
        !item.automatic
      ) {
        const start = el("button", uiText("开始确定性提取"));
        action(start, async () => {
          await api.startImport(item.id, item.client_id);
          await refreshTasks();
        });
        row.append(start);
      }
      if (
        ["uploading", "prepared", "processing", "running", "paused"].includes(
          item.state,
        )
      ) {
        const cancel = el("button", uiText("取消"));
        action(cancel, async () => {
          epoch++;
          await api.cancelImport(item.id, item.client_id);
          await rows();
        });
        row.append(cancel);
      }
      if (item.run_id) {
        row.append(el("small", uiText("模型额度 0")));
        if (
          ["needs_review", "needs_supplement", "needs_split"].includes(
            item.state,
          )
        ) {
          const review = el("button", uiText("前往文档审核"));
          review.type = "button";
          review.addEventListener("click", () =>
            document.querySelector('[data-tab="documents"]').click(),
          );
          row.append(review);
        }
      }
      addExpertControls(row, item, api, rows);
      $("import-list").append(row);
    }
    for (const command of commands) {
      const row = el(
        "p",
        phrase("飞书指令 {p0} · {p1} · {p2}", {
          p0: command.event_id,
          p1: uiText(command.state),
          p2: command.next_action,
        }),
      );
      row.dataset.taskKind = "expert";
      row.dataset.taskId = command.event_id;
      $("import-list").append(row);
    }
    if (!items.length)
      $("import-list").append(
        el("p", uiText("当前范围暂无导入记录。"), "empty"),
      );
  }
  async function send(files, resume = null) {
    if (running || !files.length || !files[0]) return;
    const owner = scope(),
      selected = epoch,
      notify = captureMessage("imports", "source-imports");
    if (!owner.client_id || !owner.vault_id) {
      notify("请先选择具有新增和原件权限的来源身份与知识库。", true);
      return;
    }
    const cap = settings.effective_policy.limits;
    if (
      files.some((f) => f.size < 1 || f.size > cap.source_bytes) ||
      files.reduce((n, f) => n + f.size, 0) +
        Array.from($("import-attachments").files).reduce(
          (n, f) => n + f.size,
          0,
        ) >
        cap.batch_bytes
    ) {
      notify("文件或本批总量超过当前有效限制。", true);
      return;
    }
    const acknowledge = captureDraftSave($("import-form"));
    const operation = Symbol("import");
    running = operation;
    const progress = $("import-progress"),
      input = $("import-files"),
      submit = $("import-submit");
    submit.disabled = true;
    try {
      const references = {};
      if (!resume) {
        for (const companion of $("import-attachments").files) {
          const control = Array.from(
            $("import-attachment-paths").querySelectorAll("input"),
          ).find((field) => field.dataset.filename === companion.name);
          const literal = control.value.trim();
          if (!literal || references[literal])
            throw new Error("每个附件需要唯一的正文引用路径。");
          if (companion.size < 1 || companion.size > cap.source_bytes)
            throw new Error("附件超过当前大小限制。");
          const attachment = await api.createImport({
            ...owner,
            submission_id: window.crypto.randomUUID(),
            filename: companion.name,
            source_sha256: await fileSha(companion),
            source_bytes: companion.size,
            action: "archive",
            engine: "ordinary",
            configuration_sha256: settings.configuration_sha256,
            overrides: {},
            references: {},
          });
          for (
            let offset = 0;
            offset < companion.size;
            offset += attachment.chunk_bytes
          ) {
            if (!current(selected)) return;
            const blob = companion.slice(
              offset,
              offset + attachment.chunk_bytes,
            );
            await api.importChunk(attachment.id, {
              client_id: owner.client_id,
              ordinal: Math.floor(offset / attachment.chunk_bytes),
              sha256: await fileSha(blob),
              data: encodeChunk(new Uint8Array(await blob.arrayBuffer())),
            });
          }
          if (!current(selected)) return;
          await api.finishImport(attachment.id, owner.client_id);
          references[literal] = attachment.id;
        }
      }
      for (const file of files) {
        if (!current(selected)) return;
        setText(progress, phrase("{p0} · 校验文件摘要…", { p0: file.name }));
        const sha = await fileSha(file);
        if (!current(selected)) return;
        let item = resume;
        if (
          item &&
          (item.source_sha256 !== sha ||
            item.source_bytes !== file.size ||
            item.filename !== file.name)
        )
          throw new Error("续传文件与已冻结原件不一致。");
        if (!item) {
          const overrides = {};
          for (const key of [
            "source_bytes",
            "pdf_pages",
            "markdown_bytes",
            "markdown_total_bytes",
          ]) {
            const value = Number($("import-limit-" + key).value);
            if (!Number.isSafeInteger(value) || value < 1 || value > cap[key])
              throw new Error("单次限制必须在管理员配置范围内。");
            overrides[key] = value;
          }
          item = await api.createImport({
            ...owner,
            submission_id: window.crypto.randomUUID(),
            filename: file.name,
            source_sha256: sha,
            source_bytes: file.size,
            action: $("import-action").value,
            engine: $("import-engine").value,
            profile: $("import-profile").value,
            expert_profile: $("import-expert-profile").value,
            expert_backend: $("import-expert-backend").value || null,
            workpack_import_id: $("import-workpack-id").value.trim() || null,
            configuration_sha256: settings.configuration_sha256,
            references,
            overrides,
          });
        }
        if (!current(selected)) return;
        // Show the durable session immediately so an in-flight upload can be cancelled.
        setText(progress, phrase("{p0} · 上传中", { p0: file.name }));
        const cancel = el("button", uiText("取消当前上传"));
        cancel.type = "button";
        action(cancel, async () => {
          epoch++;
          await api.cancelImport(item.id, owner.client_id);
          await rows();
        });
        progress.append(cancel);
        for (
          let offset = item.received_bytes;
          offset < file.size;
          offset += item.chunk_bytes
        ) {
          if (!current(selected)) return;
          const blob = file.slice(offset, offset + item.chunk_bytes),
            bytes = new Uint8Array(await blob.arrayBuffer()),
            chunkSha = await fileSha(blob);
          if (!current(selected)) return;
          await api.importChunk(item.id, {
            client_id: owner.client_id,
            ordinal: Math.floor(offset / item.chunk_bytes),
            sha256: chunkSha,
            data: encodeChunk(bytes),
          });
          if (!current(selected)) return;
          setText(
            cancel,
            phrase("取消当前上传 · {p0}/{p1}", {
              p0: Math.min(file.size, offset + item.chunk_bytes),
              p1: file.size,
            }),
          );
        }
        item = await api.finishImport(item.id, owner.client_id);
        if (!current(selected)) return;
        if (item.plan.action === "process" && !item.automatic)
          await api.startImport(item.id, owner.client_id);
        if (!current(selected)) return;
        setText(
          progress,
          phrase("{p0} · 原件已保存{p1}", {
            p0: file.name,
            p1:
              item.plan.action === "process"
                ? uiText("，提取已提交")
                : uiText(""),
          }),
        );
      }
      input.value = "";
      $("import-file-previews").replaceChildren();
      $("import-attachments").value = "";
      $("import-attachment-paths").replaceChildren();
      acknowledge();
      notify(
        "导入已记录。按本库已启用的审核与发布策略处理；异常请查看待处理状态。",
      );
      await rows();
      await refreshTasks();
    } catch (error) {
      if (current(selected)) {
        notify(
          error.message + " 已接收的分块保留，可刷新后选择相同文件续传。",
          true,
        );
        await rows();
      }
    } finally {
      if (running === operation) running = null;
      submit.disabled = false;
    }
  }
  async function refresh() {
    if (running) return;
    const selected = ++epoch;
    const [clients, vaults, policy] = await Promise.all([
      api.clients(),
      api.vaults(),
      api.sourcePolicy(),
    ]);
    if (!current(selected)) return;
    settings = policy;
    availableClients = clients.filter(
      (c) => c.active && c.grants.some((g) => g.can_add && g.can_original),
    );
    availableVaults = vaults;
    if (!initialized) {
      formView = mountImportForm({
        root: root(),
        getPolicy: () => settings,
        send,
        rows,
        scope,
        onClientChange: scopedVaults,
        onScopeChange: () => {
          epoch++;
          defaultsEpoch++;
        },
        onEngineChange: () =>
          applyDefaults().catch((error) =>
            captureMessage("imports")(error.message, true),
          ),
        generation: () => epoch,
        current,
        updateIndexRequest: (owner) => api.updateNoteIndex(owner),
        notify: captureMessage("imports"),
        isRunning: () => running !== null,
      });
      initialized = true;
    }

    const form = $("import-form"),
      dirty = hasUnsavedChanges(form);
    if (!dirty && !running) {
      const saved = scope();
      if (!saved.client_id && !saved.vault_id) {
        const target = availableVaults.find(
          (v) => v.status === "active" && v.default_query,
        );
        if (target) {
          const automatic = await api.vaultSave.automaticPolicy(target.id);
          if (!current(selected)) return;
          const permitted = (c) =>
            c.grants.some(
              (g) => g.vault_id === target.id && g.can_add && g.can_original,
            );
          const client =
            availableClients.find(
              (c) => c.id === automatic.settings.client_id && permitted(c),
            ) || availableClients.find(permitted);
          if (client) {
            saved.client_id = client.id;
            saved.vault_id = target.id;
          }
        }
      }
      $("import-client").replaceChildren();
      $("import-vault").replaceChildren();
      for (const item of availableClients) {
        const option = el("option", item.name);
        option.value = item.id;
        $("import-client").append(option);
      }
      if (availableClients.some((c) => c.id === saved.client_id))
        $("import-client").value = saved.client_id;
      scopedVaults(saved.vault_id);
      for (const key of [
        "source_bytes",
        "pdf_pages",
        "markdown_bytes",
        "markdown_total_bytes",
      ]) {
        const field = $("import-limit-" + key);
        field.value = policy.effective_policy.limits[key];
        field.max = field.value;
      }
      rememberDraft(form);
      await applyDefaults();
    }
    formView.refreshCapability();
    await rows();
  }
  async function selectScope(clientId, vaultId) {
    if (running || hasUnsavedChanges($("import-form"))) return;
    if (
      !availableClients.some(
        (c) =>
          c.id === clientId &&
          c.grants.some(
            (g) => g.vault_id === vaultId && g.can_add && g.can_original,
          ),
      )
    )
      return;
    epoch++;
    defaultsEpoch++;
    $("import-client").value = clientId;
    scopedVaults(vaultId);
    rememberDraft($("import-form"));
    await applyDefaults();
    await rows();
  }
  return { clear, refresh, selectScope };
}
