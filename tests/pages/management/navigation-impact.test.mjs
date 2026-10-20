// Migrated from the removed global multi-owner credential form. Current owner views use public services.
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {consumer,flush} from './module-consumer-fixture.mjs';
for(const owner of ['ff14/ff14','example/demo'])for(const field of ['credential_fflogs_cn','credential_fflogs_global'])for(const kind of ['client_id','client_secret','replace','clear','confirm'])test(`Module navigation scrubs ${owner} / ${field} / ${kind} without writing`,async()=>{
 const h=await consumer({owner});try{
  h.controller.edit('ff14_default_region','global');
  if(kind==='client_id'||kind==='client_secret')h.controller.editPair(field,kind,'synthetic-navigation-input');else h.editPair(field);
  assert.equal(h.controller.state.pairs[field].client_secret.length>0,kind!=='client_id');assert.equal(h.writes().length,0);
  h.dispose();assert.ok(Object.values(h.controller.state.pairs).every(pair=>pair.client_id===''&&pair.client_secret===''));assert.equal(h.writes().length,0);
 }finally{h.dispose();}
});
test('owner/runtime/epoch replacement clears secrets and cannot transfer ordinary drafts',async()=>{
 for(const change of [{owner:'example/demo'},{runtimeId:'other'},{epoch:2},{boundary:'other'}]){const h=await consumer();try{h.controller.edit('ff14_default_region','global');h.editPair();h.controller.update({...h.context,...change});assert.deepEqual(h.controller.state.draft,{});assert.ok(Object.values(h.controller.state.pairs).every(pair=>pair.client_secret===''));await h.controller.save();assert.equal(h.writes().length,0);}finally{h.dispose();}}
});
test('cancel keeps ordinary drafts for explicit re-entry while clearing both regions',async()=>{
 const h=await consumer();try{h.controller.edit('ff14_default_region','global');h.editPair();h.editPair('credential_fflogs_global');h.controller.cancel();assert.equal(h.controller.state.draft.ff14_default_region,'global');assert.ok(Object.values(h.controller.state.pairs).every(pair=>pair.client_secret===''));assert.equal(h.writes().length,0);}finally{h.dispose();}
});
for(const action of ['admin/module-config-current','admin/module-credential-status'])test(`fresh ${action} authorization failure creates no sensitive draft or write`,async()=>{
 const h=await consumer({failure:action});try{assert.equal(h.controller.state.ready,false);assert.equal(h.controller.state.error,true);assert.ok(Object.values(h.controller.state.pairs).every(pair=>pair.client_secret===''));await h.controller.save();await h.controller.replace('credential_fflogs_cn');assert.equal(h.writes().length,0);}finally{h.dispose();}
});
