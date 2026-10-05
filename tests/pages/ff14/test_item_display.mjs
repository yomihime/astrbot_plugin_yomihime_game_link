// Offline Python product projection -> page validator and synthetic DOM.
// The Python source responses are synthetic; this is not live Host acceptance.
import assert from "node:assert/strict";
import {execFileSync} from "node:child_process";
import {fileURLToPath} from "node:url";
import {validateQueryResult} from "../../../pages/ff14/app.js";
import {queryHarness, edit, submitFor, flush} from "./test_ui_a.mjs";

const root = fileURLToPath(new URL("../../../", import.meta.url));
const python = process.env.YGL_TEST_PYTHON || "python";
const samples = JSON.parse(execFileSync(python, ["-B", "-m", "tests.fixtures.ff14.item_display"], {
  cwd: root, encoding: "utf8", timeout: 30000,
  env: {...process.env, PYTHONIOENCODING: "utf-8"},
}));
let passed = 0;
for (const [name, sample] of Object.entries(samples)) {
  const result = validateQueryResult(sample);
  assert.equal(result.status, "partial_success", name);
  assert.ok(result.document.blocks.length <= 32, name);
  const text = result.document.blocks.filter((v) => v.kind === "text").map((v) => v.text).join("\n");
  for (const warning of sample.warnings) assert.ok(text.includes(warning), `${name}: ${warning}`);
  assert.equal(result.document.blocks.at(-1).links.length, 2, name);
  const h = await queryHarness("items");
  try {
    await edit(h, "query", "90001"); await submitFor(h).click(); await flush();
    h.bridge.posts[0].resolve(sample); await flush();
    assert.equal(h.app.getState().query.result.status, "partial_success", name);
    assert.ok(h.root.textContent.includes(sample.document.title), name);
    for (const warning of sample.warnings) assert.ok(h.root.textContent.includes(warning), name);
    assert.ok(h.root.textContent.includes("来源详情未关联"), name);
  } finally { h.app.destroy(); }
  passed++;
}
const boundary = structuredClone(samples.boundary);
while (boundary.document.blocks.length < 32) boundary.document.blocks.push({kind: "text", text: "boundary"});
assert.equal(validateQueryResult(boundary).document.blocks.length, 32);
boundary.document.blocks.push({kind: "text", text: "overflow"});
assert.throws(() => validateQueryResult(boundary));
console.log(JSON.stringify({kind: "offline-product-projection-and-synthetic-DOM", passed: passed + 2}));
