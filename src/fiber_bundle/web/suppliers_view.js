// Shared supplier workspace: scoped reads and explicit commands; no filesystem access.
import { onLocaleChange } from "./i18n/index.js";
import {
  captureDraftSave,
  hasUnsavedChanges,
  rememberDraft,
} from "./unsaved_changes.js";
import { t } from "./suppliers_labels.js";
import {
  el,
  field,
  input,
  select,
  keys,
  button,
  createManualForm,
  createPolicyForm,
  createQueryForm,
} from "./suppliers_components.js";

export function createSupplierView(api, root, { navigate } = {}) {
  root.classList.add("supplier-workspace");
  let generation = 0,
    controller = null,
    initialized = false,
    scopeValue = null,
    data = null,
    preview = null,
    tab = "suppliers",
    offset = 0,
    result = null;
  let poll = null;
  let clientSelect,
    vaultSelect,
    status,
    records,
    controls,
    manualForm,
    policyForm,
    details,
    queryForm,
    correctionHost;
  const textLocale = onLocaleChange(() => {
    for (const n of root.querySelectorAll("[data-supplier-key]"))
      n.textContent = t(n.dataset.supplierKey);
    render();
  });
  const scope = () => ({
    client_id: clientSelect.value,
    vault_id: vaultSelect.value,
  });
  function say(value, error = false) {
    status.replaceChildren(el("p", { textContent: value }));
    status.dataset.tone = error ? "error" : "info";
  }
  async function action(work, trigger) {
    const mine = generation;
    trigger && (trigger.disabled = true);
    try {
      const value = await work();
      if (mine !== generation) return;
      say(t("recorded"));
      await load();
      return value;
    } catch (e) {
      if (mine === generation)
        say(
          t(e.code) === e.code
            ? `${t("error")} ${/^supplier_[a-z_]+$/.test(e.code || "") ? e.code : ""}`
            : t(e.code),
          true,
        );
    } finally {
      trigger && (trigger.disabled = false);
    }
  }
  function section(key, ...children) {
    return el(
      "section",
      { className: "supplier-section" },
      el("h2", { key }),
      ...children,
    );
  }
  function initialize() {
    clientSelect = select("client", [["", "pick"]]);
    vaultSelect = select("vault", [["", "pick"]]);
    for (const node of [clientSelect, vaultSelect]) {
      node.dataset.draft = "ignore";
      node.addEventListener("change", () => {
        if (hasUnsavedChanges(root) && !window.confirm(t("discard"))) {
          clientSelect.value = scopeValue?.client_id || "";
          vaultSelect.value = scopeValue?.vault_id || "";
          return;
        }
        manualForm.reset();
        policyForm.reset();
        captureDraftSave(policyForm)();
        correctionHost.replaceChildren();
        captureDraftSave(manualForm)();
        scopeValue = scope();
        preview = null;
        result = null;
        offset = 0;
        void refresh();
      });
    }
    status = el("div", { role: "status", className: "supplier-status" });
    records = el("div");
    details = el("div", { className: "supplier-evidence", hidden: true });
    correctionHost = el("div");
    const nav = el("div", { className: "supplier-tabs", role: "tablist" });
    for (const key of ["suppliers", "offers", "issues", "manual", "settings"]) {
      const b = button(key, () => {
        tab = key;
        render();
      });
      b.setAttribute("role", "tab");
      b.dataset.supplierTab = key;
      nav.append(b);
    }
    manualForm = createManualForm({ api, scope, action });
    policyForm = createPolicyForm({
      api,
      scope,
      action,
      getPreview: () => preview,
      setPreview: (value) => {
        preview = value;
      },
      getData: () => data,
    });
    queryForm = createQueryForm(() => {
      offset = 0;
      void search();
    });
    controls = el(
      "div",
      { className: "supplier-scope" },
      field("client", clientSelect),
      field("vault", vaultSelect),
      button("refresh", () => void refresh()),
    );
    root.replaceChildren(
      controls,
      status,
      nav,
      queryForm,
      manualForm,
      policyForm,
      records,
      correctionHost,
      details,
    );
    initialized = true;
    rememberDraft(root);
  }
  async function search() {
    const f = keys(queryForm),
      bound = scope();
    await action(async () => {
      result = await api.suppliers.query({
        ...bound,
        text: f.keyword,
        model: f.sku,
        price_max: f.priceMax || null,
        currency: f.currency || null,
        price_unit: f.unit || null,
        tax: f.tax || null,
        quoted_after: f.after || null,
        quoted_before: f.before || null,
        valid_on: f.validOn || null,
        category: f.category,
        specs: f.specField
          ? [
              {
                field: f.specField,
                equals: f.specValue || null,
                minimum: f.specMin || null,
                maximum: f.specMax || null,
                unit: f.specUnit,
              },
            ]
          : [],
        expected_snapshot: offset > 0 ? result?.snapshot || null : null,
        include_legacy: f.includeLegacy,
        offset,
        limit: 25,
      });
      return result;
    });
  }
  function showEvidence(id) {
    void action(async () => {
      const value = await api.suppliers.evidence(
        clientSelect.value,
        vaultSelect.value,
        id,
      );
      details.hidden = false;
      details.replaceChildren(
        document.createTextNode(
          `${t(value.legacy ? "legacy" : "retained")}\n\n${value.text}\n`,
        ),
      );
      for (const asset of value.assets || [])
        details.append(
          el("a", {
            href:
              api.suppliers.originalUrl(
                clientSelect.value,
                vaultSelect.value,
                id,
              ) +
              "&asset_id=" +
              encodeURIComponent(asset.id),
            textContent: `${t("original")}: ${asset.name}`,
            download: "",
          }),
          el("br"),
        );
      return value;
    });
  }
  function correction(kind, row) {
    const form = el("form", {
      className: "supplier-section",
      "data-draft": "tracked",
    });
    const allowed =
      kind === "quote"
        ? [
            "product_id",
            "amount",
            "currency",
            "unit",
            "quoted_at",
            "valid_until",
            "tax",
            "freight",
            "lead_time",
            "payment",
          ]
        : kind === "supplier"
          ? ["name"]
          : ["name", "model"];
    form.append(
      el("h3", { key: "correct" }),
      field(
        "field",
        select(
          "field",
          allowed.map((k) => [k, "field." + k]),
        ),
      ),
      field("value", input("value")),
      field("reason", Object.assign(input("reason"), { required: true })),
      el("button", { type: "submit", key: "saveCorrection" }),
    );
    form.addEventListener("submit", (e) => {
      e.preventDefault();
      const f = keys(form),
        ack = captureDraftSave(form);
      void action(async () => {
        const value = await api.suppliers.correct({
          ...scope(),
          request_id: crypto.randomUUID(),
          kind,
          identity: row.id,
          expected_sha256: row.version,
          fields: { [f.field]: f.value },
          reason: f.reason,
        });
        ack();
        correctionHost.replaceChildren();
        return value;
      });
    });
    const value = form.elements.value;
    const productSelect = select(
      "productChoice",
      data.records.product
        .filter((p) => p.active !== false && p.supplier_id === row.supplier_id)
        .map((p) => [p.id]),
    );
    for (const option of productSelect.options) {
      const product = data.records.product.find((p) => p.id === option.value);
      option.textContent = `${product.name} · ${product.model || t("unknown")}`;
    }
    productSelect.dataset.draft = "ignore";
    form.append(productSelect);
    const toggle = () => {
      productSelect.hidden = form.elements.field.value !== "product_id";
      value.hidden = !productSelect.hidden;
      if (!productSelect.hidden) value.value = productSelect.value;
    };
    form.elements.field.addEventListener("change", toggle);
    productSelect.addEventListener("change", () => {
      value.value = productSelect.value;
      value.dispatchEvent(new window.Event("input", { bubbles: true }));
    });
    toggle();
    correctionHost.replaceChildren(form);
    rememberDraft(form);
  }
  function quoteView(q, normalized = false) {
    return el(
      "article",
      { className: "supplier-section" },
      el("h3", {
        textContent: `${q.supplier?.name || q.supplier_name || ""} · ${q.product?.name || q.product_name || ""} ${q.product?.model || q.model || ""}`,
      }),
      el("p", {
        textContent: `${normalized ? q.normalized_price : (q.amount ?? t("unknown"))} ${q.currency || ""}/${normalized ? q.basis.unit : q.unit || t("unknown")}`,
      }),
      el("p", {
        textContent: `${t("date")}: ${q.quoted_at || t("unknown")} · ${t("until")}: ${q.valid_until || t("unknown")}`,
      }),
      el("p", {
        textContent: `${t("tax")}: ${t(q.tax || "unknown")} · ${t("freight")}: ${t(q.freight || "unknown")}`,
      }),
      el("p", { textContent: q.evidence_quote || q.evidence?.quote || "" }),
    );
  }
  function render() {
    if (!initialized) return;
    for (const b of root.querySelectorAll("[data-supplier-tab]"))
      b.setAttribute("aria-selected", String(b.dataset.supplierTab === tab));
    manualForm.hidden = tab !== "manual";
    policyForm.hidden = tab !== "settings";
    queryForm.hidden = tab !== "offers";
    records.hidden = ["manual", "settings"].includes(tab);
    if (preview) {
      policyForm.querySelector(".supplier-preview").textContent =
        Object.entries(preview.counts)
          .map(([k, v]) => `${k}: ${v}`)
          .join(" · ");
    }
    policyForm.querySelectorAll("button")[1].disabled = !preview;
    if (!data) {
      records.replaceChildren(el("p", { key: "empty" }));
      return;
    }
    if (tab === "suppliers")
      records.replaceChildren(
        ...data.records.supplier
          .filter((s) => s.active !== false)
          .map((s) =>
            section(
              "suppliers",
              el("h3", { textContent: s.name }),
              el("p", {
                textContent: data.records.contact
                  .filter((c) => c.supplier_id === s.id && c.active !== false)
                  .map(
                    (c) =>
                      `${(c.names || []).join(" / ")} ${c.phone_raw || c.phone || ""}`,
                  )
                  .join(" · "),
              }),
              el("p", { textContent: (s.categories || []).join(" · ") }),
              el("p", { key: s.legacy ? "legacy" : "retained" }),
              button(
                "export",
                () =>
                  void action(async () => {
                    const v = await api.suppliers.markdown(
                      clientSelect.value,
                      vaultSelect.value,
                      s.id,
                    );
                    details.hidden = false;
                    details.textContent = v.markdown;
                    return v;
                  }),
              ),
              button("correct", () => correction("supplier", s)),
            ),
          ),
      );
    if (tab === "offers") {
      const items =
        result?.items ??
        data.records.quote
          .filter((q) => q.active !== false)
          .map((q) => ({
            ...q,
            product: data.records.product.find((p) => p.id === q.product_id),
            supplier: data.records.supplier.find((s) => s.id === q.supplier_id),
          }));
      const chosen = new Set();
      const compare = button(
        "compare",
        () =>
          void action(async () => {
            const v = await api.suppliers.compare({
              ...scope(),
              quote_ids: [...chosen],
            });
            details.hidden = false;
            details.replaceChildren(
              el("h2", { key: "comparison" }),
              el("p", { key: "comparisonHint" }),
              ...v.groups.map((group) =>
                el(
                  "section",
                  { className: "supplier-section" },
                  el("h3", {
                    key: group.comparable ? "sameBasis" : "differentBasis",
                  }),
                  ...group.items.map((q) => quoteView(q, true)),
                ),
              ),
            );
            if (v.unresolved_quote_ids.length)
              details.append(el("p", { key: "unresolvedComparison" }));
            return v;
          }),
      );
      compare.disabled = true;
      const list = items.map((q) => {
        const check = el("input", {
          type: "checkbox",
          onchange: (e) => {
            e.target.checked ? chosen.add(q.id) : chosen.delete(q.id);
            compare.disabled = chosen.size < 2;
          },
        });
        check.setAttribute("aria-label", q.id);
        return el(
          "article",
          { className: "supplier-section" },
          check,
          el("h3", {
            textContent: `${q.supplier?.name || q.supplier_id || t("unknown")} · ${q.product?.name || t("unknown")}`,
          }),
          el("p", {
            textContent: `${q.amount ?? t("unknown")} ${q.currency || ""}/${q.unit || t("unknown")} · ${q.product?.model || ""}`,
          }),
          el("p", {
            textContent: `${t("date")}: ${q.quoted_at || t("unknown")} · ${t(q.legacy ? "legacy" : "retained")}`,
          }),
          button("source", () => showEvidence(q.source_id)),
          button("correct", () => correction("quote", q)),
        );
      });
      const previous = button("previous", () => {
        offset = Math.max(0, offset - 25);
        void search();
      });
      previous.disabled = !result || !offset;
      const next = button("next", () => {
        offset += 25;
        void search();
      });
      next.disabled = !result?.next_offset;
      records.replaceChildren(
        el("p", {
          textContent: result
            ? `${result.total} · ${result.coverage?.scope || ""}`
            : "",
        }),
        compare,
        ...list,
        previous,
        next,
        section(
          "catalog",
          ...data.records.product
            .filter((p) => p.active !== false)
            .map((p) =>
              el(
                "article",
                {},
                el("h3", {
                  textContent: `${p.name} · ${p.model || t("unknown")}`,
                }),
                ...Object.entries(p.specs || {}).map(([key, value]) =>
                  el("p", {
                    textContent: `${key}: ${Array.isArray(value) ? value.join(" / ") : value}`,
                  }),
                ),
                button("source", () => showEvidence(p.source_id)),
                button("correct", () => correction("product", p)),
              ),
            ),
        ),
      );
    }
    if (tab === "issues") {
      const rows = data.records.issue
        .filter((i) => i.active !== false && i.state === "open")
        .map((i) =>
          el(
            "article",
            { className: "supplier-section" },
            el("p", { textContent: t(i.reason) }),
            i.source_id
              ? button("source", () => showEvidence(i.source_id))
              : null,
          ),
        );
      for (const job of data.records.job) {
        const card = el(
          "article",
          { className: "supplier-section" },
          el("h3", { textContent: job.source_title }),
          el("p", {
            textContent: `${t(job.state)} ${job.error ? t(job.error) : ""}`,
          }),
          button("source", () => showEvidence(job.source_id)),
          ...(job.offers || []).map((q) => quoteView(q)),
        );
        for (const [state, op, key] of [
          ["needs_review", "approve", "approve"],
          ["failed", "resume", "resume"],
        ])
          if (job.state === state)
            card.append(
              button(
                key,
                () =>
                  void action(() =>
                    api.suppliers.job(job.id, op, {
                      ...scope(),
                      expected_sha256: job.version,
                    }),
                  ),
              ),
            );
        if (!["complete", "cancelled"].includes(job.state))
          card.append(
            button(
              "cancel",
              () =>
                void action(() =>
                  api.suppliers.job(job.id, "cancel", {
                    ...scope(),
                    expected_sha256: job.version,
                  }),
                ),
            ),
          );
        rows.push(card);
      }
      rows.push(
        button(
          "classification",
          () =>
            void action(async () => {
              const v = await api.suppliers.mocs(scope());
              details.hidden = false;
              details.replaceChildren(
                el("p", { key: "noWrite" }),
                ...v.proposals.map((proposal) => {
                  const product = data.records.product.find(
                    (p) => p.id === proposal.product_id,
                  );
                  return el(
                    "section",
                    { className: "supplier-section" },
                    el("h3", { textContent: product?.name || t("unknown") }),
                    el("p", { textContent: product?.model || "" }),
                    el("p", {
                      textContent: `${t("category")}: ${proposal.categories.join(" · ") || t("unknown")}`,
                    }),
                  );
                }),
                el("h3", { key: "existingMocs" }),
                ...v.existing_moc_notes.map((m) =>
                  el("p", { textContent: `${m.title} · ${m.path}` }),
                ),
                el("p", { key: "mocNext" }),
              );
              return v;
            }),
        ),
      );
      records.replaceChildren(...rows);
    }
    if (!records.childNodes.length) records.append(el("p", { key: "empty" }));
  }
  async function load() {
    if (!clientSelect.value || !vaultSelect.value) return;
    const mine = generation;
    const value = await api.suppliers.list(
      clientSelect.value,
      vaultSelect.value,
      controller?.signal,
    );
    if (mine !== generation) return;
    if (data && JSON.stringify(data.records) !== JSON.stringify(value.records))
      result = null;
    data = value;
    const settings = data.profile.settings;
    if (!hasUnsavedChanges(policyForm)) {
      policyForm.elements.enabled.checked = settings.enabled;
      policyForm.elements.cloud.checked = settings.cloud_allowed;
      policyForm.elements.mode.value = settings.review_mode;
      policyForm.elements.model.value = settings.model_configuration_id;
      rememberDraft(policyForm);
    }
    render();
  }
  async function refresh() {
    if (!initialized) initialize();
    const mine = ++generation;
    controller?.abort();
    controller = new AbortController();
    const [clients, vaults, modelCatalog] = await Promise.all([
      api.clients(),
      api.vaults(),
      api.modelsApi.catalog(),
    ]);
    if (mine !== generation) return;
    const selectedModel = policyForm.elements.model.value;
    policyForm.elements.model.replaceChildren(
      el("option", { value: "app-default", key: "defaultModel" }),
      ...modelCatalog.models.map((m) =>
        el("option", {
          value: m.id,
          textContent: `${modelCatalog.credentials.find((c) => c.id === m.credential_id)?.name || ""} · ${m.model}`,
          disabled: !m.active,
        }),
      ),
    );
    if (
      [...policyForm.elements.model.options].some(
        (o) => o.value === selectedModel,
      )
    )
      policyForm.elements.model.value = selectedModel;
    const active = clients.filter(
      (c) => c.active && c.grants.some((g) => g.can_query),
    );
    const previousClient = clientSelect.value;
    clientSelect.replaceChildren(
      ...active.map((c) => el("option", { value: c.id, textContent: c.name })),
    );
    if (active.some((c) => c.id === previousClient))
      clientSelect.value = previousClient;
    const client = active.find((c) => c.id === clientSelect.value);
    const allowed = vaults.filter(
      (v) =>
        v.status === "active" &&
        !["trashed", "detached"].includes(v.attachment_state) &&
        client?.grants.some((g) => g.vault_id === v.id && g.can_query),
    );
    const previousVault = vaultSelect.value;
    vaultSelect.replaceChildren(
      ...allowed.map((v) => el("option", { value: v.id, textContent: v.name })),
    );
    if (allowed.some((v) => v.id === previousVault))
      vaultSelect.value = previousVault;
    scopeValue = scope();
    data = null;
    await load();
    render();
    schedulePoll();
  }
  function schedulePoll() {
    clearTimeout(poll);
    if (!initialized) return;
    poll = setTimeout(
      async () => {
        if (!initialized) return;
        try {
          if (!document.hidden) await load();
        } catch (e) {
          if (e.name !== "AbortError") say(t("error"), true);
        }
        schedulePoll();
      },
      data?.records.job.some((j) => ["queued", "running"].includes(j.state))
        ? 3000
        : 15000,
    );
  }
  function clear() {
    clearTimeout(poll);
    generation++;
    controller?.abort();
    initialized = false;
    data = null;
    preview = null;
    result = null;
    root.replaceChildren();
  }
  return {
    refresh,
    clear,
    dispose() {
      clear();
      textLocale();
    },
    navigate,
  };
}
