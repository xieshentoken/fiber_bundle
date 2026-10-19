// Shared card/row presentation. Callers own scope, actions and refresh state.
import { el } from "./ui.js";
import { icon } from "./icons.js";
import { readState, shortTime } from "./home_status.js";
import { joinText, phrase, setText, uiText } from "./i18n/index.js";
export function jump(text, page, className = "") {
  const button = el("button", uiText(text), className);
  button.type = "button";
  button.dataset.pageTarget = page;
  return button;
}
export function link(text, page) {
  const button = jump(null, page, "quiet ui-link");
  button.append(el("span", uiText(text)), icon("chevron"));
  return button;
}
export function card(title, id, linkText, page) {
  const node = el("section", null, "ui-card");
  const head = el("div", null, "ui-card-head"),
    heading = el("h2", uiText(title));
  heading.id = id;
  node.setAttribute("aria-labelledby", id);
  head.append(heading);
  if (linkText) head.append(link(linkText, page));
  node.append(head);
  return { node, head };
}
// Read time for a card; an error keeps the last good time and says what failed.
export function stampText(record) {
  if (!record) return "尚未读取";
  if (record.error)
    return joinText([
      uiText(readState(record).text),
      record.lastSuccess
        ? joinText([
            " · ",
            phrase("最近读取 {p0}", { p0: shortTime(record.lastSuccess) }),
          ])
        : "",
    ]);
  return "最近读取 " + shortTime(record.lastSuccess);
}
export function stampLine(record) {
  const line = el("p", uiText(stampText(record)), "ui-stamp");
  if (record?.error) line.dataset.tone = "error";
  return line;
}
export function pill(text, tone) {
  const node = el("span", uiText(text), "status-badge " + (tone || ""));
  return node;
}
const kindIcons = {
  videos: "media",
  batch: "batch",
  documents: "document",
  media: "media",
  articles: "articles",
  vault: "vault",
  imports: "import",
  expert: "expert",
  automatic: "automatic",
  channel: "channel",
  import: "import",
  article: "articles",
};
export function row(item, detail) {
  const button = jump(null, item.page, "ui-row");
  if (item.id)
    Object.assign(button.dataset, {
      taskId: item.id,
      taskKind: item.source || item.kind,
    });
  if (item.vault_id)
    Object.assign(button.dataset, {
      clientId: item.client_id || "",
      vaultId: item.vault_id,
    });
  const text = el("span", null, "ui-row-text");
  text.append(
    el(
      "strong",
      item.systemTitle
        ? joinText([
            item.title.startsWith("飞书")
              ? uiText("飞书")
              : item.title.startsWith("微信")
                ? uiText("微信")
                : uiText("消息"),
            uiText(item.title.replace(/^(飞书|微信|消息)/, "")),
          ])
        : item.title,
    ),
    el(
      "small",
      item.detailParts
        ? joinText(
            item.detailParts.map((part) =>
              part.ui ? uiText(part.text) : part.text,
            ),
            " · ",
          )
        : uiText(detail),
    ),
  );
  setText(
    button,
    joinText(
      [
        item.title,
        detail && uiText(detail),
        item.status && uiText(item.status),
      ].filter(Boolean),
      " · ",
    ),
    "title",
  );
  button.append(
    icon(kindIcons[item.source || item.kind] || "worklist"),
    text,
    pill(item.status, item.tone),
    icon("chevron"),
  );
  return button;
}
