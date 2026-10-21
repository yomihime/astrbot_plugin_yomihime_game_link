// Current source single-root navigation; production assets have a separate build gate.
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {resolve} from 'node:path';
import {root} from './compile.mjs';
import {harness,flush} from './single-root-fixture.mjs';
test('mobile navigation opens only declared roles and empty modules have no invented config/query tabs',async()=>{
 const h=await harness();try{
  await h.go('#/module/ff14%2Fff14');const select=h.doc.getElementById('module-select-mobile');assert.equal(select.value,'ff14/ff14');assert.equal(h.doc.querySelector('.module-tabs [aria-current=page]').textContent,'\u4e3b\u9875');
  h.doc.querySelector('.module-tabs a[href$="/config"]').click();await flush();assert.equal(h.window.location.hash,'#/module/ff14%2Fff14/config');assert.ok(h.doc.querySelector('[data-module-role=config]'));assert.equal(h.doc.querySelector('.query-links'),null);
  await h.go('#/module/ff14%2Fff14/-/queries');assert.equal(h.doc.querySelectorAll('.query-links a').length,1);assert.equal(h.doc.querySelector('[data-module-role]'),null);
  await h.go('#/modules');assert.equal(h.doc.querySelector('.module-dashboard').hidden,false);assert.equal(h.doc.querySelector('#module-management-container a[target="_top"]'),null);assert.equal(h.calls.some(call=>call.action==='admin/module-credential-replace'),false);
 }finally{h.close();}
});
test('retired FF14 compatibility locator is absent from current source',async()=>{
 await assert.rejects(readFile(resolve(root,'modules/ff14/pages/compat/index.html'),'utf8'),{code:'ENOENT'});
 const builder=await readFile(resolve(root,'scripts/build_dashboard_zip.py'),'utf8');
 assert.equal(builder.includes('pages/ff14/'),false);
});
