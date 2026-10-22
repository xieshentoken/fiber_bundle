// Shared relationship presentation and explicit query-scope editing; no network calls.
import { locale } from "./i18n/index.js";

const node = (tag, text = "") => {
  const result = document.createElement(tag);
  result.textContent = text;
  return result;
};
const en = () => locale() === "en";
const word = (zh, english) => (en() ? english : zh);

export function relationLines(entry) {
  const settings = entry.configuration?.settings || entry.settings || {};
  const scope = entry.query_scope;
  const intake = settings.purpose === "intake" || settings.add;
  const query = settings.purpose !== "intake";
  const rows = [];
  if (intake) {
    const target =
      entry.intake?.target_name ||
      scope?.vaults?.find((v) => v.vault_id === entry.vault_id)?.name ||
      entry.vault_id ||
      settings.vault_id;
    rows.push(`${word("独立入库", "Save only")} → ${target}`);
  }
  if (query && scope) {
    rows.push(
      `${word("默认查询", "Default query")} → ${scope.vaults.map((v) => (v.alias ? `${v.alias} · ${v.name}` : v.name)).join(" / ")}`,
    );
    if (!scope.vaults.some((v) => v.alias))
      rows.push(
        word(
          "尚未设置库别名；请在编辑配置中指定查询库和别名。",
          "No library aliases configured; edit the channel to assign query libraries and aliases.",
        ),
      );
    for (const item of scope.vaults) {
      if (item.alias)
        rows.push(
          `查询[${item.alias}]：${word("问题", "question")} → ${item.name}${item.available ? "" : word("（不可用）", " (unavailable)")}`,
        );
    }
    rows.push(
      word(
        "查询：问题 / 自然语言 → 全部查询库；单库选择只对本条消息生效。",
        "查询：question / natural questions → all selected libraries; a single-library selection applies to one message only.",
      ),
    );
  }
  return rows;
}

export function relationCard(entry) {
  const root = node("section");
  root.className = "channel-query-relations";
  root.setAttribute(
    "aria-label",
    word("入口与知识库关系", "Channel and library relationships"),
  );
  const list = node("ul");
  for (const line of relationLines(entry)) list.append(node("li", line));
  root.append(list);
  return root;
}

export function relationOverview(entries) {
  const root = node("section");
  root.className = "channel-query-relations";
  root.setAttribute(
    "aria-label",
    word("消息与知识库关系", "Message and library relationships"),
  );
  root.append(
    node("h3", word("消息与知识库关系", "Message and library relationships")),
  );
  const list = node("ul");
  for (const entry of entries) {
    const lines = relationLines(entry);
    if (lines.length)
      list.append(
        node(
          "li",
          `${entry.name} — ${lines[0]}${lines[1]?.startsWith(word("默认查询", "Default query")) ? `；${lines[1]}` : ""}`,
        ),
      );
  }
  if (!entries.length)
    list.append(
      node("li", word("尚未配置消息入口。", "No message channels configured.")),
    );
  root.append(
    list,
    node(
      "p",
      word(
        "入库按各入口目标独立保存；统一查询只读取明确选中的库。查询不会移动或复制笔记。",
        "Each saving channel has its own target. Queries read only explicitly selected libraries. Queries do not move or copy notes.",
      ),
    ),
  );
  return root;
}

