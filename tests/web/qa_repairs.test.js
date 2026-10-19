import test from "node:test";
import assert from "node:assert/strict";
import { expireNotice, clearNoticeTimer, NOTICE_DURATION } from "../../src/fiber_bundle/web/notice_lifetime.js";
import { configurationIssues, channelIssuesText } from "../../src/fiber_bundle/web/channel_validation.js";
import { channelErrorText } from "../../src/fiber_bundle/web/channel_api.js";
import { vaultCatalogErrorText } from "../../src/fiber_bundle/web/vault_catalog_errors.js";

test("notice expires, replacement owns its lifetime, and repeated polling cannot extend it", () => {
  const originalSet=globalThis.setTimeout, originalClear=globalThis.clearTimeout;
  const callbacks=new Map();let serial=0;
  globalThis.setTimeout=(work,delay)=>{const id=++serial;callbacks.set(id,{work,delay});return id;};
  globalThis.clearTimeout=()=>{}; // Deliberately deliver stale callbacks to exercise ownership.
  const node={textContent:"first",hidden:false,dataset:{},className:""};
  try {
    expireNotice(node,false);const first=serial;
    node.textContent="failure";expireNotice(node,true);const second=serial;
    assert.equal(callbacks.get(first).delay,NOTICE_DURATION.success);
    assert.equal(callbacks.get(second).delay,NOTICE_DURATION.error);
    callbacks.get(first).work();assert.equal(node.textContent,"failure");
    expireNotice(node,true,false);assert.equal(serial,second);
    callbacks.get(second).work();assert.equal(node.hidden,true);assert.equal(node.textContent,"");
    node.textContent="later";expireNotice(node,true);clearNoticeTimer(node);callbacks.get(serial).work();assert.equal(node.textContent,"later");
  } finally {globalThis.setTimeout=originalSet;globalThis.clearTimeout=originalClear;}
});

test("expert rules name every missing permission and quota without requiring optional receipts", () => {
 const value={expert_enabled:true,platform:"feishu",original:false,add:false,expert_limit:0};
 const issues=configurationIssues(value);
 assert.deepEqual(issues,["expert_original_required","expert_add_required","expert_limit_required"]);
 const text=channelIssuesText(issues);assert.match(text,/允许取回原件/);assert.match(text,/允许入库/);assert.match(text,/专家指令上限/);
 assert.deepEqual(configurationIssues({...value,original:true,add:true,expert_limit:1,expert_receipt_limit:0}),[]);
 assert.equal(channelIssuesText(["untrusted_secret_value"]),"");
});

test("public errors name recovery settings and preserve runtime failures as runtime failures", () => {
 assert.match(channelErrorText("channel_configuration_invalid",["query_limit_required"]),/最多接收查询/);
 assert.match(channelErrorText("intake_article_extraction_disabled"),/正文及原图/);
 assert.match(vaultCatalogErrorText("vault_trash_helper_required"),/启动器/);
 assert.match(vaultCatalogErrorText("vault_file_size_limit"),/排除范围/);
 assert.equal(vaultCatalogErrorText("private-file-name"),undefined);
});
