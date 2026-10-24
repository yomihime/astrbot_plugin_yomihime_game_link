import {test} from 'node:test';
import assert from 'node:assert/strict';
import {sourceModule,root} from './compile.mjs';
import {catalog} from './fixtures.mjs';
import {harness} from './single-root-fixture.mjs';
const {validateCatalog}=await sourceModule(root+'pages/frontend/src/catalog.ts');
function fixture(summary){const value=catalog();value.modules[0].display={default_name:'Example module',summary};return value;}

test('optional module-owned summary is bounded Unicode plain text, never inferred',()=>{
 for(const value of [undefined,null])assert.equal(validateCatalog(fixture(value)).modules[0].display.summary,null);
 for(const value of ['物品查询 · 市场行情','A'.repeat(160),'😀'.repeat(160),'<b>Literal text</b>'])assert.equal(validateCatalog(fixture(value)).modules[0].display.summary,value);
 for(const value of ['', '  ', 'A'.repeat(161),'😀'.repeat(161),'line\nbreak','tab\there','zero\u200bwidth','\ud800',false,17,[],{}])assert.throws(()=>validateCatalog(fixture(value)),/invalid_catalog/);
 const unknown=fixture(undefined);unknown.modules[0].capabilities=[{capability_id:'imagined_business_feature'}];
 assert.equal(validateCatalog(unknown).modules[0].display.summary,null);
});

test('home renders provided summary as non-clickable escaped text and omits missing summary',async()=>{
 const value=fixture('<b>真实摘要</b>');const h=await harness(value);try{
  const summary=h.doc.querySelector('.workspace-module-summary');assert.ok(summary);
  assert.equal(summary.textContent,'<b>真实摘要</b>');assert.equal(summary.querySelector('b'),null);
  assert.equal(summary.closest('a'),null);assert.equal(summary.hasAttribute('tabindex'),false);
 }finally{h.close();}
 const missing=await harness(fixture(undefined));try{assert.equal(missing.doc.querySelector('.workspace-module-summary'),null);}finally{missing.close();}
});
