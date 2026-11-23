"""Link real HTTP-served ESM graphs for both renderers without running a browser."""

import json
import posixpath
import re
import shutil
import subprocess

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.responses import Response

from fiber_bundle.auth import Denied
from fiber_bundle.web_routes import router

IMPORT = re.compile(r"""(?:\bfrom\s*|\bimport\s*\(?\s*)["'](\.[^"']+)["']""")
LINK = r"""
import vm from 'node:vm';
import {readFileSync} from 'node:fs';
const {roots,sources}=JSON.parse(readFileSync(0,'utf8'));
const modules=new Map();
function load(name) {
  if (!Object.hasOwn(sources,name)) throw Error('unserved_module:'+name);
  if (!modules.has(name))
    modules.set(name,new vm.SourceTextModule(sources[name],{identifier:name}));
  return modules.get(name);
}
async function link(name) {
  const module=load(name);
  if (module.status==='unlinked') await module.link((specifier,parent)=>{
    const target=new URL(specifier,'http://synthetic/static/'+parent.identifier);
    if (target.origin!=='http://synthetic'||!target.pathname.startsWith('/static/'))
      throw Error('unexpected_module_origin:'+specifier);
    return load(target.pathname.slice('/static/'.length));
  });
}
for (const root of roots) await link(root);
// Literal lazy imports belong to the same shipped dependency graph. Link their
// real exports too; do not replace dependencies with permissive export stubs.
for (const name of Object.keys(sources)) await link(name);
console.log(JSON.stringify({roots,linked:modules.size}));
"""


def test_both_renderer_startup_graphs_are_http_served_and_real_esm_exports_link():
    app = FastAPI()
    app.include_router(router())

    @app.exception_handler(Denied)
    async def denied(request, error):
        return Response(status_code=error.status)

    roots = ["ui_entry.js", "app.js", "atlas/main.js"]
    pending, sources = list(roots), {}
    with TestClient(app) as client:
        assert "/static/ui_entry.js" in client.get("/").text
        while pending:
            name = pending.pop()
            if name in sources:
                continue
            response = client.get("/static/" + name)
            assert response.status_code == 200, f"Startup dependency is not served: {name}"
            assert response.headers["content-type"].startswith("text/javascript"), name
            sources[name] = response.text
            for dependency in IMPORT.findall(response.text):
                target = posixpath.normpath(posixpath.join(posixpath.dirname(name), dependency))
                assert not target.startswith("../"), target
                pending.append(target)
        # The allowlist stays closed even though the full UI graph is available.
        for name in ["config.test.json", "../auth.py", "i18n/not-packaged.js"]:
            assert client.get("/static/" + name).status_code == 404
    assert {
        "organization_progress_view.js",
        "note_cloud.js",
        "note_cloud_explorer.js",
        "note_cloud_model.js",
        "note_cloud_presentation.js",
        "note_cloud_copy.js",
        "i18n/note_cloud.js",
        "atlas/view_note_cloud.js",
        "organization_diagnostics.js",
        "vault_index_details.js",
        "i18n/organization_reliability.js",
        "organization_candidate_coverage.js",
        "organization_task_record.js",
        "i18n/organization_visibility.js",
    } <= sources.keys()
    node = shutil.which("node")
    assert node, "Node is required to validate the shipped ESM export contracts"
    result = subprocess.run(
        [node, "--experimental-vm-modules", "--input-type=module", "-e", LINK],
        input=json.dumps({"roots": roots, "sources": sources}),
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["linked"] == len(sources)
