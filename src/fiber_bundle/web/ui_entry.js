// The root URL stays fixed for the desktop trust boundary. Only one app is mounted.
import { entryTheme, themeRenderer } from "./theme_entry.js";
let local, session;
try {
  local = window.localStorage;
  session = window.sessionStorage;
} catch {
  /* Classic fallback. */
}
const theme = entryTheme(local, session);
window.fiberEntryTheme = theme;
async function boot() {
  if (themeRenderer(theme) === "classic") {
    document.documentElement.dataset.ui = "classic";
    await import("./app.js");
    return;
  }
  const response = await fetch("/static/atlas/index.html", {
    credentials: "same-origin",
  });
  if (!response.ok) throw new Error("atlas_document_unavailable");
  const parsed = new DOMParser().parseFromString(
    await response.text(),
    "text/html",
  );
  const styles = [...parsed.querySelectorAll('link[rel="stylesheet"]')];
  await Promise.all(
    styles.map(
      (source) =>
        new Promise((resolve, reject) => {
          const link = document.createElement("link");
          link.rel = "stylesheet";
          const url = new URL(
            source.getAttribute("href"),
            window.location.origin,
          );
          if (
            url.origin !== window.location.origin ||
            !url.pathname.startsWith("/static/")
          ) {
            reject(new Error("invalid_atlas_resource"));
            return;
          }
          link.href = url.href;
          link.dataset.uiStyle = "atlas";
          link.onload = resolve;
          link.onerror = () => reject(new Error("atlas_styles_unavailable"));
          document.head.append(link);
        }),
    ),
  );
  for (const link of document.querySelectorAll('[data-ui-style="classic"]'))
    link.remove();
  document.body.replaceChildren(
    ...[...parsed.body.childNodes].map((node) =>
      document.importNode(node, true),
    ),
  );
  document.documentElement.dataset.ui = "atlas";
  document.documentElement.dataset.theme = "atlas";
  const favicon = document.querySelector('link[rel="icon"]');
  if (favicon) favicon.href = "/static/atlas/mark.svg";
  await import("./atlas/main.js");
}
boot().catch(() => {
  document.documentElement.dataset.ui = "error";
  const box = document.createElement("main"),
    text = document.createElement("p"),
    retry = document.createElement("button"),
    classic = document.createElement("button");
  text.textContent = "界面未能加载 / Interface could not load";
  retry.textContent = "重试 / Retry";
  retry.onclick = () => window.location.reload();
  classic.textContent = "打开经典界面 / Open Classic";
  classic.onclick = async () => {
    const { navigateTheme } = await import("./theme_entry.js");
    navigateTheme("clay");
  };
  box.append(text, retry, classic);
  document.body.replaceChildren(box);
});
