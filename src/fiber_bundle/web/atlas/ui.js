// Atlas presentation components. No network access and no business rules.
import { h, icon } from "./dom.js";
import { strings } from "./i18n.js";
import { errorText, toast } from "./feedback.js";

const t = strings({
  zh: {
    retry: "重试",
    copy: "复制",
    copied: "已复制",
    copyFailed: "无法访问剪贴板，请手动选择复制。",
    loading: "正在读取…",
    nothing: "暂无内容",
    loadFailed: "读取失败",
    required: "必填",
    optional: "可选",
  },
  en: {
    retry: "Retry",
    copy: "Copy",
    copied: "Copied",
    copyFailed: "Clipboard unavailable; select the text to copy.",
    loading: "Loading…",
    nothing: "Nothing here yet",
    loadFailed: "Couldn't load",
    required: "Required",
    optional: "Optional",
  },
});

// Status pill: tone is success | waiting | error | info | paused | idle. The dot shape
// differs per tone so state never relies on color alone.
export function badge(text, tone = "info", title) {
  return h(
    "span.a-badge",
    { dataset: { tone }, title },
    h("i.a-badge-dot"),
    text,
  );
}
export function card({ title, subtitle, actions, className, id }, ...children) {
  return h(
    "section.a-card",
    { class: className, id },
    title || actions
      ? h(
          "header.a-card-head",
          h(
            "div.a-card-titles",
            title ? h("h2.a-card-title", title) : null,
            subtitle ? h("p.a-card-subtitle", subtitle) : null,
          ),
          actions ? h("div.a-card-actions", actions) : null,
        )
      : null,
    ...children,
  );
}
export function empty(
  text = t("nothing"),
  { iconName = "inbox", action } = {},
) {
  return h(
    "div.a-empty",
    h("span.a-empty-icon", icon(iconName)),
    h("p", text),
    action || null,
  );
}
export function skeleton(lines = 3) {
  return h(
    "div.a-skeleton",
    { "aria-hidden": "true" },
    Array.from({ length: lines }, () => h("span")),
  );
}
export function loading(text = t("loading")) {
  return h(
    "div.a-loading",
    { role: "status" },
    h("span.a-spinner", { "aria-hidden": "true" }),
    text,
  );
}
export function failure(error, retry) {
  const { title, detail } = errorText(error);
  return h(
    "div.a-failure",
    { role: "alert" },
    icon("alert"),
    h(
      "div",
      h("strong", t("loadFailed")),
      h("p", title),
      detail ? h("p.a-muted", detail) : null,
    ),
    retry
      ? h(
          "button.a-button",
          { type: "button", onclick: retry },
          icon("refresh"),
          t("retry"),
        )
      : null,
  );
}
export function notice(text, tone = "info", ...extra) {
  return h(
    "div.a-notice",
    { dataset: { tone } },
    icon(tone === "error" || tone === "warning" ? "alert" : "info"),
    h("div", typeof text === "string" ? h("p", text) : text, ...extra),
  );
}
// Definition list from [label, value] pairs; null values are skipped.
export function facts(pairs, className = "") {
  return h(
    "dl.a-facts",
    { class: className },
    pairs
      .filter(
        (pair) =>
          pair && pair[1] !== null && pair[1] !== undefined && pair[1] !== "",
      )
      .map(([label, value]) => h("div", h("dt", label), h("dd", value))),
  );
}
export function copyable(text, { label, mono = true, display } = {}) {
  const button = h(
    "button.a-icon-button",
    { type: "button", "aria-label": label || t("copy"), title: t("copy") },
    icon("copy"),
  );
  button.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(text);
      toast(t("copied"), { tone: "success", timeout: 1800 });
    } catch {
      toast(t("copyFailed"), { tone: "warning" });
    }
  });
  return h(
    "span.a-copy",
    { title: text },
    h(mono ? "code" : "span", display ?? text),
    button,
  );
}
export function codeBlock(text) {
  const button = h(
    "button.a-button.is-small",
    { type: "button" },
    icon("copy"),
    t("copy"),
  );
  button.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(text);
      toast(t("copied"), { tone: "success", timeout: 1800 });
    } catch {
      toast(t("copyFailed"), { tone: "warning" });
    }
  });
  return h("div.a-code", h("pre", h("code", text)), button);
}
export function disclosure(summary, ...children) {
  return h(
    "details.a-disclosure",
    h("summary", summary),
    h("div.a-disclosure-body", ...children),
  );
}
// Segmented control: options [{ value, label, count }]; calls onChange(value).
export function segmented(options, value, onChange, label) {
  const group = h("div.a-segmented", {
    role: "radiogroup",
    "aria-label": label,
  });
  for (const option of options) {
    const button = h(
      "button",
      {
        type: "button",
        role: "radio",
        "aria-checked": String(option.value === value),
        dataset: { value: option.value },
      },
      option.icon ? icon(option.icon) : null,
      option.label,
      option.count !== undefined && option.count !== null
        ? h("b.a-count", String(option.count))
        : null,
    );
    button.addEventListener("click", () => {
      for (const other of group.children)
        other.setAttribute("aria-checked", String(other === button));
      onChange(option.value);
    });
    group.append(button);
  }
  return group;
}
export function chips(options, value, onChange, label) {
  const group = h("div.a-chips", { role: "group", "aria-label": label });
  for (const option of options) {
    const button = h(
      "button.a-chip",
      { type: "button", "aria-pressed": String(option.value === value) },
      option.label,
      option.count !== undefined ? h("b", String(option.count)) : null,
    );
    button.addEventListener("click", () => onChange(option.value));
    group.append(button);
  }
  return group;
}
export function tabs(items, active, onChange, label) {
  const list = h("div.a-tabs", { role: "tablist", "aria-label": label });
  for (const item of items) {
    const tab = h(
      "button.a-tab",
      {
        type: "button",
        role: "tab",
        "aria-selected": String(item.value === active),
      },
      item.label,
      item.count ? h("b.a-count", String(item.count)) : null,
    );
    tab.addEventListener("click", () => onChange(item.value));
    list.append(tab);
  }
  return list;
}
// Step indicator for wizards: steps [label], current index.
export function steps(labels, current) {
  return h(
    "ol.a-steps",
    labels.map((label, index) =>
      h(
        "li",
        {
          dataset: {
            state:
              index < current ? "done" : index === current ? "current" : "todo",
          },
          "aria-current": index === current ? "step" : null,
        },
        h(
          "span.a-step-mark",
          index < current ? icon("check") : String(index + 1),
        ),
        h("span", label),
      ),
    ),
  );
}

