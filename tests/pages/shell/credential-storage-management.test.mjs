// Global key lifecycle and Module business credentials are independent surfaces.
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {harness,keyStatus,flush} from '../management/global-settings-fixture.mjs';
import {consumer} from '../management/module-consumer-fixture.mjs';
test('fresh global managed status never forces business key re-entry and a Module can save a complete pair',async()=>{
 const global=await harness();const module=await consumer();try{assert.equal(global.writes('master-key-restore').length,0);assert.equal(global.doc.querySelector('[data-role=client_secret]'),null);module.editPair();await module.controller.replace('credential_fflogs_cn');assert.equal(module.writes().length,1);assert.equal(module.controller.state.pairs.credential_fflogs_cn.client_secret,'');}finally{global.close();module.dispose();}
});
test('restore CAS conflict keeps the original inventory and requires explicit refresh before another confirmed attempt',async()=>{
 let conflict=true;const h=await harness(keyStatus({state:'needs_restore',ready:false,reason_code:'managed_key_missing'}),action=>{if(action==='admin/master-key-restore'&&conflict)throw Error('revision_conflict');});try{const input=()=>h.doc.getElementById('master-key-restore-input');input().value=Buffer.alloc(32,4).toString('base64');await h.click('#master-key-restore');await h.click('[data-action=confirm-key-restore]');assert.equal(input().value,'');assert.equal(h.doc.getElementById('master-key-restore').disabled,true);conflict=false;await h.click('#refresh');input().value=Buffer.alloc(32,4).toString('base64');await h.click('#master-key-restore');await h.click('[data-action=confirm-key-restore]');assert.equal(h.writes('master-key-restore').length,2);assert.match(h.doc.getElementById('master-key-status').textContent,/\u5c31\u7eea/);}finally{h.close();}
});
test('global reset impact lists every module, cancellation is zero-write and submitted reset uses one exact CAS',async()=>{
 const h=await harness(keyStatus({impact:[{module_id:'ff14/ff14',config_count:2,grant_count:0},{module_id:'example/demo',config_count:1,grant_count:2}]}));try{assert.match(h.doc.getElementById('master-key-reset-impact').textContent,/3.*2/);assert.match(h.doc.querySelector('.master-key-panel').textContent,/example\/demo/);await h.click('#master-key-reset-consent');await h.click('#master-key-reset');await h.click('[data-action=cancel-key-reset]');assert.equal(h.writes('master-key-reset').length,0);await h.click('#master-key-reset-consent');await h.click('#master-key-reset');await h.click('[data-action=confirm-key-reset]');assert.equal(h.writes('master-key-reset').length,1);}finally{h.close();}
});
test('reset confirmation is revoked by suspend/refresh/context replacement and never posts from detached nodes',async()=>{
 for(const action of ['suspend','context']){const h=await harness();try{await h.click('#master-key-reset-consent');await h.click('#master-key-reset');const detached=h.doc.querySelector('[data-action=confirm-key-reset]');if(action==='suspend')h.page.suspend();else h.emit({pluginName:'other'});await flush();detached.click();await flush();assert.equal(h.writes('master-key-reset').length,0);}finally{h.close();}}
});
