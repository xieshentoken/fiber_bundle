import { optionText, phrase, setText, uiText } from "./i18n/index.js";
import { el, checkbox, action } from "./ui.js";
import { control } from "./record_ui.js";
import {
  rememberDraft,
  captureDraftSave,
  hasUnsavedChanges,
} from "./unsaved_changes.js";
import { intakeLabel, renderIntakeRecords } from "./channel_intake_records.js";
import { failureText } from "./batch_progress.js";

const materialNames = {
  text: "文字",
  link: "链接",
  image: "图片",
  file: "文件",
};
export function intakeSettings(form) {
  const field = (key) => form.elements.namedItem(key);
  return {
    policy_version: 2,
    enabled: field("enabled").checked,
    target_vault_id: field("target_vault_id").value || null,
    grouping: field("grouping").value,
    materials: [...form.querySelectorAll('[name="material"]:checked')].map(
      (n) => n.value,
    ),
    sites: [...form.querySelectorAll('[name="site"]:checked')].map(
      (n) => n.value,
    ),
    custom_domains: field("custom_domains")
      .value.split(/\n/)
      .map((s) => s.trim())
      .filter(Boolean),
    store_limit: 0,
    download_limit: 0,
    outbound_limit: 0,
    receipt: field("receipt").checked,
    fetch_articles: field("fetch_articles").checked,
    fetch_subtitles: field("fetch_subtitles").checked,
  };
}

