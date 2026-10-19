import { phrase, setText, translate, uiText } from "./i18n/index.js";
import { confirmAction } from "./confirm_dialog.js";
import { knowledgePanel } from "./agent_knowledge.js";
import { accessPanel } from "./agent_access.js";
// GUI connection lifecycle. Storage/model work stays behind the management API.
import { $, el, captureMessage, setBusy } from "./ui.js";
import { control, showJSON } from "./record_ui.js";
import {
  captureDraftSave,
  rememberDraft,
  hasUnsavedChanges,
} from "./unsaved_changes.js";
import { connectionForm, readConfiguration } from "./agent_forms.js";
import { testForm, describeTest } from "./agent_test.js";
import { states, operationResult } from "./agent_results.js";
import { agentError } from "./agent_labels.js";
import { createSections } from "./view_sections.js";
import { agentLayout, focusAgentSection } from "./agent_layout.js";

export function createAgentConnectionsView(api) {
  const panel = $("clients-panel"),
    root = $("agent-connections"),
    ordinary = $("ordinary-clients");
  let rows = [],
    vaults = [],
    catalog = null,
    selected = null,
    form = null,
    epoch = 0,
    timer = null,
    active = null,
    loading = false,
    saving = false,
    submitting = false;
  const layout = agentLayout(
      root,
      () => choose(null),
      () => refresh(),
    ),
    { list, detail, result } = layout;
  const notify = (text, error = false) =>
    captureMessage("clients")(error ? agentError(text) : text, error);
  const loadingControls = new Map();
  const isRunning = () =>
    submitting || (active && ["queued", "running"].includes(active.state));
  function disable(button, value) {
    if (!button) return;
    button.dataset.policyDisabled = String(Boolean(value));
    button.disabled =
      Boolean(value) || button.getAttribute("aria-busy") === "true";
  }
  function updateControls() {
    if (loading) {
      for (const input of detail.querySelectorAll(
        "form input,form select,form textarea,form button",
      )) {
        if (!loadingControls.has(input))
          loadingControls.set(input, input.disabled);
        input.disabled = true;
      }
    } else {
      for (const [input, disabled] of loadingControls)
        if (input.isConnected) input.disabled = disabled;
      loadingControls.clear();
    }
    disable(
      form?.querySelector('button[type="submit"]'),
      loading || saving || row()?.access_state === "revoked",
    );
    disable(layout.newButton, loading || saving || !catalog);
    disable(layout.refreshButton, loading || saving);
    for (const choice of list.querySelectorAll(".agent-choice"))
      disable(choice, loading || saving);
    disable($("agent-apply"), loading || isRunning());
    disable(
      $("agent-probe"),
      loading || isRunning() || row()?.access_state !== "enabled",
    );
    disable(
      detail.querySelector('.agent-test button[type="submit"]'),
      loading || isRunning() || row()?.access_state !== "enabled",
    );
  }
  function setLoading(value) {
    loading = value;
    layout.loading.hidden = !value;
    root.setAttribute("aria-busy", String(value));
    updateControls();
  }
  function stop() {
    clearTimeout(timer);
    timer = null;
    epoch++;
    if (loading) setLoading(false);
  }
  function safeReplace() {
    return (
      !hasUnsavedChanges(panel) ||
      window.confirm(translate("当前有未保存的内容。确定放弃这些编辑？"))
    );
  }
  function row() {
    return rows.find((item) => item.id === selected);
  }
  function changeBody(item) {
    return {
      expected_revision: item.revision,
      request_id: window.crypto.randomUUID(),
    };
  }
  function canAct() {
    if (form && hasUnsavedChanges(form))
      throw new Error("请先保存连接配置，再执行此操作。");
    const item = row();
    if (!item || item.access_state === "revoked")
      throw new Error("此连接不可操作。");
    return item;
  }
  function listRows() {
    list.replaceChildren();
    setText(
      layout.summary,
      phrase("已保存的连接（{p0}）", { p0: rows.length }),
    );
    if (!rows.length) {
      const empty = el("p", uiText("尚未创建 Agent 连接。"), "empty");
      empty.setAttribute("role", "listitem");
      list.append(empty);
    }
    for (const item of rows) {
      const card = el("div", null, "row");
      card.setAttribute("role", "listitem");
      const choice = control(item.configuration.name, () => choose(item.id), {
        localize: false,
      });
      choice.className = "agent-choice";
      choice.dataset.connectionId = item.id;
      if (item.id === selected) choice.setAttribute("aria-current", "true");
      card.append(
        choice,
        el(
          "p",
          item.configuration.agent_type === "hermes"
            ? uiText("Hermes")
            : uiText("通用 MCP"),
          "muted",
        ),
      );
      states(card, item);
      list.append(card);
    }
  }
  function choose(id) {
    if (loading || saving || !catalog) return;
    if (!safeReplace()) return;
    stop();
    active = null;
    selected = id;
    for (const choice of list.querySelectorAll(".agent-choice")) {
      if (choice.dataset.connectionId === id)
        choice.setAttribute("aria-current", "true");
      else choice.removeAttribute("aria-current");
    }
    renderDetail();
    focusAgentSection(detail.querySelector("h3"));
  }
  function renderDetail() {
    detail.replaceChildren();
    result.replaceChildren();
    for (const key of ["state", "kind", "operationId"])
      delete result.dataset[key];
    if (!catalog) return;
    const item = row();
    detail.append(
      el("h3", item ? item.configuration.name : uiText("新建 Agent 连接")),
    );
    if (item) {
      const status = el("div");
      status.id = "agent-detail-states";
      states(status, item);
      detail.append(status);
    }
    form = connectionForm(vaults, catalog, item?.configuration);
    detail.append(form);
    if (item?.access_state === "revoked") {
      for (const input of form.querySelectorAll("input,select,button"))
        input.disabled = true;
      detail.append(
        el("p", uiText("连接已永久撤销。可新建连接，不能恢复此凭证。")),
      );
      return;
    }
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (saving) return;
      const submittedForm = form,
        button = submittedForm.querySelector('button[type="submit"]');
      const acknowledge = captureDraftSave(submittedForm),
        generation = epoch;
      button.disabled = true;
      setBusy(button, true);
      saving = true;
      updateControls();
      try {
        const configuration = readConfiguration(submittedForm),
          current = row();
        const saved = current
          ? await api.agents.save(current.id, {
              ...changeBody(current),
              configuration,
            })
          : await api.agents.create({
              request_id: window.crypto.randomUUID(),
              configuration,
            });
        if (generation !== epoch) return;
        acknowledge();
        rows = [saved, ...rows.filter((r) => r.id !== saved.id)];
        selected = saved.id;
        listRows();
        stop();
        active = null;
        result.replaceChildren();
        if (!hasUnsavedChanges(detail)) renderDetail();
        else updateState(saved);
        notify("连接已保存并暂停。后续编辑已保留；应用配置后可授权查询。");
      } catch (error) {
        notify(error.message, true);
      } finally {
        saving = false;
        setBusy(button, false);
        button.disabled = false;
        updateControls();
      }
    });
    if (!item) return;
    const operations = el("div", null, "row agent-actions");
    operations.append(
      control("应用接入配置", async () => {
        const current = canAct();
        const preview =
          current.configuration.agent_type === "hermes"
            ? "将为此连接创建独立 Hermes 配置和 MCP 入口，不修改现有飞书 gateway。"
            : "将创建只读 MCP 入口；外部宿主仍需自行导入配置。";
        if (
          !(await confirmAction({
            title: "应用接入配置？",
            body: preview,
            confirmLabel: "应用配置",
          }))
        )
          return;
        await start("apply_config");
      }),
    );
    operations.querySelector("button").id = "agent-apply";
    operations.append(
      control(
        item.access_state === "enabled" ? "暂停查询" : "授权并启用查询",
        async () => {
          const current = canAct(),
            action = current.access_state === "enabled" ? "pause" : "enable";
          const saved = await api.agents.control(
            current.id,
            action,
            changeBody(current),
          );
          rows = rows.map((r) => (r.id === saved.id ? saved : r));
          listRows();
          updateState(saved);
          notify(
            action === "pause"
              ? "查询授权已暂停；后台拒绝后续检索。"
              : "已授予所选库 query，开始无模型连接检查。",
          );
          if (action === "enable") await start("probe");
        },
      ),
    );
    operations.querySelectorAll("button")[1].id = "agent-toggle-access";
    const probe = control("验证连接（无模型）", () => start("probe"));
    probe.id = "agent-probe";
    probe.disabled = item.access_state !== "enabled";
    probe.dataset.policyDisabled = String(probe.disabled);
    operations.append(
      probe,
      control("导出 MCP 配置", async () => {
        canAct();
        const value = await api.agents.export(item.id);
        result.replaceChildren(
          el(
            "p",
            uiText(
              "以下配置无明文凭证；在同一台机器的外部 MCP 客户端导入后再核对。",
            ),
          ),
        );
        const area = el("textarea");
        area.readOnly = true;
        area.rows = 12;
        area.value = JSON.stringify(value, null, 2);
        setText(area, uiText("无秘密 MCP 配置"), "aria-label");
        result.append(area);
        area.focus();
        area.select();
      }),
    );
    const lifecycle = el("details");
    lifecycle.append(el("summary", uiText("凭证与撤销")));
    lifecycle.append(
      control("轮换凭证", async () => {
        canAct();
        if (
          !(await confirmAction({
            title: "轮换凭证？",
            body: "旧凭证将永久失效，新凭证初始暂停，需重新应用并授权。",
            confirmLabel: "轮换凭证",
            tone: "danger",
          }))
        )
          return;
        await start("rotate_credential");
      }),
      control("永久撤销连接", async () => {
        const current = canAct();
        if (
          !(await confirmAction({
            title: "永久撤销此连接全部访问？",
            body: "已交付外部 Agent 的内容无法收回。",
            confirmLabel: "永久撤销",
            tone: "danger",
          }))
        )
          return;
        await api.agents.control(current.id, "revoke", changeBody(current));
        await refresh(false);
        notify("连接已撤销。");
      }),
    );
    detail.append(
      operations,
      lifecycle,
      accessPanel(item, api, canAct),
      knowledgePanel(item, api, canAct),
    );
    if (item.configuration.agent_type === "hermes")
      detail.append(
        testForm(item, {
          vaults,
          catalog,
          getCurrent: row,
          start,
          notify,
          isRunning,
        }),
      );
    else
      detail.append(
        el(
          "p",
          uiText(
            "通用 MCP 提供只读目录、检索、专家对象与证据工具。模型回答、强制检索与费用由外部客户端控制；导出不代表宿主已加载。",
          ),
          "muted",
        ),
      );
    detail.append(result);
    const history = el("details");
    history.id = "agent-history";
    history.append(el("summary", uiText("操作记录（每页最多 50 项）")));
    const historyBody = el("div");
    let offset = 0;
    async function historyPage(next) {
      const generation = epoch;
      const records = await api.agents.history(item.id, next);
      if (generation !== epoch || selected !== item.id) return;
      offset = next;
      historyBody.replaceChildren();
      showJSON(
        historyBody,
        records,
        `第 ${Math.floor(offset / 50) + 1} 页 · 安全摘要`,
      );
      const previous = control("上一页", () =>
        historyPage(Math.max(0, offset - 50)),
      );
      const more = control("下一页", () => historyPage(offset + 50));
      previous.disabled = offset === 0;
      previous.dataset.policyDisabled = String(previous.disabled);
      more.disabled = records.length < 50 || offset >= 9950;
      more.dataset.policyDisabled = String(more.disabled);
      const pagination = el("div", null, "panel-actions");
      pagination.append(previous, more);
      historyBody.append(pagination);
    }
    history.append(
      control("读取操作记录", () => historyPage(0)),
      historyBody,
    );
    detail.append(history);
    const test = detail.querySelector(".agent-test");
    const children = [...detail.children],
      formIndex = children.indexOf(form),
      end = children.indexOf(test || result);
    createSections(
      detail,
      [
        {
          id: "configuration",
          label: "配置与范围",
          nodes: children.slice(formIndex, end),
        },
        { id: "query", label: "问答与结果", nodes: [test, result] },
        { id: "history", label: "操作记录", nodes: [history] },
      ],
      { id: "agent-detail" },
    );
    rememberDraft(detail);
    updateControls();
  }
  function updateState(item) {
    const status = $("agent-detail-states");
    if (status) {
      status.replaceChildren();
      states(status, item);
    }
    const toggle = $("agent-toggle-access");
    if (toggle)
      setText(
        toggle,
        item.access_state === "enabled"
          ? uiText("暂停查询")
          : uiText("授权并启用查询"),
      );
    const probe = $("agent-probe");
    if (probe) {
      probe.disabled = item.access_state !== "enabled";
      probe.dataset.policyDisabled = String(probe.disabled);
    }
    const scope = detail.querySelector(".agent-test-scope");
    if (scope) setText(scope, uiText(describeTest(item, vaults, catalog)));
    const test = detail.querySelector('.agent-test button[type="submit"]');
    if (test)
      test.disabled =
        item.access_state !== "enabled" ||
        item.configuration.agent_type !== "hermes";
    updateControls();
  }
  async function start(kind, request) {
    if (isRunning()) throw new Error(agentError("agent_operation_busy"));
    const current = canAct(),
      generation = epoch;
    let operation;
    submitting = true;
    updateControls();
    try {
      operation = await api.agents.start(current.id, {
        ...changeBody(current),
        kind,
        ...(request ? { request } : {}),
      });
    } finally {
      submitting = false;
      updateControls();
    }
    if (generation !== epoch) return;
    active = {
      identity: current.id,
      operation: operation.id,
      deadline: Date.now() + 185000,
    };
    showOperation(operation);
    if (["queued", "running"].includes(operation.state)) observe(generation);
    else await finish(operation, generation);
  }
  function showOperation(operation) {
    const cancelFocused = document.activeElement?.id === "agent-cancel";
    // A disabled cancel button loses focus before its async handler runs.
    // Preserve the request intent across polling, without taking focus from edits.
    const restoreCancelFocus =
      cancelFocused ||
      (active?.cancelRequested &&
        (document.activeElement === document.body ||
          result.contains(document.activeElement)));
    if (active?.operation === operation.id) active.state = operation.state;
    const connection = selected;
    operationResult(
      result,
      operation,
      (module_id, expected_version, identity) =>
        api.agents.knowledgeEvidence(connection, {
          module_id,
          expected_version,
          identity,
          kind: "expert_media",
        }),
    );
    if (["queued", "running"].includes(operation.state)) {
      const cancel = control("取消受管操作", async () => {
        const current = row();
        if (!current || !active || active.cancelRequested) return;
        const pending = active;
        pending.cancelRequested = true;
        try {
          await api.agents.cancel(
            current.id,
            pending.operation,
            changeBody(current),
          );
        } catch (error) {
          pending.cancelRequested = false;
          throw error;
        }
        notify("已请求取消，后台将停止本应用拥有的进程。");
      });
      cancel.id = "agent-cancel";
      disable(cancel, active?.cancelRequested);
      result.append(cancel);
      if (restoreCancelFocus)
        (cancel.disabled ? result : cancel).focus({ preventScroll: true });
    } else if (restoreCancelFocus) result.focus({ preventScroll: true });
    updateControls();
  }
  async function observe(generation) {
    if (!active || generation !== epoch || panel.hidden || root.hidden) return;
    if (Date.now() >= active.deadline) {
      stop();
      notify("观察已到期。后台仍有硬截止；可手动刷新核对。", true);
      return;
    }
    try {
      const operation = await api.agents.status(
        active.identity,
        active.operation,
      );
      if (generation !== epoch) return;
      showOperation(operation);
      if (["queued", "running"].includes(operation.state))
        timer = setTimeout(() => observe(generation), 800);
      else await finish(operation, generation);
    } catch (error) {
      if (generation === epoch) {
        stop();
        notify(error.message, true);
      }
    }
  }
  async function finish(operation, generation) {
    if (generation !== epoch) return;
    clearTimeout(timer);
    timer = null;
    const item = await api.agents.detail(selected);
    if (generation !== epoch) return;
    rows = rows.map((r) => (r.id === item.id ? item : r));
    listRows();
    updateState(item);
    // The completed result was already rendered by start/observe. Recreating it here
    // would reread the same authorized expert images after the detail refresh.
    notify(
      operation.state === "succeeded"
        ? "操作已完成。配置、工具可用和问答结果分别显示。"
        : "操作未完成，请查看本次安全错误。",
      operation.state !== "succeeded",
    );
  }
  async function refresh(guard = true) {
    if (loading || saving) return;
    if (guard && !safeReplace()) return;
    stop();
    const generation = epoch;
    setLoading(true);
    try {
      const [connections, available, choices] = await Promise.all([
        api.agents.list(),
        api.vaults(),
        api.agents.catalog(),
      ]);
      if (generation !== epoch) return;
      rows = connections;
      vaults = available;
      catalog = choices;
      if (selected && !row()) selected = null;
      listRows();
      renderDetail();
      if (selected) {
        const operations = await api.agents.history(selected);
        if (generation !== epoch) return;
        const latest = operations.find((op) =>
          ["test_query", "probe"].includes(op.kind),
        );
        if (latest) {
          active = {
            identity: selected,
            operation: latest.id,
            deadline: Date.now() + 185000,
          };
          const operation = await api.agents.status(selected, latest.id);
          if (generation !== epoch) return;
          showOperation(operation);
          if (["queued", "running"].includes(operation.state))
            observe(generation);
        }
      }
    } finally {
      if (generation === epoch) setLoading(false);
    }
  }
  for (const button of panel.querySelectorAll("[data-client-view]"))
    button.addEventListener("click", () => {
      if (!safeReplace()) return;
      stop();
      const agent = button.dataset.clientView === "agent";
      root.hidden = !agent;
      ordinary.hidden = agent;
      for (const tab of panel.querySelectorAll("[data-client-view]"))
        tab.setAttribute("aria-pressed", String(tab === button));
    });
  new window.MutationObserver(() => {
    if (panel.hidden) stop();
  }).observe(panel, { attributes: true, attributeFilter: ["hidden"] });
  window.addEventListener("pagehide", stop);
  return {
    refresh,
    prefillVault(vaultId) {
      choose(null);
      if (!form) return;
      for (const choice of form.querySelectorAll('[name="vault_ids"]'))
        choice.checked = choice.value === vaultId;
      rememberDraft(form);
    },
    clear() {
      stop();
      selected = null;
      rows = [];
      active = null;
      catalog = null;
      list.replaceChildren();
      detail.replaceChildren();
      saving = false;
      submitting = false;
      setLoading(false);
    },
  };
}
