// Build macOS icon representations from the same image used by the web shell.
import { execFileSync } from "node:child_process";
import { existsSync, mkdirSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

export function buildIcon(source, destination) {
  if (process.platform !== "darwin")
    throw new Error("macOS icon generation requires macOS");
  if (existsSync(destination)) throw new Error("Icon output already exists");
  const temporary = mkdtempSync(join(tmpdir(), "fiber-icon-"));
  const iconset = join(temporary, "FiberBundle.iconset");
  mkdirSync(iconset);
  try {
    for (const size of [16, 32, 128, 256, 512]) {
      for (const scale of [1, 2]) {
        const pixels = String(size * scale);
        const suffix = scale === 2 ? "@2x" : "";
        execFileSync(
          "sips",
          [
            "-z",
            pixels,
            pixels,
            source,
            "--out",
            join(iconset, `icon_${size}x${size}${suffix}.png`),
          ],
          { stdio: "pipe" },
        );
      }
    }
    execFileSync("iconutil", ["-c", "icns", "-o", destination, iconset], {
      stdio: "pipe",
    });
  } finally {
    rmSync(temporary, { recursive: true, force: true });
  }
}

if (
  process.argv[1] &&
  resolve(process.argv[1]) === fileURLToPath(import.meta.url)
) {
  if (process.argv.length !== 4)
    throw new Error("Usage: build_icon.mjs source.png output.icns");
  buildIcon(resolve(process.argv[2]), resolve(process.argv[3]));
}
