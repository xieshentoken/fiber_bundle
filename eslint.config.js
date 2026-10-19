import js from "@eslint/js";

export default [
  {
    ignores: [
      "docs/**",
      "build/**",
      "dist/**",
      ".venv/**",
      "node_modules/**",
      "src/fiber_bundle/web/graph_renderer.bundle.js",
      "src/fiber_bundle/web/graph_force_worker.js",
    ],
  },
  js.configs.recommended,
  {
    files: ["desktop/preload.cjs"],
    languageOptions: {
      sourceType: "commonjs",
      globals: { require: "readonly" },
    },
  },
  {
    files: [
      "src/fiber_bundle/executor/*.mjs",
      "src/fiber_bundle/creation_executor/*.mjs",
      "desktop/*.mjs",
      "frontend/graph/*.mjs",
      "tests/desktop/*.js",
    ],
    languageOptions: {
      globals: {
        process: "readonly",
        Buffer: "readonly",
        fetch: "readonly",
        console: "readonly",
        URL: "readonly",
        URLSearchParams: "readonly",
      },
    },
  },
  {
    files: [
      "src/fiber_bundle/web/*.js",
      "src/fiber_bundle/web/i18n/*.js",
      "frontend/graph/*.js",
      "tests/web/*.js",
      "desktop/offline.js",
    ],
    languageOptions: {
      ecmaVersion: "latest",
      sourceType: "module",
      globals: Object.fromEntries(
        [
          "document",
          "window",
          "fetch",
          "setInterval",
          "setTimeout",
          "clearTimeout",
          "AbortController",
          "URL",
          "console",
          "Blob",
          "Worker",
          "ResizeObserver",
          "requestAnimationFrame",
          "cancelAnimationFrame",
          "clearInterval",
          "URLSearchParams",
          "performance",
        ].map((name) => [name, "readonly"]),
      ),
    },
  },
];
