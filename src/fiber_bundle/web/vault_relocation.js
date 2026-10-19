import { chooseDirectory } from "./native_directory_picker.js";
import { renderRelocation } from "./vault_relocation_view.js";
import { relocationActive } from "./vault_relocation_api.js";
import { confirmAction } from "./confirm_dialog.js";
import { revealSection } from "./view_sections.js";

// A page-local controller; every response is checked against the current Vault and view epoch.
export function createVaultRelocationView(api, host, refreshData) {
  let epoch = 0,
    timer = null,
    busy = false,
    state = null;
  function cancelTimer() {
    if (timer) clearTimeout(timer);
    timer = null;
  }
  function clear() {
    epoch++;
    cancelTimer();
    state = null;
    busy = false;
    host.replaceChildren();
    host.hidden = true;
    host.parentElement?.removeAttribute("data-relocating");
    document.body.classList.remove("show-vault-relocation");
  }
  async function close() {
    clear();
    await refreshData();
  }
  function destination() {
    return `${state.parent.replace(/\/$/, "")}/${state.name}`;
  }
  const actions = {
    destination,
    input(parent, name) {
      state.parent = parent;
      state.name = name;
      state.packet = null;
    },
    close,
    show(packet) {
      if (packet.vault_id !== state.vault.id) return;
      state.packet = packet;
      followState();
      draw(true);
    },
    choose() {
      cancelTimer();
      state.screen = "choose";
      state.packet = null;
      state.error = "";
      draw(true);
    },
    async pick() {
      if (!window.fiberDesktop?.selectDirectory)
        throw new Error("Web 请填写本机绝对目录路径。");
      const captured = epoch,
        current = state;
      const previous = current.parent;
      const path = await chooseDirectory(previous);
      return captured === epoch &&
        state === current &&
        current.parent === previous
        ? path
        : null;
    },
    preview: () =>
      mutate(async (current) => {
        if (
          !current.parent.startsWith("/") ||
          !current.name ||
          /[/\\\0]/.test(current.name) ||
          [".", ".."].includes(current.name)
        )
          throw new Error("请选择目标父目录并填写单层目录名称。");
        current.packet = await api.preview(
          current.vault.id,
          `${current.parent.replace(/\/$/, "")}/${current.name}`,
        );
        current.screen = "review";
      }),
    start: () =>
      mutate(async (current) => {
        current.packet = await api.start(
          current.packet.id,
          current.packet.plan_sha256,
        );
        followState(current);
      }),
    stop: () =>
      mutate(async (current) => {
        current.packet = await api.stop(
          current.packet.id,
          current.packet.plan_sha256,
        );
        followState(current);
      }),
    recoveryPreview: () =>
      mutate(async (current) => {
        current.packet = await api.recoveryPreview(current.packet.id);
      }),
    recover: (action) =>
      mutate(async (current) => {
        if (
          !(await confirmAction({
            title: action === "restore" ? "恢复原位置？" : "继续迁移？",
            body: [
              current.packet.source + " → " + current.packet.destination,
              "再次核对目录身份，不覆盖已有文件。",
            ],
            confirmLabel: action === "restore" ? "恢复原位置" : "继续迁移",
          }))
        )
          return;
        if (state !== current) return;
        current.packet = await api.recover(
          current.packet.id,
          action,
          current.packet.recovery.sha256,
        );
        followState(current);
      }),
  };
  function followState(current = state) {
    const packet = current.packet;
    current.screen = ["complete", "restored"].includes(packet.state)
      ? "result"
      : packet.state === "needs_recovery"
        ? "recovery"
        : packet.state === "prepared"
          ? "review"
          : "progress";
    if (packet.state === "complete" || packet.state === "restored")
      current.vault.absolute_root = packet.current_path;
  }
  function draw(focus = false) {
    if (!state) return;
    const result = renderRelocation(host, { ...state, busy }, actions);
    if (focus) result.title.focus({ preventScroll: true });
    cancelTimer();
    if (!busy && state.screen === "progress" && relocationActive(state.packet))
      timer = setTimeout(poll, 800);
  }
  async function mutate(work) {
    if (busy || !state) return;
    const captured = epoch,
      working = state;
    busy = true;
    state.error = "";
    host
      .querySelector(".relocation-workspace")
      ?.setAttribute("aria-busy", "true");
    if (host.firstChild) host.firstChild.inert = true;
    try {
      await work(working);
    } catch (error) {
      if (captured === epoch && state) state.error = error.message;
    } finally {
      if (captured === epoch && state) {
        busy = false;
        draw(true);
      }
    }
  }
  async function poll() {
    if (
      !state ||
      busy ||
      document.hidden ||
      document.getElementById("vaults-panel")?.hidden
    ) {
      if (state) timer = setTimeout(poll, 1200);
      return;
    }
    const captured = epoch,
      identity = state.packet.id;
    try {
      const packet = await api.status(identity);
      if (packet.id !== identity || packet.vault_id !== state?.vault.id)
        throw new Error("迁移状态的身份不符，请刷新。");
      if (captured !== epoch || !state || state.packet.id !== identity) return;
      if (JSON.stringify(packet) !== JSON.stringify(state.packet)) {
        state.packet = packet;
        followState();
        draw();
      } else timer = setTimeout(poll, 800);
    } catch (error) {
      if (captured === epoch && state) {
        state.error = error.message;
        draw();
      }
    }
  }
  async function open(vault) {
    clear();
    const captured = epoch;
    state = {
      vault: { ...vault },
      parent: (vault.absolute_root || "").replace(/\/[^/]+$/, ""),
      name: (vault.absolute_root || "").split("/").pop() || vault.name,
      packet: null,
      screen: "choose",
      error: "",
    };
    busy = true;
    host.hidden = false;
    host.parentElement.setAttribute("data-relocating", "true");
    document.body.classList.add("show-vault-relocation");
    revealSection(host);
    draw(true);
    try {
      const previous = await api.list(vault.id);
      if (captured !== epoch || !state) return;
      if (previous.some((p) => p.vault_id !== vault.id))
        throw new Error("迁移记录的知识库范围不符。");
      state.history = previous;
      const unfinished = previous.find((p) => relocationActive(p));
      if (unfinished) {
        state.packet = unfinished;
        followState();
      }
    } catch (error) {
      if (captured === epoch && state) {
        state.error = error.message;
      }
    } finally {
      if (captured === epoch && state) {
        busy = false;
        draw(true);
      }
    }
  }
  return {
    open,
    clear,
    get vaultId() {
      return state?.vault.id || null;
    },
  };
}
