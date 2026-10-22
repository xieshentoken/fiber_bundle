// Sign-in screen. The password is never stored; the field is cleared after each attempt.
import { $, h, icon, clear } from "./dom.js";
import { strings } from "./i18n.js";
import { errorText } from "./feedback.js";

const t = strings({
  zh: {
    heading: "欢迎回到你的知识空间",
    intro: "在本机管理知识库、资料导入和 Agent 访问权限。",
    password: "管理密码",
    submit: "登录",
    signingIn: "正在登录…",
    firstRun: "首次使用？",
    firstRunText:
      "双击发行文件夹中的“设置密码.command”；从源码运行时在终端执行",
    offline: "无法连接本机后台。请运行“启动后台.command”，然后重试。",
    retry: "重新连接",
    classic: "使用经典界面",
    language: "English",
    wrong: "密码不正确，或尚未设置管理密码。",
    local: "仅限本机 · 127.0.0.1",
  },
  en: {
    heading: "Welcome back to your knowledge space",
    intro: "Manage vaults, imports and agent access on this computer.",
    password: "Admin password",
    submit: "Sign in",
    signingIn: "Signing in…",
    firstRun: "First time?",
    firstRunText:
      "Double-click “设置密码.command” in the release folder; from source, run",
    offline:
      "Can't reach the local backend. Run “启动后台.command” (Start backend), then retry.",
    retry: "Reconnect",
    classic: "Use Classic UI",
    language: "中文",
    wrong: "Wrong password, or no admin password has been set yet.",
    local: "Local only · 127.0.0.1",
  },
});

export function createLogin({ api, onSignedIn, onClassic, onLanguage }) {
  const root = $("#atlas-login");
  const password = h("input.a-input", {
    id: "atlas-password",
    type: "password",
    autocomplete: "current-password",
    required: true,
    maxLength: 1024,
  });
  const error = h("p.a-login-error", { role: "alert", hidden: true });
  const submit = h(
    "button.a-button.is-primary.is-block",
    { type: "submit" },
    t("submit"),
  );
  const form = h(
    "form.a-login-form",
    h("label", { for: password.id }, t("password")),
    password,
    error,
    submit,
  );
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    submit.disabled = true;
    submit.setAttribute("aria-busy", "true");
    submit.textContent = t("signingIn");
    error.hidden = true;
    try {
      const value = await api.login(password.value);
      password.value = "";
      await onSignedIn(value);
    } catch (failure) {
      password.value = "";
      showError(failure);
      password.focus();
    } finally {
      submit.disabled = false;
      submit.removeAttribute("aria-busy");
      submit.textContent = t("submit");
    }
  });
  function showError(failure) {
    if (!failure) {
      error.hidden = true;
      return;
    }
    const offline = failure.code === "network" || failure.code === "timeout";
    error.textContent =
      failure.status === 401
        ? t("wrong")
        : offline
          ? t("offline")
          : errorText(failure).title;
    error.hidden = false;
    retry.hidden = !offline;
  }
  const retry = h(
    "button.a-button.is-ghost",
    { type: "button", hidden: true, onclick: () => window.location.reload() },
    icon("refresh"),
    t("retry"),
  );
  clear(
    root,
    h(
      "div.a-login-card",
      h("img.a-login-mark", {
        src: "/static/atlas/mark.svg",
        alt: "",
        width: 44,
        height: 44,
      }),
      h("h1", t("heading")),
      h("p.a-login-intro", t("intro")),
      form,
      retry,
      h(
        "p.a-login-help",
        h("strong", t("firstRun") + " "),
        t("firstRunText") + " ",
        h("code", "fiber-bundle set-password"),
      ),
      h(
        "div.a-login-foot",
        h("span.a-muted", icon("shield"), t("local")),
        h(
          "span",
          h(
            "button.a-link",
            { type: "button", onclick: onLanguage },
            icon("globe"),
            t("language"),
          ),
          h(
            "button.a-link",
            { type: "button", onclick: onClassic },
            icon("swap"),
            t("classic"),
          ),
        ),
      ),
    ),
  );
  return {
    show(failure) {
      root.hidden = false;
      showError(failure || null);
      password.focus();
    },
    hide() {
      root.hidden = true;
      error.hidden = true;
    },
  };
}
