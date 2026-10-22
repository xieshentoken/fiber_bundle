import { setText, uiText } from "./i18n/index.js";
// A styled, keyboard-accessible single-select projection. The original select remains
// the sole form/draft/API value, including required validation and programmatic writes.
import { availableOptions, nextOption, findOption } from "./select_state.js";

let sequence = 0;
export function enhanceSelects(
  root = document.body,
  { dialogs = false, popupClass = "" } = {},
) {
  const controls = new Map();
  let opened = null;
  function attach(select) {
    // Native dialog owns its top layer; a body-level custom popup would be inert.
    const dialog = select.closest("dialog");
    if (dialog && !dialogs) return;
    if (controls.has(select) || select.multiple || select.size > 1) return;
    const wrapper = document.createElement("span");
    wrapper.className = "select-field";
    const button = document.createElement("button");
    button.type = "button";
    button.className = "select-trigger";
    button.setAttribute("role", "combobox");
    button.setAttribute("aria-haspopup", "listbox");
    button.setAttribute("aria-expanded", "false");
    const text = document.createElement("span");
    text.className = "select-value";
    const caret = document.createElement("span");
    caret.className = "select-caret";
    caret.setAttribute("aria-hidden", "true");
    button.append(text, caret);
    const list = document.createElement("div");
    list.id = `select-options-${++sequence}`;
    list.className = "select-options" + (popupClass ? " " + popupClass : "");
    const popover = dialogs && typeof list.showPopover === "function";
    if (popover) list.setAttribute("popover", "manual");
    list.setAttribute("role", "listbox");
    list.hidden = true;
    button.setAttribute("aria-controls", list.id);
    const error = document.createElement("span");
    error.id = `select-error-${sequence}`;
    error.className = "select-error";
    error.setAttribute("role", "alert");
    error.hidden = true;
    const originalTab = select.getAttribute("tabindex");
    const originalAria = select.getAttribute("aria-hidden");
    select.before(wrapper);
    wrapper.append(select, button, error);
    (dialog || document.body).append(list);
    select.classList.add("select-native");
    select.tabIndex = -1;
    select.setAttribute("aria-hidden", "true");
    let active = -1,
      search = "",
      searchedAt = 0,
      renderedOptions = "";

    function label() {
      if (select.getAttribute("aria-label"))
        return select.getAttribute("aria-label");
      return (
        Array.from(select.labels || [])
          .map((node) => {
            const copy = node.cloneNode(true);
            copy
              .querySelectorAll("select,.select-field,button,input")
              .forEach((child) => child.remove());
            return copy.textContent.trim();
          })
          .join(" ") || String(uiText("选择选项"))
      );
    }
    function position() {
      const box = button.getBoundingClientRect();
      if (!box.width || !box.height) return close(false);
      const below = window.innerHeight - box.bottom - 12;
      const above = box.top - 12;
      const upward = below < 160 && above > below;
      const width = Math.min(Math.max(box.width, 200), window.innerWidth - 24);
      list.style.width = `${width}px`;
      list.style.left = `${Math.max(12, Math.min(box.left, window.innerWidth - width - 12))}px`;
      list.style.maxHeight = `${Math.max(60, Math.min(300, upward ? above : below))}px`;
      list.style.top = upward ? "auto" : `${box.bottom + 6}px`;
      list.style.bottom = upward
        ? `${window.innerHeight - box.top + 6}px`
        : "auto";
    }
    function highlight(index, reveal = true) {
      active = index;
      for (const node of list.querySelectorAll('[role="option"]')) {
        node.dataset.active = String(Number(node.dataset.index) === active);
        node.setAttribute(
          "aria-selected",
          String(Number(node.dataset.index) === select.selectedIndex),
        );
      }
      const node = list.querySelector(`[data-index="${active}"]`);
      if (node) {
        button.setAttribute("aria-activedescendant", node.id);
        // Reveal keyboard navigation inside this popup only. Scrolling ancestors
        // can move the trigger and flip an upward-opening font list.
        if (reveal) {
          const row = node.getBoundingClientRect();
          const viewport = list.getBoundingClientRect();
          const top = viewport.top + list.clientTop;
          const bottom = top + list.clientHeight;
          if (row.top < top) list.scrollTop += row.top - top;
          else if (row.bottom > bottom) list.scrollTop += row.bottom - bottom;
        }
      } else button.removeAttribute("aria-activedescendant");
    }
    function close(focus = true) {
      if (popover && list.matches(":popover-open")) list.hidePopover();
      list.hidden = true;
      button.setAttribute("aria-expanded", "false");
      button.removeAttribute("aria-activedescendant");
      if (opened === control) opened = null;
      if (focus && button.isConnected && !button.disabled)
        button.focus({ preventScroll: true });
    }
    function commit(index) {
      if (
        !availableOptions(select.options).some(
          (item) => item.index === index,
        ) ||
        button.disabled
      )
        return;
      const changed = select.selectedIndex !== index;
      select.selectedIndex = index;
      error.hidden = true;
      sync();
      close();
      if (changed) {
        select.dispatchEvent(new window.Event("input", { bubbles: true }));
        select.dispatchEvent(new window.Event("change", { bubbles: true }));
      }
    }
    function renderOptions() {
      const signature = JSON.stringify(
        Array.from(select.options, (option) => [
          option.textContent,
          option.hidden,
          option.disabled,
          option.parentElement.tagName,
          option.parentElement.label,
          option.parentElement.disabled,
        ]),
      );
      if (signature === renderedOptions) return;
      renderedOptions = signature;
      const scrollTop = list.scrollTop;
      list.replaceChildren();
      let group = null;
      for (const [index, option] of Array.from(select.options).entries()) {
        if (option.hidden) continue;
        if (
          option.parentElement.tagName === "OPTGROUP" &&
          option.parentElement !== group
        ) {
          group = option.parentElement;
          const title = document.createElement("div");
          title.className = "select-group";
          setText(title, group.label);
          list.append(title);
        }
        const node = document.createElement("div");
        node.id = `${list.id}-${index}`;
        node.dataset.index = String(index);
        node.setAttribute("role", "option");
        node.setAttribute(
          "aria-disabled",
          String(option.disabled || option.parentElement.disabled === true),
        );
        setText(node, option.textContent);
        node.addEventListener("pointermove", () => {
          if (node.getAttribute("aria-disabled") !== "true" && active !== index)
            highlight(index, false);
        });
        node.addEventListener("pointerdown", (event) => event.preventDefault());
        node.addEventListener("click", () => commit(index));
        list.append(node);
      }
      list.scrollTop = scrollTop;
    }
    function sync() {
      wrapper.hidden = select.hidden;
      if (!error.hidden && select.validity.valid) error.hidden = true;
      const selected = select.options[select.selectedIndex];
      const value = selected?.textContent ?? String(uiText("请选择"));
      if (text.textContent !== value) setText(text, value);
      button.disabled = select.matches(":disabled");
      setText(button, label(), "aria-label");
      button.setAttribute("aria-required", String(select.required));
      button.setAttribute(
        "aria-invalid",
        String(!error.hidden || select.getAttribute("aria-invalid") === "true"),
      );
      const describedBy = [
        select.getAttribute("aria-describedby"),
        !error.hidden && error.id,
      ]
        .filter(Boolean)
        .join(" ");
      if (describedBy) button.setAttribute("aria-describedby", describedBy);
      else button.removeAttribute("aria-describedby");
      if (opened === control) {
        if (button.disabled || !button.getClientRects().length)
          return close(false);
        const eligible = availableOptions(select.options);
        if (!eligible.some((item) => item.index === active))
          active = eligible[0]?.index ?? -1;
        renderOptions();
        highlight(active, false);
        position();
      }
    }
    function open() {
      sync();
      if (button.disabled) return;
      opened?.close(false);
      opened = control;
      renderOptions();
      list.hidden = false;
      if (popover) list.showPopover();
      button.setAttribute("aria-expanded", "true");
      position();
      const eligible = availableOptions(select.options);
      highlight(
        eligible.some((item) => item.index === select.selectedIndex)
          ? select.selectedIndex
          : (eligible[0]?.index ?? -1),
      );
    }
    button.addEventListener("click", () =>
      opened === control ? close() : open(),
    );
    button.addEventListener("focus", sync);
    button.addEventListener("keydown", (event) => {
      if (["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) {
        event.preventDefault();
        if (opened !== control) open();
        else highlight(nextOption(select.options, active, event.key));
        if (["Home", "End"].includes(event.key))
          highlight(nextOption(select.options, active, event.key));
      } else if (["Enter", " "].includes(event.key)) {
        event.preventDefault();
        if (opened === control) commit(active);
        else open();
      } else if (event.key === "Escape" && opened === control) {
        event.preventDefault();
        event.stopPropagation();
        close();
      } else if (event.key === "Tab") close(false);
      else if (
        event.key.length === 1 &&
        !event.ctrlKey &&
        !event.metaKey &&
        !event.altKey
      ) {
        event.preventDefault();
        if (opened !== control) open();
        const now = Date.now();
        search = now - searchedAt > 700 ? event.key : search + event.key;
        searchedAt = now;
        highlight(findOption(select.options, active, search));
      }
    });
    function invalid(event) {
      event.preventDefault();
      setText(error, uiText(select.validationMessage));
      error.hidden = false;
      sync();
      button.focus();
    }
    function nativeFocus() {
      button.focus({ preventScroll: true });
    }
    select.addEventListener("invalid", invalid);
    select.addEventListener("focus", nativeFocus);
    const valueDescriptors = new Map();
    const control = {
      sync,
      close,
      position,
      wrapper,
      list,
      destroy() {
        close(false);
        list.remove();
        select.removeEventListener("invalid", invalid);
        select.removeEventListener("focus", nativeFocus);
        select.classList.remove("select-native");
        for (const [key, descriptor] of valueDescriptors) {
          if (descriptor) Object.defineProperty(select, key, descriptor);
          else delete select[key];
        }
        if (originalTab === null) select.removeAttribute("tabindex");
        else select.setAttribute("tabindex", originalTab);
        if (originalAria === null) select.removeAttribute("aria-hidden");
        else select.setAttribute("aria-hidden", originalAria);
        wrapper.replaceWith(select);
      },
    };
    controls.set(select, control);
    // Scope projections to these instances; do not patch DOM prototypes or emit
    // change events for API-driven updates.
    for (const key of ["value", "selectedIndex"]) {
      const native = Object.getOwnPropertyDescriptor(
        window.HTMLSelectElement.prototype,
        key,
      );
      valueDescriptors.set(key, Object.getOwnPropertyDescriptor(select, key));
      Object.defineProperty(select, key, {
        configurable: true,
        get() {
          return native.get.call(this);
        },
        set(value) {
          native.set.call(this, value);
          sync();
        },
      });
    }
    sync();
  }
  function refresh() {
    for (const select of root.querySelectorAll("select")) attach(select);
    for (const [select, control] of controls) {
      if (!root.contains(select)) {
        control.destroy();
        controls.delete(select);
      } else control.sync();
    }
  }
  function outside(event) {
    if (
      opened &&
      !opened.wrapper.contains(event.target) &&
      !opened.list.contains(event.target)
    )
      opened.close(false);
  }
  function reposition() {
    opened?.position();
  }
  const observer = new window.MutationObserver((records) => {
    if (
      records.some(
        (record) =>
          !record.target.closest?.(
            ".select-options,.select-trigger,.select-error",
          ),
      )
    )
      refresh();
  });
  refresh();
  observer.observe(root, {
    subtree: true,
    childList: true,
    characterData: true,
    attributes: true,
    attributeFilter: [
      "disabled",
      "required",
      "selected",
      "hidden",
      "label",
      "aria-label",
      "aria-invalid",
      "aria-describedby",
    ],
  });
  root.addEventListener("pointerdown", outside);
  root.addEventListener("change", refresh);
  function reset() {
    window.setTimeout(refresh, 0);
  }
  root.addEventListener("reset", reset);
  window.addEventListener("resize", reposition);
  document.addEventListener("scroll", reposition, true);
  return {
    refresh,
    destroy() {
      observer.disconnect();
      root.removeEventListener("pointerdown", outside);
      root.removeEventListener("change", refresh);
      root.removeEventListener("reset", reset);
      window.removeEventListener("resize", reposition);
      document.removeEventListener("scroll", reposition, true);
      for (const control of controls.values()) control.destroy();
      controls.clear();
    },
  };
}
