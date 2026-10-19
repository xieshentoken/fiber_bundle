// Explicit opt-in isolated fixture. Never reads a production session or real Key.
const {chromium, _electron} = require("playwright");
const assert = require("node:assert/strict"), fs = require("node:fs"), path = require("node:path");
const root = path.resolve(__dirname,"../.."), base = process.env.FIBER_GUI_ACCEPTANCE_URL, out = process.env.FIBER_GUI_EVIDENCE;
assert(new URL(base).hostname === "127.0.0.1" && +new URL(base).port >= 18000);
fs.mkdirSync(out,{recursive:true});
const result = {checks:[],errors:[],synthetic:true,real_provider:false};
const runId = Date.now().toString();
async function run(page, kind) {
  page.on("pageerror",e=>result.errors.push(e.message));
  if(kind === "web") await page.goto(base);
  await page.locator("#password").fill("synthetic-model-ui");
  await page.locator("#login-form button").click();
  await page.locator("#workspace").waitFor({state:"visible"});
  await page.locator('[data-tab="models"]').click();
  await page.locator("#models-add").waitFor({state:"visible"});
  await page.waitForFunction(()=>!document.querySelector("#page-loading")?.offsetHeight);
  for (const theme of ["cursor","oracle","tesla"]) {
    await page.locator("#theme-toggle").click();
    await page.locator(`[data-theme-choice="${theme}"]`).click();
    await page.locator("#models-add").click();
    await page.locator("#model-wizard").waitFor({state:"visible"});
    assert.equal(await page.locator("#model-wizard-base_url").isVisible(),false);
    await page.locator("#model-wizard-name").fill(`Synthetic ${kind} ${theme} ${runId}`);
    await page.locator("#model-wizard-api_key").fill(`synthetic-${kind}-${theme}-${runId}`);
    await page.route("**/admin/models/discover",route=>route.fulfill({json:{models:[{id:"gpt-5",efforts:["default","low","medium","high"]}]}}));
    await page.getByRole("button",{name:"读取可用模型",exact:true}).click();
    await page.locator("#model-wizard-model").selectOption("gpt-5");
    await page.locator("#model-wizard-effort").selectOption("high");
    await page.screenshot({path:path.join(out,`${kind}-${theme}-wizard.png`)});
    await page.getByRole("button",{name:"保存模型",exact:true}).click();
    await page.locator("#model-wizard").waitFor({state:"hidden"});
    const card=page.locator(".model-card").filter({hasText:`Synthetic ${kind} ${theme} ${runId}`});
    await card.waitFor();
    await card.getByRole("button",{name:"设为默认",exact:true}).click();
    await page.locator(".model-card").filter({hasText:`Synthetic ${kind} ${theme} ${runId}`}).getByText("应用默认",{exact:true}).waitFor();
    result.checks.push(`${kind}/${theme}: preset save effort/default`);
    await page.unroute("**/admin/models/discover");
  }
  let dialogs=0;
  page.on("dialog",async d=>{dialogs++;await d.accept();});
  await page.locator('[data-tab="home"]').click();
  assert.equal(dialogs,0,"saved wizard must not warn about drafts");
  await page.locator('[data-tab="models"]').click();
  await page.locator("#models-add").click();
  await page.keyboard.press("Escape");
  assert.equal(await page.locator("#models-add").evaluate(n=>n===document.activeElement),true);
  await page.locator("#models-add").click();
  await page.locator("#model-wizard-provider").selectOption("local");
  await page.locator("#model-wizard-name").fill(`Synthetic local ${kind} ${runId}`);
  await page.locator("#model-wizard-api_key").fill(`synthetic-local-${kind}-${runId}`);
  await page.locator("#model-wizard-base_url").fill("http://127.0.0.1:18852/v1");
  await page.getByRole("button",{name:"读取可用模型",exact:true}).click();
  await page.locator("#model-wizard-model").selectOption("synthetic/model:1");
  assert.equal(await page.locator("#model-wizard-effort").isVisible(),false);
  await page.getByRole("button",{name:"保存模型",exact:true}).click();
  await page.locator("#model-wizard").waitFor({state:"hidden"});
  await page.locator(".model-card").filter({hasText:`Synthetic local ${kind} ${runId}`}).getByRole("button",{name:"测试调用（消耗 Tokens）",exact:true}).click();
  await page.locator(".model-card").filter({hasText:`Synthetic local ${kind} ${runId}`}).getByText("调用验证通过（当前配置）",{exact:true}).waitFor({timeout:25000});
  await page.locator("#models-add").click();
  await page.locator("#model-wizard-provider").selectOption("custom");
  await page.locator("#model-wizard-name").fill("keep failed draft");
  await page.locator("#model-wizard-base_url").fill("https://unavailable.invalid/v1");
  await page.locator("#model-wizard-api_key").fill("synthetic-failed-key");
  await page.getByRole("button",{name:"读取可用模型",exact:true}).click();
  await page.locator("#model-wizard-error").waitFor({state:"visible"});
  assert.equal(await page.locator("#model-wizard-name").inputValue(),"keep failed draft");
  if(kind==="web") await page.setViewportSize({width:390,height:844});
  await page.screenshot({path:path.join(out,`${kind}-narrow-error.png`)});
  assert(await page.locator("#model-wizard").evaluate(n=>n.scrollWidth <= n.clientWidth+1));
  await page.keyboard.press("Escape");
  assert.equal(await page.locator("#model-wizard-api_key").inputValue(),"");
  result.checks.push(`${kind}: real loopback discovery/test, no dirty warning, Esc focus, failed draft, modal reflow`);
}
(async()=>{
  const browser=await chromium.launch({headless:true});
  try{await run(await browser.newPage({viewport:{width:1180,height:900}}),"web");}finally{await browser.close();}
  const app=await _electron.launch({executablePath:process.env.FIBER_ELECTRON_EXECUTABLE || path.join(root,"node_modules/electron/dist/Electron.app/Contents/MacOS/Electron"),args:[process.env.FIBER_ELECTRON_APP || path.join(root,"desktop"),"--url",base,"--user-data",path.join(out,"profile-"+runId)],timeout:30000});
  try{await run(await app.firstWindow(),"electron");}finally{await app.close();}
  assert.deepEqual(result.errors,[]);
  fs.writeFileSync(path.join(out,"acceptance.json"),JSON.stringify(result,null,2));
  console.log(JSON.stringify(result));
})().catch(e=>{console.error(e);process.exit(1);});
