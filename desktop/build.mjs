// Build a local macOS candidate from fixed Electron; include only the desktop shell.
import { copyFile, mkdir, stat } from "node:fs/promises";
import { createRequire } from "node:module";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { packager } from "@electron/packager";
import { buildIcon } from "./build_icon.mjs";
const require = createRequire(import.meta.url);
if (process.platform !== "darwin")
  throw new Error("macOS candidate build requires macOS");
const index = process.argv.indexOf("--out");
if (index < 0 || !process.argv[index + 1])
  throw new Error("Specify a fresh --out directory");
const output = resolve(process.argv[index + 1]);
try {
  await stat(output);
  throw new Error("Output already exists");
} catch (error) {
  if (error.code !== "ENOENT") throw error;
}
await mkdir(output, { mode: 0o700 });
const icon = resolve(output, "FiberBundle.icns");
buildIcon(
  resolve(
    dirname(fileURLToPath(import.meta.url)),
    "../src/fiber_bundle/web/logo-original.png",
  ),
  icon,
);
const version = require("electron/package.json").version;
// Web assets are authoritative; generated offline copies carry the same fonts/logo.
for (const name of [
  "tokens.css",
  "themes.css",
  "fonts.css",
  "dm-sans.ttf",
  "noto-sans-sc.ttf",
  "dm-sans-OFL.txt",
  "noto-sans-sc-OFL.txt",
  "font-sources.json",
  "logo-original.png",
])
  await copyFile(
    resolve(
      dirname(fileURLToPath(import.meta.url)),
      "../src/fiber_bundle/web/",
      name,
    ),
    resolve(dirname(fileURLToPath(import.meta.url)), name),
  );
const paths = await packager({
  dir: dirname(fileURLToPath(import.meta.url)),
  out: output,
  name: "Fiber bundle",
  appBundleId: "local.fiber.bundle",
  icon,
  platform: "darwin",
  arch: process.arch,
  electronVersion: version,
  asar: false,
  prune: true,
  overwrite: false,
  download: {
    cacheRoot:
      process.env.electron_config_cache || "/private/tmp/fiber-electron-cache",
  },
  ignore: (path) =>
    [
      "/build.mjs",
      "/build_icon.mjs",
      "/cloud-status.swift",
      "/vault-trash.swift",
      "/release.py",
      "/verify_release.py",
    ].includes(path) ||
    path === "/runtime-licenses" ||
    path.startsWith("/runtime-licenses/"),
});
console.log(
  JSON.stringify({
    state: "candidate",
    bundle: paths[0] + "/Fiber bundle.app",
    electron: version,
    architecture: process.arch,
    developer_id_signed: false,
    notarized: false,
  }),
);
