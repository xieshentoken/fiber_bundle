// Settings. Display preferences never change models, channels or vault permissions; the
// session length applies from the next sign-in.
import { h, clear } from "./dom.js";
import { strings, lang } from "./i18n.js";
import { button, card, field, segmented, select, skeleton } from "./ui.js";
import { run, toast } from "./feedback.js";
import { appearanceSettings } from "./appearance_settings.js";
import { themeChoices } from "./theme_choices.js";
import { switchUi } from "./prefs.js";
import { captureDraftSave, rememberDraft } from "../unsaved_changes.js";
import { dateTime } from "./format.js";

const t = strings({
  zh: {
    interface: "界面",
    ui: "界面风格",
    atlas: "Atlas（新版）",
    classic: "经典",
    uiHelp: "两种界面使用同一后台与数据；切换只改变显示方式。",
    toClassic: "切换到经典界面",
    language: "语言",
    appearance: "外观",
    mode: "颜色模式",
    system: "跟随系统",
    light: "浅色",
    dark: "深色",
    accent: "强调色",
    iris: "鸢尾",
    teal: "青绿",
    amber: "琥珀",
    graphite: "石墨",
    clay: "陶土",
    density: "密度",
    comfortable: "舒适",
    compact: "紧凑",
    motion: "动画",
    reduce: "减少动画",
    previewNote: "外观选择立即生效并保存在本浏览器中，不发送到后台。",
    session: "登录时长",
    sessionHelp:
      "从登录成功开始计时，页面操作不会延长。保存后从下次登录起生效。",
    m15: "15 分钟",
    m30: "30 分钟",
    h1: "1 小时（默认）",
    h4: "4 小时",
    h8: "8 小时",
    h24: "24 小时",
    custom: "{n} 秒（启动配置）",
    expires: "当前会话到期：{time}",
    save: "保存登录时长",
    saved: "登录时长已保存，下次登录生效；当前会话到期时间保持不变。",
    conflict: "设置已在其他窗口更新，请重新进入本页核对后再保存。",
    about: "关于",
    build: "版本信息",
    shortcuts: "快捷键：⌘K / Ctrl K 打开搜索与跳转。",
  },
  en: {
    interface: "Interface",
    ui: "Interface style",
    atlas: "Atlas (new)",
    classic: "Classic",
    uiHelp:
      "Both interfaces use the same backend and data; switching only changes the presentation.",
    toClassic: "Switch to Classic",
    language: "Language",
    appearance: "Appearance",
    mode: "Color mode",
    system: "System",
    light: "Light",
    dark: "Dark",
    accent: "Accent",
    iris: "Iris",
    teal: "Teal",
    amber: "Amber",
    graphite: "Graphite",
    clay: "Clay",
    density: "Density",
    comfortable: "Comfortable",
    compact: "Compact",
    motion: "Motion",
    reduce: "Reduce motion",
    previewNote:
      "Appearance choices apply immediately and are stored in this browser only — never sent to the backend.",
    session: "Session length",
    sessionHelp:
      "Counted from a successful sign-in; activity doesn't extend it. Applies from the next sign-in.",
    m15: "15 minutes",
    m30: "30 minutes",
    h1: "1 hour (default)",
    h4: "4 hours",
    h8: "8 hours",
    h24: "24 hours",
    custom: "{n} s (startup configuration)",
    expires: "Current session expires: {time}",
    save: "Save session length",
    saved:
      "Session length saved; it applies from the next sign-in. The current session's expiry is unchanged.",
    conflict:
      "Settings were updated in another window. Reopen this page and check before saving.",
    about: "About",
    build: "Build",
    shortcuts: "Shortcut: ⌘K / Ctrl K opens search and jump.",
  },
});

