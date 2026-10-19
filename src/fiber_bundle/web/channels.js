import { channelPurpose } from "./channel_services.js";
import { phrase, setText, uiText } from "./i18n/index.js";
// Configuration editing and bounded observation; feature cards retain their own drafts.
import { $, el, action } from "./ui.js";
import { control, showJSON, reconcileChildren } from "./record_ui.js";
import { captureDraftSave, rememberDraft } from "./unsaved_changes.js";
import {
  configurationIssues,
  showChannelIssues,
  channelIssuesText,
} from "./channel_validation.js";
import { createChannelCard } from "./channel_card.js";
import { renderChannelOverview } from "./channel_status.js";

export function createChannelsView(api) {
  const form = $("channel-form"),
    list = $("channel-list"),
    summary = $("channel-overview"),
    archive = $("channel-archive"),
    disclosure = $("channel-configuration");
  let current = null,
    epoch = 0,
    timer,
    expiry,
    deadline = 0,
    snapshot = null,
    generation = 0,
    archiveStamp = "";
  const groups = new Map(
    ["query", "intake", "both"].map((purpose) => {
      const root = el("section"),
        body = el("div", null, "rows"),
        title = el(
          "h3",
          uiText(
            {
              query: "知识库查询入口",
              intake: "笔记保存入口",
              both: "兼用入口（现有配置）",
            }[purpose],
          ),
        );
      root.append(title, body);
      return [purpose, { root, body }];
    }),
  );
  const cards = new Map(),
    field = (name) => $("channel-" + name),
    visible = () => !$("channels-panel").hidden && !document.hidden;
  function layout() {
    const feishu = field("platform").value === "feishu";
    const purpose = field("purpose").value;
    field("purpose").querySelector('[value="intake"]').disabled = !feishu;
    if (!feishu && purpose === "intake") field("purpose").value = "query";
    if (field("purpose").value) {
      field("add").checked = field("purpose").value === "intake";
      field("add").disabled = true;
      if (field("purpose").value === "query")
        field("expert-enabled").checked = false;
      if (field("purpose").value === "intake")
        field("answer-mode").value = "evidence";
    } else field("add").disabled = false;
    field("expert-enabled").disabled = field("purpose").value === "query";
    field("answer-mode").disabled = field("purpose").value === "intake";
    field("feishu-fields").hidden = !feishu;
    field("app-id").required = feishu;
    field("expert-media-field").hidden = false;
    field("model-limit").disabled = field("answer-mode").value === "evidence";
    field("cloud-hint").hidden = field("answer-mode").value !== "local_cloud";
    field("natural-questions").disabled =
      field("answer-mode").value !== "model";
  }
  field("purpose").addEventListener("change", layout);
  field("platform").addEventListener("change", layout);
  field("answer-mode").addEventListener("change", layout);
  layout();
  function settings() {
    const value = {
      policy_version: 2,
      name: field("name").value.trim(),
      ...(field("purpose").value ? { purpose: field("purpose").value } : {}),
      platform: field("platform").value,
      app_id:
        field("platform").value === "feishu"
          ? field("app-id").value.trim()
          : null,
      client_id: field("client").value,
      vault_id: field("vault").value,
      add: field("add").checked,
      original: field("original").checked,
      answer_mode: field("answer-mode").value,
      natural_questions:
        field("answer-mode").value === "model" &&
        field("natural-questions").checked,
      configuration_sha256: field("policy").value || null,
      runtime_manifest: field("manifest").value.trim(),
      query_limit: 0,
      model_limit: 0,
      outbound_limit: 0,
      download_limit: 0,
      expert_enabled: field("expert-enabled").checked,
      expert_limit: 0,
      expert_receipt_limit: 0,
    };
    if (
      field("expert-media").value !== "unknown" ||
      Object.hasOwn(current?.settings || {}, "expert_media")
    )
      value.expert_media = field("expert-media").value;
    return value;
  }
  for (const name of [
    "query-limit",
    "outbound-limit",
    "model-limit",
    "download-limit",
    "expert-limit",
    "expert-receipt-limit",
  ]) {
    field(name).closest("label").hidden = true;
    field(name).required = false;
  }
  form.append(
    el(
      "p",
      uiText(
        "查询、提取、归档、保存与通知不设次数额度；模型调用统一使用 API Key 消耗预算，已有用量和去重记录保留。",
      ),
    ),
  );
  const validation = el("div", null, "error");
  validation.id = "channel-validation";
  validation.hidden = true;
  form.append(validation);
  function validate() {
    const issues = configurationIssues(settings());
    showChannelIssues(validation, issues, field);
    return issues;
  }
  form.addEventListener("input", () => {
    if (!validation.hidden) validate();
  });
  form.addEventListener("change", () => {
    if (!validation.hidden) validate();
  });
  form.addEventListener("submit", (event) => event.preventDefault());
  action(field("save"), async (notify) => {
    if (!form.reportValidity()) return;
    const issues = validate();
    if (issues.length) {
      form.querySelector('[aria-invalid="true"]')?.focus();
      notify("无法保存：" + channelIssuesText(issues), true);
      return;
    }
    const acknowledge = captureDraftSave(form),
      version = generation,
      selected = current;
    let result;
    try {
      result = await api.channelSave({
        id: selected?.id || null,
        expected_revision: selected?.revision || null,
        settings: settings(),
        credential_file: field("credential").value.trim() || null,
        app_secret: field("secret").value || null,
      });
    } catch (error) {
      if (version === generation && error.issues?.length)
        showChannelIssues(validation, error.issues, field);
      throw error;
    }
    if (version !== generation) return;
    field("secret").value = "";
    field("credential").value = "";
    acknowledge();
    current = result;
    await refresh();
    notify("配置与所选权限已保存；请完成本人绑定，再启动入口。");
  });
  action(field("new"), async () => {
    current = null;
    form.reset();
    field("purpose").querySelector('[value=""]').hidden = true;
    layout();
    disclosure.open = true;
    rememberDraft(form);
    field("name").focus();
  });
  action(field("refresh"), refresh);
  function edit(channel) {
    current = channel;
    form.reset();
    field("purpose").value = channel.settings.purpose || "";
    field("purpose").querySelector('[value=""]').hidden = Boolean(
      channel.settings.purpose,
    );
    for (const [key, value] of Object.entries(channel.settings)) {
      const map = {
        client_id: "client",
        vault_id: "vault",
        app_id: "app-id",
        answer_mode: "answer-mode",
        natural_questions: "natural-questions",
        configuration_sha256: "policy",
        runtime_manifest: "manifest",
        query_limit: "query-limit",
        model_limit: "model-limit",
        outbound_limit: "outbound-limit",
        download_limit: "download-limit",
        expert_enabled: "expert-enabled",
        expert_media: "expert-media",
        expert_limit: "expert-limit",
        expert_receipt_limit: "expert-receipt-limit",
      };
      const node = field(map[key] || key);
      if (node) {
        if (node.type === "checkbox") node.checked = value;
        else node.value = value ?? "";
      }
    }
    field("secret").value = "";
    field("credential").value = "";
    layout();
    disclosure.open = true;
    rememberDraft(form);
    form.scrollIntoView({ block: "start" });
    field("name").focus();
  }
  function render(overview, stale = false) {
    snapshot = overview;
    renderChannelOverview(summary, overview, stale);
    const archived = overview.removed_channels || [],
      stamp = JSON.stringify(archived);
    if (stamp !== archiveStamp) {
      archiveStamp = stamp;
      archive.replaceChildren();
      archive.hidden = !archived.length;
      archive.append(
        el(
          "summary",
          phrase("已移除连接与审计记录（{p0}）", { p0: archived.length }),
        ),
      );
      for (const item of archived) {
        const row = el("div", null, "record-card");
        row.append(
          el(
            "p",
            phrase("{p0} · 已移除；笔记、额度与执行审计保留。", {
              p0: item.name,
            }),
          ),
        );
        if (item.origin === "gui")
          row.append(
            control("查看保留的执行记录", async () => {
              const events = await api.channelEvents(item.id);
              if (row.isConnected) showJSON(row, events, "执行记录");
            }),
          );
        else
          row.append(
            el("p", uiText("既有入口的消息与批次审计保留在原账本中。")),
          );
        archive.append(row);
      }
    }
    const nodes = overview.channels.map((entry) => {
      if (!cards.has(entry.id)) {
        const version = generation;
        cards.set(
          entry.id,
          createChannelCard(
            api,
            entry,
            async () => {
              if (version === generation && cards.has(entry.id) && visible())
                await refresh();
            },
            edit,
          ),
        );
      }
      const card = cards.get(entry.id);
      card.update(entry, stale);
      return card.root;
    });
    if (nodes.length) {
      for (const [purpose, group] of groups) {
        const selected = overview.channels
          .map((entry, index) => ({ entry, node: nodes[index] }))
          .filter(({ entry }) => channelPurpose(entry) === purpose)
          .map(({ node }) => node);
        reconcileChildren(group.body, selected);
        group.root.hidden = !selected.length;
      }
      reconcileChildren(
        list,
        [...groups.values()].map((group) => group.root),
      );
    } else
      reconcileChildren(list, [
        el("p", uiText("尚无消息入口，可新增飞书或微信连接。")),
      ]);
    const active = new Set(overview.channels.map((entry) => entry.id));
    for (const id of cards.keys())
      if (!active.has(id)) {
        cards.get(id).dispose();
        cards.delete(id);
      }
  }
  function stale() {
    clearTimeout(expiry);
    if (snapshot) render(snapshot, true);
  }
  function schedule(version) {
    clearTimeout(timer);
    clearTimeout(expiry);
    expiry = setTimeout(() => {
      if (version === epoch) stale();
    }, 6000);
    if (visible() && Date.now() < deadline)
      timer = setTimeout(() => observe(version), 2000);
  }
  async function observe(version) {
    if (version !== epoch || !visible() || Date.now() >= deadline) return;
    try {
      const overview = await api.channelOverview();
      if (version !== epoch || !visible()) return;
      render(overview);
      schedule(version);
    } catch {
      if (version !== epoch || !visible()) return;
      stale();
      if (Date.now() < deadline)
        timer = setTimeout(() => observe(version), 5000);
    }
  }
  async function refresh() {
    const version = ++epoch;
    clearTimeout(timer);
    deadline = Date.now() + 180000;
    try {
      const [overview, clients, vaults, policy] = await Promise.all([
        api.channelOverview(),
        api.clients(),
        api.vaults(),
        api.queryPolicy(),
      ]);
      if (version !== epoch) return;
      for (const [id, items] of [
        ["client", clients.filter((c) => c.active)],
        ["vault", vaults.filter((v) => v.status === "active")],
      ]) {
        const select = field(id),
          selected = select.value;
        select.replaceChildren(
          ...items.map((item) => {
            const option = el("option", item.name);
            option.value = item.id;
            return option;
          }),
        );
        if ([...select.options].some((o) => o.value === selected))
          select.value = selected;
      }
      if (!current) field("policy").value = policy.configuration_sha256 || "";
      setText(
        field("policy-info"),
        policy.configured
          ? phrase("使用“模型与查询”的配置 {p0}；本地检索后可显式云端整理。", {
              p0: policy.configuration_sha256.slice(0, 12),
            })
          : uiText("尚未配置回答模型，可先使用原文查询。"),
      );
      render(overview);
      schedule(version);
    } catch (error) {
      if (version === epoch) stale();
      throw error;
    }
  }
  document.addEventListener("visibilitychange", () => {
    if (!visible()) {
      clearTimeout(timer);
      stale();
    } else if (snapshot) refresh().catch(stale);
  });
  return {
    refresh,
    clear() {
      epoch++;
      generation++;
      clearTimeout(timer);
      clearTimeout(expiry);
      for (const card of cards.values()) card.dispose();
      cards.clear();
      current = snapshot = null;
      list.replaceChildren();
      summary.replaceChildren();
      archive.replaceChildren();
      archive.hidden = true;
      archiveStamp = "";
      form.reset();
      disclosure.open = false;
      layout();
    },
  };
}
