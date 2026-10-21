import {test} from 'node:test';
import assert from 'node:assert/strict';
import {harness,flush} from './single-root-fixture.mjs';
test('source single root separates home, module lifecycle, global Core settings and role pages',async()=>{
 const h=await harness();try{
  assert.match(h.doc.getElementById('page-title').textContent,/首页/);assert.equal(h.doc.querySelector('[data-module-role]'),null);
  await h.go('#/module/ff14%2Fff14');assert.ok(h.doc.querySelector('[data-module-role=home]'));assert.equal(h.doc.querySelector('.query-links'),null);
  await h.go('#/module/ff14%2Fff14/config');assert.ok(h.doc.querySelector('[data-module-role=config]'));assert.equal(h.doc.querySelector('.query-links'),null);
  await h.go('#/module/ff14%2Fff14/-/queries');assert.equal(h.doc.querySelector('[data-module-role]'),null);assert.equal(h.doc.querySelectorAll('.query-links a').length,1);
  await h.go('#/settings');assert.ok(h.doc.querySelector('[data-field=sample]'));assert.equal(h.doc.querySelector('[data-field=not_global]'),null);assert.ok(h.doc.querySelector('[data-legacy-policy="example.copy"]'));assert.equal(h.calls.some(call=>call.action.startsWith('admin/credential-')),false);
  await h.go('#/modules');assert.equal(h.doc.querySelector('.module-dashboard').hidden,false);assert.match(h.doc.getElementById('page-title').textContent,/模块管理/);
 }finally{h.close();}
});
test('Core navigation confirmation cancels safely and re-entry preserves ordinary draft',async()=>{
 const h=await harness();try{
  await h.go('#/settings');const input=h.doc.querySelector('[data-field=sample] [data-role=value]');input.value='new draft';input.dispatchEvent(new h.window.Event('input',{bubbles:true}));await flush();
  await h.go('#/');assert.equal(h.window.location.hash,'#/settings');assert.ok(h.doc.querySelector('[data-confirmation=navigation]'));h.doc.querySelector('[data-action=cancel-navigation]').click();await flush();assert.equal(input.value,'new draft');
  await h.go('#/');h.doc.querySelector('[data-action=confirm-navigation]').click();await flush();assert.equal(h.window.location.hash,'#/');await h.go('#/settings');assert.equal(h.doc.querySelector('[data-field=sample] [data-role=value]').value,'new draft');assert.equal(h.calls.filter(call=>call.action==='admin/update').length,0);
 }finally{h.close();}
});