export function createChannelIntake(
  api,
  getEntry,
  refreshOverview = async () => {},
) {
  const root = el("details", null, "channel-intake"),
    summary = el("summary", uiText("自动入库与网站白名单")),
    status = el("p", uiText("展开后读取此入口的策略。"), "muted"),
    panel = el("div"),
    records = el("div");
  let snapshot = null,
    form = null,
    loading = false,
    loaded = false,
    observedAt = 0,
    timer = null;
  const visible = () =>
    root.open &&
    root.isConnected &&
    !document.hidden &&
    !root.closest("#channels-panel")?.hidden;
  function stop() {
    clearTimeout(timer);
    timer = null;
  }
  function schedule() {
    if (timer || !visible()) return;
    timer = setTimeout(() => {
      timer = null;
      if (visible()) load(true);
    }, 5000);
  }
  root.append(
    summary,
    status,
    control("刷新策略与最近记录", async () => load()),
    panel,
    records,
  );

  async function load(observe = false) {
    if (loading) return;
    loading = true;
    try {
      const value = await api.channelIntake(getEntry().id);
      if (!root.isConnected || (observe && !visible())) return;
      if (snapshot && value.revision < snapshot.revision) return;
      loaded = true;
      observedAt = Date.now();
      setText(
        status,
        phrase(
          "{p0} · 目标库：{p1} · 累计归档 {p2}，下载 {p3}，飞书回执 {p4}",
          {
            p0: value.settings.enabled
              ? uiText("自动入库已开启")
              : uiText("自动入库已关闭"),
            p1:
              value.targets.find((v) => v.id === value.target_vault_id)?.name ||
              "尚未选择 / 授权不可用",
            p2: value.used.store,
            p3: value.used.download,
            p4: value.used.outbound,
          },
        ),
      );
      for (const reason of value.blockers || [])
        status.append(el("span", ` · ${intakeLabel(reason)}`));
      if (value.worker?.error)
        status.append(
          el("span", ` · ${failureText(value.worker.error)}`, "error"),
        );
      else if (
        value.worker &&
        (!value.worker.fresh || value.worker.state === "needs_review")
      )
        status.append(
          el(
            "span",
            ` · ${intakeLabel(value.worker.state === "needs_review" ? "intake_worker_stopped" : "intake_worker_unobserved")}`,
            "error",
          ),
        );
      else if (value.worker?.state === "intake_requires_review")
        status.append(
          el("span", ` · ${intakeLabel("intake_requires_review")}`),
        );
      if (!value.supported) {
        panel.replaceChildren(
          el(
            "p",
            uiText("此入口的自动归档尚不可用；当前支持独立飞书知识入口。"),
          ),
        );
        return;
      }
      const focused = form?.contains(document.activeElement);
      if (!form || (!hasUnsavedChanges(form) && !(observe && focused))) {
        const changed = snapshot?.revision !== value.revision;
        snapshot = value;
        if (!observe || changed) build(value);
      } else if (value.revision !== snapshot.revision) {
        status.append(
          el(
            "span",
            uiText(" · 策略已在其他窗口改变，当前草稿保留；保存前请核对版本。"),
          ),
        );
      }
      if (!(
        observe &&
        (records.contains(document.activeElement) ||
          records.querySelector("details[open]"))
      ))
        renderIntakeRecords(records, api, getEntry().id, value, () => load());
    } catch (error) {
      if (observe)
        setText(
          status,
          uiText("最近记录读取失败，当前显示旧结果；可手动刷新。"),
        );
      else throw error;
    } finally {
      loading = false;
      schedule();
    }
  }
  function build(value) {
    const priorFocus = panel.contains(document.activeElement)
      ? document.activeElement.name
      : "";
    form = el("form", null, "create-form intake-form");
    form.dataset.draft = "tracked";
    form.addEventListener("submit", (e) => e.preventDefault());
    const check = (key, text) => {
      const row = checkbox(text, value.settings[key]);
      row.input.name = key;
      form.append(row.label);
      return row.input;
    };
    check("enabled", "自动归档新消息");
    const target = el("select"),
      targetLabel = el("label", uiText("目标知识库（必须已有可写授权）"));
    target.name = "target_vault_id";
    target.append(optionText("请选择目标库", ""));
    for (const vault of value.targets)
      target.append(new window.Option(vault.name, vault.id));
    target.value = value.settings.target_vault_id || "";
    targetLabel.append(target);
    form.append(targetLabel);
    const materials = el("fieldset"),
      sites = el("fieldset"),
      grouping = el("select"),
      groupLabel = el("label", uiText("同批消息保存方式"));
    materials.append(el("legend", uiText("自动归档的资料类型")));
    for (const [key, title] of Object.entries(materialNames)) {
      const row = checkbox(title, value.settings.materials.includes(key));
      row.input.name = "material";
      row.input.value = key;
      materials.append(row.label);
    }
    sites.append(el("legend", uiText("允许自动处理的网站")));
    for (const site of value.sites) {
      const row = checkbox(site.name, value.settings.sites.includes(site.id));
      row.input.name = "site";
      row.input.value = site.id;
      sites.append(row.label);
    }
    const siteActions = el("div", null, "panel-actions");
    for (const [label, checked] of [
      ["全选网站", true],
      ["清空网站", false],
    ])
      siteActions.append(
        control(label, async () => {
          for (const node of sites.querySelectorAll("input")) {
            node.checked = checked;
            node.dispatchEvent(new window.Event("change", { bubbles: true }));
          }
        }),
      );
    const domains = el("textarea"),
      domainLabel = el(
        "label",
        uiText("自定义域名（每行一个；删除对应行即可移除）"),
      );
    domains.name = "custom_domains";
    domains.value = value.settings.custom_domains.join("\n");
    domains.placeholder = "papers.org";
    domains.maxLength = 5200;
    domainLabel.append(domains);
    sites.append(
      siteActions,
      domainLabel,
      el(
        "p",
        uiText(
          "空白名单不会允许所有网站。自定义域名可归档链接；没有提取器时正文待补全。",
        ),
        "muted",
      ),
    );
    grouping.name = "grouping";
    grouping.append(
      optionText("同批合并（保留消息顺序）", "batch"),
      optionText("每条消息分别保存", "message"),
    );
    grouping.value = value.settings.grouping;
    groupLabel.append(grouping);
    form.append(
      materials,
      sites,
      groupLabel,
      el(
        "p",
        uiText(
          "基础提取无需次数额度。智能处理、完整性校验和直接入库沿用目标知识库的提取与入库策略。",
        ),
        "muted",
      ),
    );
    const showSites = () => {
      sites.hidden = !materials.querySelector('[value="link"]').checked;
    };
    materials.addEventListener("change", showSites);
    showSites();
    check(
      "fetch_articles",
      "归档链接后，提取可取得的 X 长文章 / 公众号正文及原图",
    );
    check(
      "fetch_subtitles",
      value.settings.policy_version === 2
        ? "归档视频链接后提取笔记（字幕优先，无字幕自动转写，必要时分析画面；不设时长额度）"
        : "归档视频链接后，获取可取得的元数据 / 已有字幕（不设时长额度）",
    );
    check("receipt", "发送接收确认及处理/入库结果回执（次数不限）");
    form.append(
      el(
        "p",
        uiText(
          "自动归档仅处理启用后的新消息，历史资料需明确选择。提取方式及笔记自动保存由目标知识库的“提取与入库策略”控制：基础提取或 Pi + Skill 均可自动保存；未开启自动保存时，提取稿等待确认。已有资料和累计用量始终保留。",
        ),
        "muted",
      ),
    );
    const save = el("button", uiText("保存自动入库策略"));
    save.type = "button";
    save.name = "save_intake";
    action(save, async (notify) => {
      if (!form.reportValidity()) return;
      const acknowledge = captureDraftSave(form),
        submitted = intakeSettings(form),
        revision = snapshot.revision;
      const result = await api.channelIntakeSave(getEntry().id, {
        expected_revision: revision,
        settings: submitted,
      });
      if (!root.isConnected) return;
      acknowledge();
      snapshot = { ...snapshot, ...result };
      await load();
      await refreshOverview();
      notify("策略已保存；历史消息未回放。入口运行时将处理之后的新消息。");
    });
    form.append(save);
    panel.replaceChildren(form);
    rememberDraft(form);
    if (priorFocus)
      form.elements.namedItem(priorFocus)?.focus({ preventScroll: true });
  }
  root.addEventListener("toggle", () => {
    if (root.open && !loaded)
      load().catch(() => {
        setText(status, uiText("策略读取失败，请刷新重试。"));
      });
    else if (root.open) schedule();
    else stop();
  });
  return {
    root,
    stop,
    observe() {
      if (!visible()) return stop();
      if (Date.now() - observedAt >= 5000) return load(true);
      schedule();
    },
  };
}
