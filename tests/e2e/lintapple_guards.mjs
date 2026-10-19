import assert from "node:assert/strict";

// Independently verify the contracts covered by narrowly scoped lint exceptions.
export async function checkContracts(page) {
  const issues = await page.evaluate(() => {
    const issues = [];
    const visible = (node) =>
      node.checkVisibility() && node.getBoundingClientRect().width > 0;
    for (const button of document.querySelectorAll(".sidebar nav > button")) {
      if (visible(button) && button.getBoundingClientRect().height < 43.9)
        issues.push("Navigation target below 44px: " + button.dataset.tab);
    }
    for (const trigger of document.querySelectorAll("[aria-controls]")) {
      for (const id of trigger.getAttribute("aria-controls").split(/\s+/)) {
        if (document.querySelectorAll("#" + CSS.escape(id)).length !== 1)
          issues.push("Missing or duplicate aria-controls target: " + id);
      }
      if (trigger.matches(".select-trigger")) {
        const target = document.getElementById(
          trigger.getAttribute("aria-controls"),
        );
        if (
          target?.getAttribute("role") !== "listbox" ||
          (trigger.getAttribute("aria-expanded") === "true") !== !target.hidden
        )
          issues.push("Combobox role/state mismatch");
      }
      if (trigger.getAttribute("aria-controls") === "theme-picker") {
        const dialog = document.getElementById("theme-picker");
        // Both launchers describe the same modal, but only its opener is expanded.
        if (
          !(dialog instanceof HTMLDialogElement) ||
          (!dialog.open && trigger.getAttribute("aria-expanded") !== "false")
        )
          issues.push("Theme dialog target/state mismatch");
      }
    }
    if (document.body.dataset.sidebar === "rail") {
      for (const label of document.querySelectorAll(
        ".sidebar nav .nav-label",
      )) {
        if (!label.closest("button").checkVisibility()) continue;
        const box = label.getBoundingClientRect();
        if (
          box.width !== 1 ||
          box.height !== 1 ||
          getComputedStyle(label).clipPath !== "inset(50%)"
        )
          issues.push("Unexpected rail label clipping");
      }
    }
    const headings =
      "#vaults-panel .section-heading h2, #clients-panel .section-heading h2, #documents-panel .section-heading h2, #media-panel > h2, #models-panel > h2, #backend-panel > h2, #backups-panel > h2";
    for (const heading of document.querySelectorAll(headings)) {
      if (heading.closest("section[hidden]")) continue;
      const box = heading.getBoundingClientRect(),
        style = getComputedStyle(heading);
      if (
        box.width !== 1 ||
        box.height !== 1 ||
        style.clipPath !== "inset(50%)"
      )
        issues.push(
          "Unexpected hidden heading contract: " + heading.textContent.trim(),
        );
      if (![...document.querySelectorAll("h1")].some(visible))
        issues.push("No visible page heading");
    }
    const rgb = (value) => value.match(/[\d.]+/g)?.map(Number);
    const luminance = (color) =>
      color
        .slice(0, 3)
        .map((v) => {
          v /= 255;
          return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
        })
        .reduce((sum, v, i) => sum + v * [0.2126, 0.7152, 0.0722][i], 0);
    for (const node of document.querySelectorAll(
      ".usage-chart-scroll .chart-label, .cost-chart .axis, .sidebar .nav-heading, .sidebar nav .nav-label",
    )) {
      if (!visible(node)) continue;
      const style = getComputedStyle(node);
      let parent = node,
        background;
      while (parent) {
        const color = rgb(getComputedStyle(parent).backgroundColor);
        if (color?.length >= 3 && (color.length === 3 || color[3] === 1)) {
          background = color;
          break;
        }
        parent = parent.parentElement;
      }
      const foreground = rgb(
        node instanceof SVGElement ? style.fill : style.color,
      );
      if (!foreground || !background) {
        issues.push("Unresolved companion contrast");
        continue;
      }
      const a = luminance(foreground),
        b = luminance(background);
      const contrast = (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
      if (contrast < 4.5)
        issues.push("Companion contrast below 4.5: " + contrast.toFixed(2));
    }
    for (const node of document.querySelectorAll(".usage-numeric")) {
      if (!visible(node)) continue;
      const style = getComputedStyle(node);
      if (
        !["end", "right"].includes(style.textAlign) ||
        !style.fontVariantNumeric.includes("tabular-nums")
      )
        issues.push("Numeric table alignment mismatch");
    }
    for (const button of document.querySelectorAll(
      ".sidebar [data-tab], #sidebar-toggle, #theme-toggle",
    )) {
      const tooltip = document.getElementById(
        button.getAttribute("aria-describedby"),
      );
      if (
        !tooltip ||
        tooltip.getAttribute("role") !== "tooltip" ||
        tooltip.textContent !== button.getAttribute("aria-label")
      )
        issues.push("Tooltip label mismatch");
    }
    return issues;
  });
  assert.deepEqual(issues, []);
}

export async function checkInteractions(page) {
  const opener = page.locator("#theme-toggle");
  await opener.click();
  const dialog = page.locator("#theme-picker");
  await dialog.waitFor({ state: "visible" });
  assert.equal(await opener.getAttribute("aria-expanded"), "true");
  for (const key of ["Tab", "Shift+Tab"]) {
    await page.keyboard.press(key);
    assert(
      await dialog.evaluate((node) => node.contains(document.activeElement)),
    );
  }
  await page.keyboard.press("Escape");
  await dialog.waitFor({ state: "hidden" });
  await page.waitForFunction(
    () =>
      document.querySelector("#theme-toggle").getAttribute("aria-expanded") ===
      "false",
  );
  assert.equal(await opener.getAttribute("aria-expanded"), "false");
  assert(await opener.evaluate((node) => node === document.activeElement));
  const combo = page.locator(".select-trigger:visible:not(:disabled)").first();
  if (await combo.count()) {
    const value = await combo.textContent();
    const target = page.locator(
      "#" + (await combo.getAttribute("aria-controls")),
    );
    await combo.focus();
    await page.keyboard.press("ArrowDown");
    await target.waitFor({ state: "visible" });
    assert.equal(await combo.getAttribute("aria-expanded"), "true");
    await page.keyboard.press("Escape");
    await target.waitFor({ state: "hidden" });
    assert.equal(await combo.textContent(), value);
    assert(await combo.evaluate((node) => node === document.activeElement));
  }
  for (const selector of ["#sidebar-toggle", "#theme-toggle"]) {
    const toggle = page.locator(selector);
    if (await toggle.isVisible()) {
      await toggle.focus();
      const tooltip = page.locator(
        "#" + (await toggle.getAttribute("aria-describedby")),
      );
      await tooltip.waitFor({ state: "visible" });
      assert.equal(
        await tooltip.textContent(),
        await toggle.getAttribute("aria-label"),
      );
      await toggle.blur();
    }
  }
}
