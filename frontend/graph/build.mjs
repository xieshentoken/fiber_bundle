import { build } from "esbuild";
import { readFile, writeFile } from "node:fs/promises";
await build({
  entryPoints: ["frontend/graph/renderer.js"],
  bundle: true,
  format: "esm",
  minify: true,
  target: "chrome120",
  outfile: "src/fiber_bundle/web/graph_renderer.bundle.js",
  legalComments: "inline",
});
await build({
  entryPoints: ["frontend/graph/force_worker.js"],
  bundle: true,
  format: "esm",
  minify: true,
  target: "chrome120",
  outfile: "src/fiber_bundle/web/graph_force_worker.js",
  legalComments: "inline",
});
const entries = [];
for (const name of [
  "sigma",
  "graphology",
  "graphology-layout-forceatlas2",
  "graphology-utils",
  "events",
]) {
  const pkg = JSON.parse(
    await readFile(`node_modules/${name}/package.json`, "utf8"),
  );
  let license;
  try {
    license = await readFile(`node_modules/${name}/LICENSE.txt`, "utf8");
  } catch {
    license = await readFile(`node_modules/${name}/LICENSE`, "utf8");
  }
  entries.push(`${name} ${pkg.version}\n${license}`);
}
await writeFile(
  "src/fiber_bundle/web/graph-library-licenses.txt",
  entries.join("\n\n---\n\n"),
);
console.log(
  "Built fixed offline Sigma/Graphology renderer and ForceAtlas2 Worker.",
);
