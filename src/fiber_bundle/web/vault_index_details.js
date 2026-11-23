// Classic index detail destination. Opening it reads state and never starts work.
import { el } from "./ui.js";
import { control } from "./record_ui.js";
import { phrase, setText, uiText } from "./i18n/index.js";
import {
  indexDependencyText,
  indexDependencyTitle,
  indexProblem,
  noteIndexOutcome,
} from "./note_index_errors.js";
import { revealSection } from "./view_sections.js";

export async function openVaultIndexDetails(
  api,
  vaultId,
  current = () => true,
) {
  const row = [
    ...(document
      .getElementById("vault-list")
      ?.querySelectorAll(".vault-row[data-vault-id]") || []),
  ].find((node) => node.dataset.vaultId === vaultId);
  if (!row || !current()) return;
  let host = row.querySelector(".vault-index-details");
  if (host) {
    revealSection(host);
    host.scrollIntoView({ block: "nearest" });
    return;
  }
  host = el("section", null, "vault-index-details record-card");
  const status = el("p", uiText("正在读取索引状态；尚未确认当前进度。")),
    content = el("div"),
    who = el("select");
  setText(who, uiText("索引使用的身份"), "aria-label");
  who.dataset.draft = "ignore";
  status.setAttribute("role", "status");
  host.append(el("h3", uiText("笔记索引")), who, status, content);
  row.append(host);
  revealSection(host);
  host.scrollIntoView({ block: "nearest" });
  let generation = 0;
  const valid = (ticket) =>
    current() && host.isConnected && ticket === generation;
  async function read() {
    if (!who.value) return;
    const ticket = ++generation;
    setText(status, uiText("正在读取索引状态；尚未确认当前进度。"));
    try {
      const value = await api.noteIndexStatus(who.value, vaultId);
      if (!valid(ticket)) return;
      setText(
        status,
        phrase("知识库索引：{p0}", {
          p0:
            value.state === "blocked"
              ? indexDependencyTitle(value.sync?.dependency)
              : uiText(
                  {
                    ready: "已就绪",
                    pending: "等待更新",
                    building: "更新中",
                    blocked: "等待归档核验",
                    waiting: "等待文件或观察器就绪",
                    partial: "部分内容待核对",
                    failed: "更新失败，可单独重试",
                    stale: "需要更新",
                  }[value.state] || "状态待核对",
                ),
        }),
      );
      content.replaceChildren(
        el(
          "p",
          phrase("已索引 {p0} 篇 · 待更新 {p1} · 需核对 {p2}", {
            p0: value.notes ?? "—",
            p1: value.sync?.pending ?? "—",
            p2: value.sync?.needs_attention ?? "—",
          }),
        ),
      );
      if (value.sync?.error)
        content.append(el("p", indexProblem(value.sync.error), "task-impact"));
      if (value.sync?.dependency)
        content.append(
          el("p", indexDependencyText(value.sync.dependency), "task-impact"),
        );
      for (const issue of value.sync?.issues || [])
        content.append(
          el(
            "p",
            `${issue.path} · ${indexProblem(issue.reason)} · ${issue.reason}`,
            "muted",
          ),
        );
    } catch (error) {
      if (valid(ticket))
        setText(status, uiText("索引状态读取失败；保留上次观察，请手动刷新。"));
      throw error;
    }
  }
  const clients = await api.clients();
  if (!current() || !host.isConnected) return;
  for (const client of clients.filter(
    (value) =>
      value.active &&
      value.grants?.some(
        (grant) => grant.vault_id === vaultId && grant.can_query,
      ),
  )) {
    const option = el("option", client.name);
    option.value = client.id;
    who.append(option);
  }
  who.addEventListener("change", () => void read().catch(() => {}));
  const update = control("更新此知识库索引", async () => {
    const ticket = ++generation,
      result = await api.updateNoteIndex({
        client_id: who.value,
        vault_id: vaultId,
      });
    if (!valid(ticket)) return;
    setText(status, noteIndexOutcome(result));
    if (["ready", "partial"].includes(result.state)) await read();
  });
  update.disabled = !who.value;
  update.dataset.policyDisabled = String(!who.value);
  host.append(control("刷新索引状态", read), update);
  if (who.value) await read();
  else setText(status, uiText("此身份没有可查询知识库。"));
}
