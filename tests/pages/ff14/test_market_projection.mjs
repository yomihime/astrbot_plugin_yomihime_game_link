// Registered HostRuntime/Core handler projection -> validator and synthetic DOM.
// Upstream transport is fixture data; no real Host process or network acceptance.
import assert from "node:assert/strict";
import {execFileSync} from "node:child_process";
import {fileURLToPath} from "node:url";
import {validateQueryResult} from "../../../modules/ff14/pages/src/query-contract.js";
import {queryHarness, edit, submitFor, flush} from "./test_ui_a.mjs";
const root = fileURLToPath(new URL("../../../", import.meta.url));
const samples = JSON.parse(execFileSync(process.env.YGL_TEST_PYTHON || "python", ["-B", "-m", "tests.fixtures.ff14.market_public"], {
  cwd: root, encoding: "utf8", timeout: 60000, env: {...process.env, PYTHONIOENCODING: "utf-8"},
}));
let passed = 0;
for (const [name, sample] of Object.entries(samples)) {
  const clean = validateQueryResult(sample); assert.equal(clean.status, sample.status);
  const h = await queryHarness("market");
  try {
    await edit(h, "query", "44091"); await submitFor(h).click(); await flush(); h.bridge.posts[0].resolve(sample); await flush();
    assert.equal(h.root.querySelector('.ff14-result').getAttribute('data-result-status'), sample.status, name);
    if (sample.document) {
      for (const b of sample.document.blocks.filter((b) => b.kind === "text")) assert.ok(h.root.textContent.includes(b.text), `${name}: original text`);
      for (const s of sample.document.sources) assert.ok(h.root.textContent.includes(s));
    }
    for (const warning of sample.warnings) assert.ok(h.root.textContent.includes(warning));
    if (name === "cache") assert.match(h.root.textContent, /缓存结果/);
    if (name === "partial") assert.match(h.root.textContent, /Japan.*获取失败/);
    if (name === "empty") assert.match(h.root.textContent, /没有可展示记录/);
    if (name === "selection") {
      const selection = sample.model_facts.selection, buttons = h.root.all((n) => n.getAttribute("data-item-id") !== null);
      assert.equal(buttons.length, selection.candidates.length); await buttons[0].click(); await flush();
      assert.deepEqual(JSON.parse(h.bridge.posts[1].parameters.input), {selection: {batch_id: selection.batch_id, generation: selection.generation, item_id: selection.candidates[0].item_id}});
    }
  } finally { h.app.destroy(); }
  passed++;
}
console.log(JSON.stringify({kind: "isolated-registered-handler-projection-and-synthetic-DOM", passed}));
