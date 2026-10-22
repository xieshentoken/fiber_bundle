// Explicit supplier form components and safe DOM primitives. No filesystem or global app state.
import { captureDraftSave } from "./unsaved_changes.js";
import { t } from "./suppliers_labels.js";
export function el(tag, props = {}, ...children) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
    else if (k === "key") {
      n.dataset.supplierKey = v;
      n.textContent = t(v);
    } else if (k in n) n[k] = v;
    else n.setAttribute(k, v);
  }
  for (const c of children.flat())
    if (c !== undefined && c !== null)
      n.append(c instanceof Node ? c : String(c));
  return n;
}
export const field = (key, node) =>
  el("label", { className: "supplier-field" }, el("span", { key }), node);
export const input = (name, type = "text") =>
  el("input", { name, type, className: "a-input" });
export function select(name, items) {
  return el(
    "select",
    { name, className: "a-input" },
    ...items.map(([value, key]) =>
      el("option", { value, ...(key ? { key } : { textContent: value }) }),
    ),
  );
}
export const keys = (root) =>
  Object.fromEntries(
    [...root.querySelectorAll("[name]")].map((n) => [
      n.name,
      n.type === "checkbox" ? n.checked : n.value,
    ]),
  );
export const button = (key, click) =>
  el("button", { type: "button", key, onclick: click });

export function createManualForm({ api, scope, action }) {
  const form = el("form", {
    className: "supplier-section",
    "data-draft": "tracked",
  });
  let attempt = null;
  const grid = el("div", { className: "supplier-grid" });
  for (const [key, type] of [
    ["company", "text"],
    ["product", "text"],
    ["sku", "text"],
    ["amount", "text"],
    ["date", "date"],
    ["until", "date"],
  ]) {
    const n = input(key, type);
    if (["company", "product", "amount"].includes(key)) n.required = true;
    grid.append(field(key, n));
  }
  grid.append(
    field(
      "currency",
      select("currency", [
        ["CNY"],
        ["USD"],
        ["EUR"],
        ["JPY"],
        ["GBP"],
        ["HKD"],
      ]),
    ),
    field(
      "unit",
      select("unit", [
        ["kg"],
        ["100g"],
        ["g"],
        ["t"],
        ["piece"],
        ["set"],
        ["m"],
        ["m2"],
        ["L"],
      ]),
    ),
  );
  const advanced = el("details", {}, el("summary", { key: "details" }));
  const more = el("div", { className: "supplier-grid" });
  for (const key of ["tax", "freight"])
    more.append(
      field(
        key,
        select(key, [
          ["unknown", "unknown"],
          ["included", "included"],
          ["excluded", "excluded"],
        ]),
      ),
    );
  for (const key of [
    "minimum",
    "maximum",
    "quantityUnit",
    "lead",
    "payment",
    "contact",
    "phone",
  ])
    more.append(field(key, input(key)));
  advanced.append(more);
  const note = el("textarea", { name: "note", rows: 3 });
  form.append(el("h2", { key: "manual" }), grid, advanced, field("note", note));
  const save = el("button", { type: "submit", key: "saveQuote" });
  form.append(
    save,
    button("clear", () => {
      form.reset();
      captureDraftSave(form)();
    }),
  );
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const f = keys(form),
      bound = scope(),
      ack = captureDraftSave(form);
    const offer = {
      supplier_name: f.company,
      product_name: f.product,
      model: f.sku,
      amount: f.amount,
      currency: f.currency,
      unit: f.unit,
      quoted_at: f.date || null,
      valid_until: f.until || null,
      tax: f.tax,
      freight: f.freight,
      minimum_quantity: f.minimum || null,
      maximum_quantity: f.maximum || null,
      quantity_unit: f.quantityUnit,
      lead_time: f.lead,
      payment: f.payment,
      contact_name: f.contact,
      phone: f.phone,
      specs: {},
      evidence_quote: JSON.stringify(f),
    };
    const payload = JSON.stringify({ ...bound, offer });
    if (attempt?.payload !== payload)
      attempt = { payload, id: crypto.randomUUID() };
    void action(async () => {
      const v = await api.suppliers.manual({
        ...bound,
        request_id: attempt.id,
        offer,
      });
      ack();
      return v;
    }, save);
  });
  return form;
}
export function createPolicyForm({
  api,
  scope,
  action,
  getPreview,
  setPreview,
  getData,
}) {
  const form = el("form", {
    className: "supplier-section",
    "data-draft": "tracked",
  });
  form.append(el("h2", { key: "settings" }), el("p", { key: "previewHint" }));
  const previewButton = button(
    "preview",
    () =>
      void action(async () => {
        const preview = await api.suppliers.preview(scope());
        setPreview(preview);
        return preview;
      }, previewButton),
  );
  const importButton = button("import", () => {
    const preview = getPreview();
    if (!preview) return;
    void action(
      () =>
        api.suppliers.importLegacy({
          ...scope(),
          request_id: crypto.randomUUID(),
          expected_sha256: preview.snapshot_sha256,
        }),
      importButton,
    );
  });
  form.append(
    previewButton,
    importButton,
    el("div", { className: "supplier-preview" }),
  );
  for (const key of ["enabled", "cloud"])
    form.append(field(key, el("input", { type: "checkbox", name: key })));
  form.append(
    field(
      "mode",
      select("mode", [
        ["manual", "review"],
        ["automatic", "automatic"],
      ]),
    ),
    field("model", select("model", [["app-default", "defaultModel"]])),
  );
  const save = el("button", { type: "submit", key: "savePolicy" });
  form.append(save, el("p", { key: "newHint" }));
  const run = input("runId");
  run.dataset.draft = "ignore";
  form.append(field("runId", run));
  const enqueue = button(
    "enqueue",
    () =>
      void action(
        () => api.suppliers.document({ ...scope(), run_id: run.value }),
        enqueue,
      ),
  );
  form.append(enqueue);
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const f = keys(form),
      ack = captureDraftSave(form);
    void action(async () => {
      const value = await api.suppliers.policy({
        ...scope(),
        expected_revision: getData()?.profile.revision || 0,
        settings: {
          enabled: f.enabled,
          cloud_allowed: f.cloud,
          review_mode: f.mode,
          model_configuration_id: f.model,
        },
      });
      ack();
      return value;
    }, save);
  });
  return form;
}
export function createQueryForm(onSearch) {
  const form = el("form", { className: "supplier-section" });
  const grid = el("div", { className: "supplier-grid" });
  for (const key of ["keyword", "sku", "priceMax"])
    grid.append(field(key, input(key)));
  for (const key of ["after", "before", "validOn"])
    grid.append(field(key, input(key, "date")));
  grid.append(
    field(
      "currency",
      select("currency", [["", "unknown"], ["CNY"], ["USD"], ["EUR"]]),
    ),
    field(
      "unit",
      select("unit", [
        ["", "unknown"],
        ["kg"],
        ["100g"],
        ["g"],
        ["piece"],
        ["set"],
      ]),
    ),
    field(
      "tax",
      select("tax", [
        ["", "unknown"],
        ["included", "included"],
        ["excluded", "excluded"],
      ]),
    ),
  );
  for (const key of [
    "category",
    "specField",
    "specValue",
    "specMin",
    "specMax",
    "specUnit",
  ])
    grid.append(field(key, input(key)));
  const include = el("input", {
    type: "checkbox",
    name: "includeLegacy",
    checked: true,
  });
  form.append(
    grid,
    field("includeLegacy", include),
    el("button", { type: "submit", key: "search" }),
  );
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    onSearch();
  });
  return form;
}
