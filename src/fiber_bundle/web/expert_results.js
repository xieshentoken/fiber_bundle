import { joinText, phrase, setText, uiText } from "./i18n/index.js";
// Expert content only. Strings remain text nodes; media is bound to explicit resource IDs.
import { el } from "./ui.js";
import { showJSON } from "./record_ui.js";

const mimes = new Set(["image/png", "image/jpeg", "image/webp", "image/gif"]);
const labels = {
  unknown: "图表能力未知 · 文字可用",
  text_only: "纯文本展示",
  fully_supported: "图表展示可用",
  partially_supported: "部分图表可用 · 文字保留",
};
function missing(item) {
  return (
    {
      unauthorized: "无图表读取权限",
      missing: "图表资源缺失",
      stale: "图表版本或关联已变化",
      unsupported:
        item.reason === "output_capability_unknown"
          ? "出口图表能力未知，保留文字"
          : "出口不支持该图表",
    }[item.status] || "图表读取或展示失败"
  );
}
export function reliableRows(item) {
  const rows = item.rows;
  return item.rows_verified === true &&
    Array.isArray(rows) &&
    rows.length >= 2 &&
    rows.length <= 200 &&
    rows.every(
      (row) =>
        Array.isArray(row) &&
        row.length === rows[0].length &&
        row.length > 0 &&
        row.length <= 30 &&
        row.every((cell) => ["string", "number"].includes(typeof cell)),
    )
    ? rows
    : null;
}
async function imageURL(item) {
  if (
    !mimes.has(item.media_type) ||
    typeof item.data_base64 !== "string" ||
    item.data_base64.length > 11184812 ||
    !/^[a-f0-9]{64}$/.test(item.sha256 || "")
  )
    throw new Error("image_invalid");
  const raw = Uint8Array.from(window.atob(item.data_base64), (c) =>
    c.charCodeAt(0),
  );
  if (!raw.length || raw.length > 8 * 1024 * 1024)
    throw new Error("image_capacity");
  const hash = new Uint8Array(
    await window.crypto.subtle.digest("SHA-256", raw),
  );
  if (
    Array.from(hash, (b) => b.toString(16).padStart(2, "0")).join("") !==
    item.sha256
  )
    throw new Error("image_changed");
  return "data:" + item.media_type + ";base64," + item.data_base64;
}
export function expertResults(packet, { read } = {}) {
  if (!Array.isArray(packet?.expert_blocks) || !packet.expert_blocks.length)
    return null;
  const root = el("section", null, "expert-results"),
    status = el("p", uiText("图表核对中"), "muted");
  root.append(status);
  const cache = new Map();
  let total = 0,
    available = 0,
    complete = 0;
  const capability =
    packet.presentation?.capability || (read ? "full" : "unknown");
  function update() {
    const state =
      capability === "unknown"
        ? "unknown"
        : capability === "text"
          ? "text_only"
          : available === total
            ? "fully_supported"
            : available
              ? "partially_supported"
              : "text_only";
    if (complete === total) {
      setText(status, uiText(labels[state]));
      root.dataset.presentation = state;
    }
  }
  for (const block of packet.expert_blocks) {
    const section = el("article", null, "expert-result");
    section.append(
      el("h4", uiText("专家 Draft · 审核待完成 · 知识未核实")),
      el("p", block.text),
      el("p", `${block.module_id} @ ${block.version}`, "muted"),
    );
    if (block.citation) section.append(el("p", block.citation));
    section.append(
      el(
        "p",
        phrase("证据 ID：{p0}", { p0: (block.evidence_ids || []).join(" · ") }),
      ),
      el(
        "p",
        phrase("对象 ID：{p0}", { p0: (block.object_ids || []).join(" · ") }),
      ),
    );
    if (block.behavior === "abstain")
      section.append(
        el(
          "p",
          uiText("文字证据不足，保持弃答；展示图表不代表事实或数值已核实。"),
        ),
      );
    for (const reference of block.media || []) {
      total++;
      const figure = el("figure", null, "expert-media"),
        placeholder = el("p", uiText("正在读取关联图表…"));
      const caption = el(
        "figcaption",
        joinText([
          "",
          reference.caption || "图表",
          " · ",
          reference.resource_id,
          "",
        ]),
      );
      figure.dataset.resourceId = reference.resource_id;
      figure.append(placeholder, caption);
      section.append(figure);
      const key = JSON.stringify([
        block.module_id,
        block.version,
        reference.resource_id,
      ]);
      if (!cache.has(key))
        cache.set(
          key,
          (async () => {
            let item = reference;
            if (read && !item.data_base64 && !reliableRows(item))
              item = {
                ...reference,
                ...(await read(
                  block.module_id,
                  block.version,
                  reference.resource_id,
                )),
              };
            if (
              read &&
              (item.module_id !== block.module_id ||
                item.version !== block.version ||
                item.resource_id !== reference.resource_id)
            )
              throw new Error("binding_changed");
            return item;
          })(),
        );
      void (async () => {
        try {
          const item = await cache.get(key);
          if (item.status !== "available") {
            setText(placeholder, uiText(missing(item)));
            figure.dataset.status = item.status;
            return;
          }
          const rows = reliableRows(item);
          if (rows) {
            const table = el("table");
            for (let i = 0; i < rows.length; i++) {
              const row = el("tr");
              for (const cell of rows[i])
                row.append(el(i ? "td" : "th", String(cell)));
              table.append(row);
            }
            placeholder.replaceWith(table);
          } else {
            const url = await imageURL(item);
            if (!root.isConnected) return;
            const image = el("img");
            image.alt = reference.caption || reference.resource_id;
            image.loading = "lazy";
            image.addEventListener(
              "error",
              () => {
                image.replaceWith(
                  el("p", uiText("图片无法展示，已有文字保留。")),
                );
                figure.dataset.status = "unsupported";
                available--;
                update();
              },
              { once: true },
            );
            image.src = url;
            placeholder.replaceWith(image);
          }
          figure.dataset.status = "available";
          available++;
        } catch {
          setText(
            placeholder,
            uiText("图表读取或内容校验失败，已有文字保留。"),
          );
          figure.dataset.status = "missing";
        } finally {
          complete++;
          update();
        }
      })();
    }
    root.append(section);
  }
  update();
  if (packet.query_result)
    showJSON(root, packet.query_result, "原查询结果与来源定位");
  return root;
}

export function downloadMarkdown(packet) {
  const raw = Uint8Array.from(
    window.atob(packet.markdown_archive_base64),
    (c) => c.charCodeAt(0),
  );
  const url = URL.createObjectURL(new Blob([raw], { type: "application/zip" }));
  const link = el("a", uiText("下载Markdown与关联图片"));
  link.href = url;
  link.download = "fiber-expert-answer.zip";
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
