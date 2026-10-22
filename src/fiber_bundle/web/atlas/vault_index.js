// Explicit, query-authorized index maintenance. Never sorts or republishes notes.
import { h, clear } from "./dom.js";
import { strings } from "./i18n.js";
import {
  button,
  card,
  failure,
  field,
  facts,
  loading,
  notice,
  select,
} from "./ui.js";

const t = strings({
  zh: {
    title: "笔记索引",
    update: "更新索引",
    check: "刷新状态",
    intro: "更新所选身份可访问的全文索引，不调用模型，不执行笔记整理。",
    identity: "索引使用的身份",
    needs: "需要具有此知识库查询权限的启用身份。",
    absent: "尚未建立",
    stale: "笔记已变化，需要更新",
    ready: "索引已是最新",
    building: "正在更新索引…",
    pending: "等待手动更新",
    failed: "上次更新失败",
    interrupted: "上次更新已中断",
    state: "当前状态",
    notes: "已索引笔记",
    built: "上次完成时间",
    location: "核对笔记位置",
    help: "若笔记移动、改名或缺失，请先到“整理与迁移 → 批量核对外部改动”核对，保留原登记后再更新。",
    done: "索引更新完成。",
    uncertain: "更新结果尚未确认。请刷新状态核对，不会自动重试。",
    observed: "后台正在更新，请稍后刷新状态核对。",
    detached: "此知识库未接入，不能更新索引。",
  },
  en: {
    title: "Note index",
    update: "Update index",
    check: "Refresh status",
    intro:
      "Updates the full-text index accessible to the selected identity. No model calls or note organizing.",
    identity: "Index identity",
    needs: "An active identity with query access to this vault is required.",
    absent: "Not built",
    stale: "Notes changed; update needed",
    ready: "Index is current",
    building: "Updating index…",
    pending: "Awaiting manual update",
    failed: "Last update failed",
    interrupted: "Last update interrupted",
    state: "Status",
    notes: "Indexed notes",
    built: "Last completed",
    location: "Reconcile note locations",
    help: "For moved, renamed or missing notes, reconcile external changes in Organize & migrate first, keeping the original registrations.",
    done: "Index updated.",
    uncertain:
      "Update outcome is unconfirmed. Refresh status to check; no automatic retry.",
    observed: "The backend is still updating. Refresh status again shortly.",
    detached: "This vault is disconnected; indexing is unavailable.",
  },
});

export function create(ctx, vault) {
  const status = h("div", { role: "status", "aria-live": "polite" });
  const message = h("div");
  const update = button(t("update"), {
    variant: "primary",
    iconName: "refresh",
  });
  const check = button(t("check"), { onClick: () => void read() });
  const who = select([], undefined, { dataset: { draft: "ignore" } });
  const node = card(
    { title: t("title"), subtitle: t("intro"), id: "vault-note-index" },
    field(t("identity"), who),
    status,
    message,
    h(
      "div.a-form-actions",
      update,
      check,
      button(t("location"), {
        onClick: () =>
          ctx.navigate("vaults", "detail", {
            vault: vault.id,
            tab: "organize",
          }),
      }),
    ),
    h("p.a-help", t("help")),
  );
  let alive = true,
    revision = 0,
    requestPending = false,
    reading = false;
  let value = null,
    timer = null,
    observeUntil = 0,
    postMessage = null;
  const valid = (mine) => alive && mine === revision;
  const running = () => requestPending || value?.update?.state === "building";
  function controls() {
    update.disabled = !who.value || !value || reading || running();
    who.disabled = requestPending;
    check.disabled = !who.value || reading || requestPending;
    update.textContent = requestPending ? t("building") : t("update");
  }
  function render() {
    const state = running() ? "building" : value?.state;
    clear(
      status,
      facts([
        [t("state"), t(state || "absent")],
        [
          t("notes"),
          Number.isInteger(value?.notes) ? String(value.notes) : "—",
        ],
        [
          t("built"),
          value?.built_at
            ? new Date(value.built_at * 1000).toLocaleString()
            : "—",
        ],
      ]),
    );
    const last = value?.update?.state;
    clear(
      message,
      postMessage,
      !running() && ["failed", "interrupted"].includes(last)
        ? notice(t(last), "warning")
        : null,
    );
    controls();
  }
  function schedule(mine) {
    clearTimeout(timer);
    if (!valid(mine) || !running()) return;
    if (Date.now() >= observeUntil) {
      clear(message, postMessage, notice(t("observed")));
      return;
    }
    timer = setTimeout(() => void read(), 2000);
  }
  async function read() {
    if (!alive || !who.value || reading) return;
    const mine = revision,
      client = who.value;
    reading = true;
    controls();
    try {
      const next = await ctx.api.noteIndexStatus(client, vault.id);
      if (!valid(mine)) return;
      value = next;
      if (running() && !observeUntil) observeUntil = Date.now() + 180000;
      render();
    } catch (error) {
      if (valid(mine)) {
        value = null;
        clear(
          status,
          failure(error, () => void read()),
        );
      }
    } finally {
      if (valid(mine)) {
        reading = false;
        controls();
        schedule(mine);
      }
    }
  }
  update.addEventListener("click", async () => {
    if (update.disabled || requestPending) return;
    const mine = revision,
      scope = { client_id: who.value, vault_id: vault.id };
    requestPending = true;
    postMessage = null;
    observeUntil = Date.now() + 180000;
    render();
    schedule(mine);
    try {
      await ctx.api.updateNoteIndex(scope);
      if (valid(mine)) postMessage = notice(t("done"), "success");
    } catch (error) {
      if (valid(mine))
        postMessage = h(
          "div",
          failure(error),
          error.status === 0 ? notice(t("uncertain"), "warning") : null,
        );
    } finally {
      if (valid(mine)) {
        requestPending = false;
        clearTimeout(timer);
        render();
        // Read the actual outcome, including after a timeout; never repeat POST.
        if (!reading) await read();
      }
    }
  });
  who.addEventListener("change", () => {
    revision++;
    clearTimeout(timer);
    reading = false;
    value = null;
    postMessage = null;
    observeUntil = 0;
    clear(status, loading());
    clear(message);
    controls();
    void read();
  });
  async function refresh() {
    const mine = ++revision;
    clearTimeout(timer);
    value = null;
    reading = false;
    controls();
    clear(status, loading());
    if (
      vault.status !== "active" ||
      (vault.attachment_state ?? "attached") !== "attached"
    ) {
      clear(status, notice(t("detached"), "warning"));
      return;
    }
    try {
      const clients = await ctx.store.clients();
      if (!valid(mine)) return;
      const eligible = clients.filter(
        (c) =>
          c.active &&
          !c.managed_connection_id &&
          c.grants.some((g) => g.vault_id === vault.id && g.can_query),
      );
      who.replaceChildren(
        ...eligible.map((c) => h("option", { value: c.id }, c.name)),
      );
      if (!eligible.length) {
        clear(status, notice(t("needs"), "warning"));
        controls();
        return;
      }
      await read();
    } catch (error) {
      if (valid(mine)) clear(status, failure(error, refresh));
    }
  }
  controls();
  return {
    node,
    refresh,
    dispose() {
      alive = false;
      revision++;
      clearTimeout(timer);
    },
  };
}
