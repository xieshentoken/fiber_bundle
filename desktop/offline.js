import { desktopText, retryText } from "./locale_copy.mjs";
let language = "zh-CN";
// The only native read here is a validated, presentation-only snapshot.
document.documentElement.dataset.desktop = "offline";
const themes = ["oracle", "tesla", "cursor", "clay", "dark", "atlas"];
async function initializeAppearance() {
  let timeout;
  try {
    const snapshot = window.fiberDesktop?.appearanceSnapshot;
    const value = snapshot
      ? await Promise.race([
          snapshot(),
          new Promise((resolve) => {
            timeout = setTimeout(() => resolve(null), 1000);
          }),
        ])
      : null;
    document.documentElement.dataset.theme = themes.includes(value?.theme)
      ? value.theme
      : "clay";
    document.documentElement.dataset.mode =
      value?.presentationMode === "dark" ? "dark" : "light";
  } catch {
    document.documentElement.dataset.theme = "clay";
  } finally {
    clearTimeout(timeout);
  }
}
initializeAppearance();
async function initializeLocale() {
  let timeout;
  try {
    const value = await Promise.race([
      window.fiberDesktop?.localeSnapshot?.(),
      new Promise((resolve) => {
        timeout = setTimeout(() => resolve(null), 1000);
      }),
    ]);
    if (["en", "zh-CN"].includes(value?.language)) language = value.language;
  } catch {
    /* Missing old native bridge retains the local default. */
  } finally {
    clearTimeout(timeout);
  }
  document.documentElement.lang = language;
  for (const node of document.querySelectorAll("[data-desktop-text]"))
    node.textContent = desktopText(language, node.dataset.desktopText);
}
await initializeLocale();

let seconds = 15,
  timer = null;
const status = document.getElementById("offline-retry");
function tick() {
  if (seconds <= 0) {
    window.location.href = "fiber://reconnect";
    return;
  }
  status.textContent = retryText(language, seconds);
  seconds--;
  timer = setTimeout(tick, 1000);
}
tick();
window.addEventListener("pagehide", () => clearTimeout(timer), { once: true });