export function createView(ctx) {
  const appearanceForm = appearanceSettings(ctx);
  const sessionHost = h("div");
  const aboutHost = h("div");
  function prefRow(label, key, options) {
    return h(
      "div.a-field",
      h("label", label),
      segmented(
        options.map((value) => ({
          value,
          label: t(value === "system" && key === "motion" ? "system" : value),
        })),
        ctx.prefs[key],
        (value) => ctx.savePrefs({ ...ctx.prefs, [key]: value }),
        label,
      ),
    );
  }
  const interfaceCard = card(
    { title: t("interface") },
    h(
      "div.a-field",
      h("label", t("ui")),
      segmented(
        [
          { value: "atlas", label: t("atlas"), icon: "spark" },
          { value: "classic", label: t("classic"), icon: "layers" },
        ],
        "atlas",
        (value) => {
          if (value === "classic" && ctx.confirmReload()) switchUi("classic");
        },
        t("ui"),
      ),
      h("p.a-help", t("uiHelp")),
    ),
    h(
      "div.a-field",
      h("label", t("language")),
      segmented(
        [
          { value: "zh", label: "中文" },
          { value: "en", label: "English" },
        ],
        lang(),
        (value) => ctx.reloadForPrefs({ ...ctx.prefs, lang: value }),
        t("language"),
      ),
    ),
  );
  const appearanceCard = card(
    { title: t("appearance"), subtitle: t("previewNote") },
    prefRow(t("mode"), "mode", ["system", "light", "dark"]),
    prefRow(t("accent"), "accent", [
      "iris",
      "teal",
      "amber",
      "graphite",
      "clay",
    ]),
    prefRow(t("density"), "density", ["comfortable", "compact"]),
    prefRow(t("motion"), "motion", ["system", "reduce"]),
  );
  async function refresh() {
    clear(sessionHost, skeleton(2));
    const [policy, health] = await Promise.all([
      ctx.api.systemSettings(),
      ctx.api.health().catch(() => null),
    ]);
    let current = policy;
    const ttl = select(
      [
        ["900", t("m15")],
        ["1800", t("m30")],
        ["3600", t("h1")],
        ["14400", t("h4")],
        ["28800", t("h8")],
        ["86400", t("h24")],
      ],
      undefined,
    );
    if (![...ttl.options].some((o) => Number(o.value) === policy.session_ttl))
      ttl.append(
        h(
          "option",
          { value: String(policy.session_ttl) },
          t("custom", { n: policy.session_ttl }),
        ),
      );
    ttl.value = String(policy.session_ttl);
    const submit = h(
      "button.a-button.is-primary",
      { type: "submit" },
      t("save"),
    );
    const form = h(
      "form.a-form",
      { dataset: { draft: "tracked" } },
      field(t("session"), ttl, { help: t("sessionHelp") }),
      h("p.a-subtle", t("expires", { time: dateTime(ctx.session.expiresAt) })),
      h("div.a-form-actions", submit),
    );
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      void run(submit, async () => {
        const acknowledge = captureDraftSave(form);
        try {
          current = await ctx.api.saveSystemSettings({
            session_ttl: Number(ttl.value),
            expected_revision: current.revision,
          });
        } catch (error) {
          if (error.status === 409)
            throw new Error(t("conflict"), { cause: error });
          throw error;
        }
        acknowledge();
        toast(t("saved"), { tone: "success" });
      });
    });
    clear(sessionHost, form);
    rememberDraft(form);
    const build = health?.build;
    clear(
      aboutHost,
      build ? h("pre.a-pre", JSON.stringify(build, null, 2)) : null,
      h("p.a-subtle", t("shortcuts")),
      button(t("toClassic"), {
        iconName: "swap",
        onClick: () => ctx.confirmReload() && switchUi("classic"),
      }),
    );
  }
  return {
    node: h(
      "div.a-grid-2",
      h(
        "div.a-stack",
        themeChoices(ctx),
        interfaceCard,
        appearanceCard,
        appearanceForm.node,
      ),
      h(
        "div.a-stack",
        card({ title: t("session") }, sessionHost),
        card({ title: t("about") }, aboutHost),
      ),
    ),
    refresh,
    dispose: appearanceForm.dispose,
  };
}