export function createQueryScopeEditor(initial = {}) {
  const root = node("fieldset");
  root.className = "channel-query-scope";
  root.append(
    node(
      "legend",
      word("统一查询范围与命令", "Shared query scope and commands"),
    ),
  );
  const toggle = node("input");
  toggle.type = "checkbox";
  const label = node(
    "label",
    word(
      "配置查询库与别名，默认查询全部勾选库",
      "Configure library aliases; query all selected libraries by default",
    ),
  );
  label.prepend(toggle);
  const rows = node("div");
  rows.className = "channel-query-scope-rows";
  const explanation = node(
    "p",
    word(
      "勾选要查询的库，并设置唯一、固定的别名。查询：问题 → 全部；查询[别名]：问题 → 单库。查询本库已取消。",
      "Select libraries and assign unique stable aliases. 查询：question → all; 查询[alias]：question → one. 查询本库 is no longer supported.",
    ),
  );
  const rules = node(
    "p",
    word(
      "解除绑定后别名失效，新库不会自动继承。新增库只增加查询权限，不自动开放原件或入库权限。",
      "An unbound alias becomes invalid and is not inherited by a new library. Adding a library grants query access only.",
    ),
  );
  root.append(label, explanation, rows, rules);
  let original,
    usable = true;
  const controls = new Map();
  function layout() {
    root.hidden = !usable;
    rows.hidden = !toggle.checked;
    for (const row of controls.values()) {
      row.include.disabled = !usable || !toggle.checked;
      row.alias.disabled = !usable || !toggle.checked || !row.include.checked;
      row.alias.required = usable && toggle.checked && row.include.checked;
      const duplicate =
        [...controls.values()].filter(
          (r) =>
            r.include.checked &&
            r.alias.value.trim().toLowerCase() ===
              row.alias.value.trim().toLowerCase(),
        ).length > 1;
      const reserved = [
        "本库",
        "原库",
        "主库",
        "当前",
        "默认",
        "全部",
      ].includes(row.alias.value.trim());
      row.alias.setCustomValidity(
        row.alias.required && (duplicate || reserved)
          ? word(
              "别名必须唯一，且不能使用本库、默认或全部等范围词。",
              "Use a unique alias, not an ambiguous scope word.",
            )
          : "",
      );
    }
    toggle.setCustomValidity(
      usable &&
        toggle.checked &&
        ![...controls.values()].some((r) => r.include.checked)
        ? word("至少选择一个查询库。", "Select at least one query library.")
        : "",
    );
  }
  toggle.addEventListener("change", layout);
  function setVaults(vaults) {
    const ids = new Set(vaults.map((v) => v.id));
    for (const item of original || []) {
      if (!ids.has(item.vault_id))
        vaults = [
          ...vaults,
          {
            id: item.vault_id,
            name: `${item.alias} · ${word("未挂载", "not attached")}`,
          },
        ];
    }
    for (const v of vaults) {
      if (controls.has(v.id)) continue;
      const row = node("label");
      row.className = "channel-query-scope-row";
      const include = node("input"),
        alias = node("input");
      include.type = "checkbox";
      include.setAttribute(
        "aria-label",
        `${word("查询库", "Query library")} ${v.name}`,
      );
      alias.type = "text";
      alias.maxLength = 32;
      alias.pattern = "[A-Za-z0-9_\\-\\u3400-\\u9fff]{1,32}";
      alias.placeholder = word(
        "库别名，例如：个人",
        "Stable alias, e.g. personal",
      );
      alias.setAttribute(
        "aria-label",
        `${word("库别名", "Library alias")} ${v.name}`,
      );
      const prior = original?.find((s) => s.vault_id === v.id);
      include.checked = !!prior;
      alias.value = prior?.alias || "";
      include.addEventListener("change", layout);
      alias.addEventListener("input", layout);
      row.append(include, node("span", v.name), alias);
      rows.append(row);
      controls.set(v.id, { include, alias });
    }
    layout();
  }
  function reset(settings = {}) {
    original = settings.query_vaults;
    toggle.checked = !!original;
    for (const [id, row] of controls) {
      const previous = original?.find((v) => v.vault_id === id);
      row.include.checked = !!previous;
      row.alias.value = previous?.alias || "";
    }
    layout();
  }
  reset(initial);
  return {
    root,
    setVaults,
    reset,
    context(available, primary, originals) {
      usable = available;
      layout();
      if (originals) originals.disabled = usable && toggle.checked;
      if (primary) {
        primary.required = !(usable && toggle.checked);
        const host = primary.closest("label") || primary.closest(".a-field");
        host?.toggleAttribute("hidden", usable && toggle.checked);
        const caption =
          primary.closest("label")?.querySelector("span") ||
          host?.querySelector("label");
        if (caption)
          caption.textContent = usable
            ? word(
                "单库查询目标（未设置多库时）",
                "Single query target (without an explicit scope)",
              )
            : word("独立入库目标", "Independent saving target");
      }
    },
    settings() {
      if (!usable || !toggle.checked)
        return original ? { query_vaults: null } : {};
      return {
        query_vaults: [...controls.entries()]
          .filter(([, r]) => r.include.checked)
          .map(([id, r]) => ({ vault_id: id, alias: r.alias.value.trim() })),
      };
    },
  };
}
