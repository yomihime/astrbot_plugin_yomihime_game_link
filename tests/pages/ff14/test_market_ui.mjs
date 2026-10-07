// Synthetic bridge/state tests; no browser, real authentication or upstream requests.
import assert from "node:assert/strict";
import {queryInput, validateQueryResult} from "../../../modules/ff14/pages/src/query-contract.js";
import {queryHarness, edit, submitFor, flush} from "./test_ui_a.mjs";
import {marketSample} from "./market_fixtures.mjs";
const cases = [];
const check = (name, run) => cases.push({name, run});
const candidates = (h) => h.root.all((n) => n.getAttribute("data-item-id") !== null);
async function query(h, sample) { await submitFor(h).click(); await flush(); h.bridge.posts.at(-1).resolve(sample); await flush(); }
check("market new query leaves scope resolution to server and bounds public parameters", () => {
  assert.deepEqual(queryInput("market", {query: "牛排"}).body, {query: "牛排", quality: "all", intent: "overview"});
  assert.deepEqual(queryInput("market", {query: "44091", server: "74", dc: "", region: "global", quality: "hq", intent: "min"}).body,
    {query: "44091", server: "74", region: "global", quality: "hq", intent: "min"});
  assert.ok(queryInput("market", {query: "", quality: "bad", intent: "bad", region: "bad"}).errors.query);
  assert.ok(queryInput("market", {query: "牛排", server: "a".repeat(101)}).errors.server);
});
check("market preserves document source rows warnings coverage cache time and truncation", async () => {
  const h = await queryHarness("market"); await edit(h, "query", "牛排"); assert.equal(h.bridge.posts.length, 0);
  for (const name of ["success", "partial", "cache", "nodata", "longrows"]) {
    const sample = marketSample(name); await query(h, sample);
    assert.equal(h.bridge.posts.at(-1).capability, "ff14.market.query");
    assert.deepEqual(JSON.parse(h.bridge.posts.at(-1).parameters.input), {query: "牛排", quality: "all", intent: "overview"});
    assert.ok(h.root.textContent.includes(sample.document.title)); assert.match(h.root.textContent, /当前解析范围.*服务端默认.*品质.*全部品质.*意图.*概览/);
    for (const warning of sample.warnings) assert.ok(h.root.textContent.includes(warning));
    assert.match(h.root.textContent, /来源获取时间：2026-10-05T00:00:00/);
    if (name === "cache") assert.match(h.root.textContent, /缓存结果/);
    if (name === "partial") assert.match(h.root.textContent, /获取失败.*缓存状态未知/);
    if (name === "longrows") assert.match(h.root.textContent, /结果已截断/);
  }
  h.app.destroy();
});
check("candidate continuation sends only ticket and ID and keeps keyboard focus reachable", async () => {
  const h = await queryHarness("market"); await edit(h, "query", "牛排"); await query(h, marketSample("needsselection"));
  const button = candidates(h)[0]; button.focus(); await button.press("Enter"); await flush();
  assert.deepEqual(JSON.parse(h.bridge.posts.at(-1).parameters.input), {selection: {batch_id: "fixture-batch-1", generation: "fixture-generation-1", item_id: 44091}});
  assert.equal(h.document.activeElement, h.root.all((n) => n.id === "ff14-market-query")[0]); assert.equal(submitFor(h).disabled, true);
  await button.click(); await submitFor(h).click(); await flush(); assert.equal(h.bridge.posts.length, 2);
  h.bridge.posts.at(-1).resolve(marketSample()); await flush(); assert.equal(candidates(h).length, 0); h.app.destroy();
});
check("editing new query fences old candidate buttons and late result cannot revive them", async () => {
  const h = await queryHarness("market"); await edit(h, "query", "牛排"); await query(h, marketSample("needsselection"));
  const old = candidates(h)[0]; await edit(h, "query", "新物品"); await old.click(); await flush(); assert.equal(h.bridge.posts.length, 1);
  await submitFor(h).click(); await flush(); const late = h.bridge.posts.at(-1); await edit(h, "query", "再改物品");
  late.resolve(marketSample("needsselection")); await flush(); assert.equal(candidates(h).length, 0); assert.equal(h.root.querySelectorAll("[data-item-id]").length, 0); h.app.destroy();
});
check("market errors timeout manual retry duplicate suppression stop and late response", async () => {
  const h = await queryHarness("market"); await edit(h, "query", "牛排");
  for (const code of ["error", "no_records", "parameter_error"]) {
    await query(h, marketSample(code)); assert.match(submitFor(h).textContent,/市场/);assert.equal(submitFor(h).disabled,false);
    assert.doesNotMatch(h.root.textContent, /DO_NOT_RENDER_PRIVATE/);
    assert.ok(h.root.all((n) => n.getAttribute("role") === "alert").length);
  }
  await submitFor(h).click(); await flush(); const timed = h.bridge.posts.at(-1); h.clock.fire();
  await flush();assert.match(h.root.textContent,/等待查询超过/); assert.match(submitFor(h).textContent,/市场/);assert.equal(submitFor(h).disabled,false);
  timed.resolve(marketSample()); await flush(); assert.equal(h.root.querySelectorAll("[data-item-id]").length, 0);
  await submitFor(h).click(); await flush(); const late = h.bridge.posts.at(-1), count = h.bridge.posts.length;
  await submitFor(h).click(); await flush(); assert.equal(h.bridge.posts.length, count);
  await h.root.all((n) => n.tagName.toLowerCase() === "button" && n.textContent === "停止展示")[0].click(); late.resolve(marketSample()); await flush();
  assert.equal(h.root.querySelectorAll("[data-item-id]").length, 0); assert.equal(submitFor(h).textContent, "查询市场"); h.app.destroy();
});
check("market metadata retains only whitelist and rejects oversized/unsafe candidates", () => {
  const sample = marketSample("needsselection"); sample.model_facts.secret = "DO_NOT_RENDER_PRIVATE";
  sample.model_facts.selection.owner = "DO_NOT_RENDER_PRIVATE";
  assert.doesNotMatch(JSON.stringify(validateQueryResult(sample)), /DO_NOT_RENDER_PRIVATE/);
  sample.model_facts.selection.candidates = Array.from({length: 7}, (_, i) => ({item_id: i + 1, name: "物品"}));
  assert.throws(() => validateQueryResult(sample), /invalid_query_result/);
});
check("business module mounts without implicit requests or credential editor",async()=>{
 const h=await queryHarness("market");try{await flush();assert.equal(h.bridge.posts.length,0);assert.equal(h.root.querySelectorAll('input[type=password]').length,0);assert.equal(h.root.querySelectorAll('[data-action=credential-save]').length,0);}finally{h.app.destroy();}
});

for (const {run} of cases) await run();
console.log(JSON.stringify({kind: "synthetic-market-state-and-DOM", passed: cases.length, tests: cases.map((c) => c.name)}, null, 2));
