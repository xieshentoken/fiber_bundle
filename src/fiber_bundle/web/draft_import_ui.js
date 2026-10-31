// Shared desktop/browser dialog. A frozen preview needs an explicit confirmation before copying.
import { el } from "./ui.js";
import { onLocaleChange } from "./i18n/index.js";
import { chooseDirectory } from "./native_directory_picker.js";
import { draftText as t, taskSummary } from "./draft_import_labels.js";

export function importScopeAllowed(client, vault) {
  return (
    client?.active &&
    vault?.status === "active" &&
    !vault.read_only &&
    client.grants?.some(
      (g) => g.vault_id === vault.id && g.can_add && g.can_query,
    )
  );
}
export function canConfirmDraft(task, acknowledged) {
  return task?.state === "preview" && !task.confirmed && acknowledged === true;
}
export function openDraftImporter(api, initial = {}) {
  const previous = document.activeElement;
  const labels = new Map();
  function bind(node, key) {
    labels.set(node, key);
    return node;
  }
  const dialog = el("dialog", null, "draft-import-dialog");
  const heading = el("h2", t("title"));
  heading.id = "draft-import-title";
  dialog.setAttribute("aria-labelledby", heading.id);
  const close = button("close", () => dialog.close());
  const identity = el("select"),
    vault = el("select"),
    source = el("input"),
    folder = el("input");
  source.type = folder.type = "text";
  const choose = button("choose", async () => {
    const before = source.value;
    const selected = await chooseDirectory(before);
    if (selected && dialog.open && source.value === before)
      source.value = selected;
  });
  const status = el("p");
  status.setAttribute("role", "status");
  const tasksHost = el("div", null, "draft-import-tasks");
  const preview = button("preview", async () => {
    if (!source.value.trim()) throw new Error(t("sourceMissing"));
    if (!identity.value || !vault.value) throw new Error(t("noscope"));
    await api.create({
      ...scope(),
      submission_id: crypto.randomUUID(),
      source_path: source.value.trim(),
      folder: folder.value.trim(),
    });
    await refresh();
  });
  const refreshButton = button("refresh", refresh);
  const header = el("header");
  header.append(heading, close);
  const scopes = el("div", null, "draft-import-grid");
  scopes.append(field("identity", identity), field("vault", vault));
  dialog.append(
    header,
    el("p", t("help")),
    scopes,
    field("source", source),
    choose,
    field("folder", folder),
    preview,
    status,
    el("h3", t("history")),
    refreshButton,
    tasksHost,
  );
  let clients = [],
    vaults = [],
    epoch = 0,
    timer,
    closed = false;
  const choices = new Map();
  const scope = () => ({ client_id: identity.value, vault_id: vault.value });
  function field(key, node) {
    const label = el("label");
    label.append(bind(document.createTextNode(t(key)), key), node);
    return label;
  }
  function button(key, handler) {
    const node = bind(el("button", t(key)), key);
    node.type = "button";
    node.addEventListener("click", async () => {
      if (node.disabled) return;
      node.disabled = true;
      try {
        await handler();
      } catch (error) {
        status.textContent = error.message || t("interrupted");
      } finally {
        if (node.isConnected) node.disabled = false;
      }
    });
    return node;
  }
  function changed() {
    ++epoch;
    choices.clear();
    tasksHost.replaceChildren();
    void refresh().catch(failed);
  }
  function setVaults(wanted = "") {
    vault.replaceChildren();
    for (const item of vaults.filter((v) =>
      importScopeAllowed(
        clients.find((c) => c.id === identity.value),
        v,
      ),
    )) {
      const option = el("option", item.name);
      option.value = item.id;
      vault.append(option);
    }
    if ([...vault.options].some((o) => o.value === wanted))
      vault.value = wanted;
  }
  identity.addEventListener("change", () => {
    setVaults();
    changed();
  });
  vault.addEventListener("change", changed);
  function failed(error) {
    if (!closed) status.textContent = error.message || t("interrupted");
  }
  async function refresh() {
    clearTimeout(timer);
    if (closed || !identity.value || !vault.value) return;
    const mine = ++epoch,
      owner = scope();
    const result = await api.list(owner);
    if (closed || mine !== epoch) return;
    for (const [node] of labels) if (!node.isConnected) labels.delete(node);
    tasksHost.replaceChildren();
    status.textContent = "";
    if (!result.items.length) tasksHost.append(el("p", t("empty")));
    for (const task of result.items) draw(task, owner);
    if (result.items.some((task) => ["queued", "working"].includes(task.state)))
      timer = setTimeout(() => refresh().catch(failed), 1500);
  }
  function draw(task, owner) {
    const card = el("article", null, "draft-import-task");
    card.dataset.taskId = task.id;
    card.append(
      el("h4", task.package?.title || task.source_path.split("/").pop()),
      el("p", taskSummary(task)),
    );
    if (task.target_path)
      card.append(el("p", t("target") + ": " + task.target_path));
    if (task.package)
      card.append(
        el(
          "p",
          `${task.package.file_count} ${t("files")} · ${(task.total_bytes / 1024 / 1024).toFixed(2)} MB · Draft / deferred`,
        ),
      );
    if (task.reuse) card.append(el("p", t("reuse")));
    if (task.ignored_files?.length) card.append(el("p", t("ignored")));
    if (task.confirmed && !task.reuse) {
      const progress = el("progress");
      progress.max = task.total_bytes || 1;
      progress.value = task.copied_bytes || 0;
      progress.setAttribute("aria-label", t("copy"));
      card.append(
        progress,
        el(
          "p",
          `${task.copied_files || 0}/${task.package?.file_count || 0} ${t("files")} · ${(task.copied_bytes / 1024 / 1024).toFixed(2)} MB`,
        ),
      );
    }
    for (const item of task.moc_results || [])
      card.append(
        el("p", `${item.title} · ${item.completed ? "✓" : item.error}`),
      );
    if (task.state === "preview") {
      const chosen = choices.get(task.id) || {
        mocs: new Set(),
        acknowledged: false,
        filter: "",
      };
      choices.set(task.id, chosen);
      const picker = el("fieldset"),
        legend = el("legend", t("mocs")),
        search = el("input");
      search.placeholder = t("search");
      search.value = chosen.filter;
      const list = el("div", null, "draft-import-mocs");
      const drawMocs = () => {
        list.replaceChildren();
        for (const moc of (task.mocs || []).filter((m) =>
          m.title.toLowerCase().includes(chosen.filter.toLowerCase()),
        )) {
          const box = el("input"),
            label = el("label");
          box.type = "checkbox";
          box.checked = chosen.mocs.has(moc.id);
          box.addEventListener("change", () =>
            box.checked ? chosen.mocs.add(moc.id) : chosen.mocs.delete(moc.id),
          );
          label.append(box, document.createTextNode(moc.title));
          list.append(label);
        }
      };
      search.addEventListener("input", () => {
        chosen.filter = search.value;
        drawMocs();
      });
      drawMocs();
      picker.append(legend, search, list, el("p", t("none")));
      const ack = el("input"),
        ackLabel = el("label");
      ack.type = "checkbox";
      ack.checked = chosen.acknowledged;
      ackLabel.append(ack, document.createTextNode(t("acknowledge")));
      const confirm = button("confirm", async () => {
        if (!canConfirmDraft(task, chosen.acknowledged)) return;
        // Once sent, refresh before any subsequent submission, including timeout recovery.
        try {
          await api.confirm(task.id, {
            ...owner,
            preview_sha256: task.preview_sha256,
            moc_ids: [...chosen.mocs],
            candidate_acknowledged: true,
          });
        } finally {
          await refresh();
        }
      });
      confirm.disabled = !canConfirmDraft(task, chosen.acknowledged);
      ack.addEventListener("change", () => {
        chosen.acknowledged = ack.checked;
        confirm.disabled = !ack.checked;
      });
      card.append(picker, ackLabel, confirm);
    }
    if (["queued", "working", "preview"].includes(task.state))
      card.append(
        button("cancel", async () => {
          await api.action(task.id, "cancel", owner);
          await refresh();
        }),
      );
    if (["failed", "partial", "cancelled", "interrupted"].includes(task.state))
      card.append(
        button("resume", async () => {
          await api.action(task.id, "resume", owner);
          await refresh();
        }),
      );
    tasksHost.append(card);
  }
  const offLocale = onLocaleChange(() => {
    if (!closed) {
      heading.textContent = t("title");
      for (const [node, key] of labels)
        if (node.isConnected) node.textContent = t(key);
      void refresh().catch(failed);
    }
  });
  dialog.addEventListener(
    "close",
    () => {
      closed = true;
      epoch++;
      clearTimeout(timer);
      offLocale();
      dialog.remove();
      if (previous?.isConnected) previous.focus();
    },
    { once: true },
  );
  document.body.append(dialog);
  dialog.showModal();
  status.textContent = t("loading");
  void api
    .options()
    .then((result) => {
      if (closed) return;
      clients = result.clients.filter(
        (c) => c.active && c.grants?.some((g) => g.can_add && g.can_query),
      );
      vaults = result.vaults;
      for (const client of clients) {
        const option = el("option", client.name);
        option.value = client.id;
        identity.append(option);
      }
      if (clients.some((c) => c.id === initial.client_id))
        identity.value = initial.client_id;
      setVaults(initial.vault_id);
      return refresh();
    })
    .catch(failed);
  return { close: () => dialog.close(), node: dialog };
}
