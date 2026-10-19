import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const web = readFileSync(
  new URL("../../src/fiber_bundle/web/tokens.css", import.meta.url),
  "utf8",
);
const desktop = readFileSync(
  new URL("../../desktop/tokens.css", import.meta.url),
  "utf8",
);
const themes = readFileSync(
  new URL("../../src/fiber_bundle/web/themes.css", import.meta.url),
  "utf8",
);
const lightThemes = ["cursor", "oracle", "tesla", "clay"];
const surfaces = [
  "--surface-base",
  "--surface-panel",
  "--surface-raised",
  "--surface-hover",
  "--surface-emphasis",
];

function declarations(block) {
  return Object.fromEntries(
    [...block.matchAll(/(--[\w-]+):\s*([^;]+);/g)].map((match) => [
      match[1],
      match[2].trim(),
    ]),
  );
}

function palette(theme) {
  const base = declarations(web.match(/:root\s*\{([^}]+)/)[1]);
  if (!theme) return base;
  const block = themes.match(
    new RegExp('\\[data-theme="' + theme + '"\\]\\s*\\{([^}]+)'),
  )?.[1];
  assert(block, theme);
  return { ...base, ...declarations(block) };
}

function color(tokens, name) {
  const value = tokens[name];
  assert(value, name);
  const reference = value.match(/^var\((--[\w-]+)\)$/);
  if (reference) return color(tokens, reference[1]);
  if (/^#[\da-f]{6}$/i.test(value))
    return [
      ...value
        .slice(1)
        .match(/../g)
        .map((part) => parseInt(part, 16)),
      1,
    ];
  const rgba = value.match(/^rgba?\(([^)]+)\)$/);
  assert(rgba, name + " has a supported color: " + value);
  const channels = rgba[1].split(",").map(Number);
  return channels.length === 3 ? [...channels, 1] : channels;
}

function composite(foreground, background) {
  const alpha = foreground[3];
  return foreground
    .slice(0, 3)
    .map((channel, index) => channel * alpha + background[index] * (1 - alpha));
}

function luminance(rgb) {
  const channels = rgb.map((channel) => {
    const value = channel / 255;
    return value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
  });
  return channels[0] * 0.2126 + channels[1] * 0.7152 + channels[2] * 0.0722;
}

function contrast(foreground, background) {
  const a = luminance(foreground),
    b = luminance(background);
  return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
}

function assertContrast(tokens, foreground, background, floor, context) {
  const foregroundColor = composite(color(tokens, foreground), background);
  const ratio = contrast(foregroundColor, background);
  assert(
    ratio >= floor,
    `${context} ${foreground}: ${ratio.toFixed(2)} < ${floor}`,
  );
}

test("web and desktop share byte-identical base tokens", () => {
  assert.equal(desktop, web);
});

test("status text meets 4.5 contrast after compositing its real surface", () => {
  for (const theme of [undefined, ...lightThemes, "dark"]) {
    const tokens = palette(theme);
    for (const surface of surfaces) {
      for (const [foreground, background] of [
        ["--error-text", "--error-soft"],
        ["--status-waiting", "--status-waiting-soft"],
        ["--status-connected", "--status-connected-soft"],
        ["--status-info", "--status-info-soft"],
      ]) {
        const backgroundColor = composite(
          color(tokens, background),
          color(tokens, surface),
        );
        assertContrast(
          tokens,
          foreground,
          backgroundColor,
          4.5,
          `${theme} ${surface}`,
        );
      }
    }
  }
});

// Accent-colored summaries appear on panels, hover rows and the page background.
test("theme accent text stays readable on its actual light surfaces", () => {
  for (const theme of lightThemes) {
    const tokens = palette(theme);
    for (const surface of [
      "--surface-base",
      "--surface-panel",
      "--surface-raised",
    ]) {
      assertContrast(
        tokens,
        "--accent",
        color(tokens, surface),
        4.5,
        `${theme} ${surface}`,
      );
    }
  }
});

test("Clay and Dark secondary text and focus remain readable", () => {
  for (const theme of ["clay", "dark"]) {
    const tokens = palette(theme);
    for (const surface of surfaces) {
      for (const foreground of ["--text", "--muted", "--focus"]) {
        assertContrast(
          tokens,
          foreground,
          color(tokens, surface),
          4.5,
          `${theme} ${surface}`,
        );
      }
    }
  }
});

test("Dark primary actions, status banner and inverted tooltips have readable text", () => {
  const tokens = palette("dark");
  for (const background of [
    "--accent",
    "--accent-hover",
    "--accent-active",
    "--danger",
    "--text",
  ]) {
    assertContrast(
      tokens,
      "--on-accent",
      color(tokens, background),
      4.5,
      background,
    );
  }
  for (const background of ["--nav-selected", "--nav-selected-hover"]) {
    assertContrast(
      tokens,
      "--nav-selected-ink",
      composite(color(tokens, background), color(tokens, "--surface-panel")),
      4.5,
      background,
    );
  }
});

test("Dark field boundaries and graph/chart marks remain visible", () => {
  const tokens = palette("dark");
  for (const surface of surfaces) {
    for (const foreground of [
      "--field-line",
      "--chart-calls",
      "--chart-cost",
      "--graph-explicit",
      "--graph-suggested",
      "--graph-accepted",
      "--graph-group",
      "--graph-node",
      "--graph-muted",
    ]) {
      assertContrast(tokens, foreground, color(tokens, surface), 3, surface);
    }
  }
});

test("each theme declares its native control scheme, without legacy palette rules", () => {
  for (const theme of [...lightThemes, "dark"]) {
    const block = themes.match(
      new RegExp('\\[data-theme="' + theme + '"\\]\\s*\\{([^}]+)'),
    )[1];
    assert.match(
      block,
      new RegExp(
        `color-scheme:\\s*${theme === "dark" ? "dark" : "light"}\\s*;`,
      ),
    );
  }
  assert.doesNotMatch(themes, /\[data-theme="(?:paper|green|gray)"\]/);
});