let fieldSequence = 0;
// Labelled form field. control is an input/select/textarea element.
export function field(label, control, { help, optional, className } = {}) {
  const id = control.id || `f${++fieldSequence}`;
  control.id = id;
  control.dataset.sessionKey = typeof label === "string" ? label : id;
  const helpNode = help ? h("p.a-help", { id: id + "-help" }, help) : null;
  if (helpNode) control.setAttribute("aria-describedby", helpNode.id);
  return h(
    "div.a-field",
    { class: className },
    h(
      "label",
      { for: id },
      label,
      optional ? h("span.a-optional", t("optional")) : null,
    ),
    control,
    helpNode,
  );
}
export function input(props = {}) {
  return h("input.a-input", { type: "text", ...props });
}
export function textarea(props = {}) {
  return h("textarea.a-input", { rows: 4, ...props });
}
// options: [{ value, label, disabled }] or [value, label] pairs.
export function select(options, value, props = {}) {
  const node = h("select.a-input", props);
  setOptions(node, options, value);
  return node;
}
export function setOptions(node, options, value) {
  node.replaceChildren(
    ...options.map((option) => {
      const [optionValue, label, disabled] = Array.isArray(option)
        ? option
        : [option.value, option.label, option.disabled];
      return h("option", { value: optionValue, disabled: !!disabled }, label);
    }),
  );
  if (value !== undefined && value !== null) node.value = value;
}
export function checkbox(label, checked = false, props = {}) {
  const box = h("input", { type: "checkbox", checked: !!checked, ...props });
  return { label: h("label.a-check", box, h("span", label)), input: box };
}
export function toggle(label, checked = false, props = {}) {
  const box = h("input", {
    type: "checkbox",
    role: "switch",
    checked: !!checked,
    ...props,
  });
  return {
    label: h(
      "label.a-switch",
      box,
      h("span.a-switch-track", h("i")),
      h("span", label),
    ),
    input: box,
  };
}
// Simple data table: columns [{ label, value: (row) => node|string, className }].
export function table(columns, rows, { caption, empty: emptyText } = {}) {
  if (!rows.length && emptyText) return empty(emptyText);
  return h(
    "div.a-table-wrap",
    h(
      "table.a-table",
      caption ? h("caption", caption) : null,
      h(
        "thead",
        h(
          "tr",
          columns.map((column) =>
            h("th", { scope: "col", class: column.className }, column.label),
          ),
        ),
      ),
      h(
        "tbody",
        rows.map((row) =>
          h(
            "tr",
            columns.map((column) =>
              h("td", { class: column.className }, column.value(row)),
            ),
          ),
        ),
      ),
    ),
  );
}
// Row for lists: { icon, title, detail, meta, status: [text, tone], onClick, actions }
export function listRow({
  iconName,
  title,
  detail,
  meta,
  status,
  onClick,
  actions,
  selected,
  dataset,
}) {
  const body = [
    iconName ? h("span.a-row-icon", icon(iconName)) : null,
    h(
      "span.a-row-main",
      h("span.a-row-title", title),
      detail ? h("span.a-row-detail", detail) : null,
    ),
    meta ? h("span.a-row-meta", meta) : null,
    status ? badge(status[0], status[1]) : null,
  ];
  if (onClick)
    return h(
      "div.a-row",
      { dataset, "aria-current": selected ? "true" : null },
      h(
        "button.a-row-button",
        { type: "button", onclick: onClick },
        ...body,
        h("span.a-row-chevron", icon("chevron")),
      ),
      actions ? h("span.a-row-actions", actions) : null,
    );
  return h(
    "div.a-row",
    { dataset },
    h("div.a-row-static", ...body),
    actions ? h("span.a-row-actions", actions) : null,
  );
}
export function stat(label, value, { hint, tone, onClick } = {}) {
  const content = [
    h("span.a-stat-label", label),
    h("span.a-stat-value", value),
    hint ? h("span.a-stat-hint", hint) : null,
  ];
  return onClick
    ? h(
        "button.a-stat",
        { type: "button", dataset: { tone }, onclick: onClick },
        content,
      )
    : h("div.a-stat", { dataset: { tone } }, content);
}
export function button(label, { variant, iconName, onClick, ...props } = {}) {
  return h(
    "button.a-button",
    {
      type: "button",
      class: variant ? `is-${variant}` : null,
      onclick: onClick,
      ...props,
    },
    iconName ? icon(iconName) : null,
    label,
  );
}
export function toolbar(...children) {
  return h("div.a-toolbar", ...children);
}
