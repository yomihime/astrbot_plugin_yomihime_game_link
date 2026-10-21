// Credential authority and lifecycle now exercise the public owner-bound Module consumer.
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {consumer,flush} from './module-consumer-fixture.mjs';
import {validateCredentialCatalog,validateCredentialStatus,credentialUpdateBody} from './source-management.mjs';
import {credentialCatalog,credentialStatus} from './fixtures.mjs';
test('closed metadata contracts and pair inputs never allow ordinary fields or readback',()=>{
  const c=validateCredentialCatalog(credentialCatalog());
  assert.ok(validateCredentialStatus(credentialStatus(),c));
  assert.throws(()=>validateCredentialStatus({'ff14/ff14':{revision:1,fields:{credential_fflogs_cn:'private'}}},c));
  const pair={client_id:'synthetic-id',client_secret:'synthetic-secret'};
  assert.deepEqual(credentialUpdateBody('ff14/ff14',1,'credential_fflogs_cn','replace',pair,c).updates,[{field:'credential_fflogs_cn',mode:'replace',value:pair}]);
  assert.throws(()=>credentialUpdateBody('ff14/ff14',1,'credential_fflogs_cn','replace',{...pair,token:'x'},c));
  assert.throws(()=>credentialUpdateBody('ff14/ff14',1,'default_region','clear',null,c));
  assert.throws(()=>credentialUpdateBody('ff14/ff14',1,'credential_fflogs_cn','keep',null,c));
  const extra=credentialCatalog();extra.fields[0].value='private';assert.throws(()=>validateCredentialCatalog(extra));
});

test('empty pair is zero-write; complete pair uses latest owner CAS and clears both secret inputs',async()=>{
 const h=await consumer();try{await h.controller.replace('credential_fflogs_cn');assert.equal(h.writes().length,0);h.editPair();await h.controller.replace('credential_fflogs_cn');assert.equal(h.writes().length,1);assert.equal(h.writes()[0].body.expected_revision,4);assert.deepEqual(h.writes()[0].body.pair,{client_id:'synthetic-id',client_secret:'synthetic-secret'});assert.ok(Object.values(h.controller.state.pairs).every(pair=>pair.client_secret===''));}finally{h.dispose();}
});
test('clear cancellation is zero-write and confirmed clear sends fixed owner/page/revision confirmation',async()=>{
 const h=await consumer();try{h.confirm(false);await h.controller.clear('credential_fflogs_cn');assert.equal(h.writes().length,0);await h.controller.load();h.confirm(true);await h.controller.clear('credential_fflogs_cn');assert.equal(h.writes().length,1);assert.equal(h.writes()[0].body.confirmation,'CLEAR MODULE CREDENTIAL');assert.equal(h.writes()[0].body.owner,h.context.owner);assert.equal(h.writes()[0].body.expected_revision,4);}finally{h.dispose();}
});
for(const action of ['admin/module-config-current','admin/module-credential-status','admin/module-credential-replace'])test(`credential authorization failure at ${action} scrubs secrets and fences forced saves`,async()=>{
 const h=await consumer();try{h.editPair();h.state.failure=action;if(action.endsWith('replace'))await h.controller.replace('credential_fflogs_cn');else await h.controller.load();assert.equal(h.controller.state.ready,false);assert.equal(h.controller.state.error,true);assert.ok(Object.values(h.controller.state.pairs).every(pair=>pair.client_secret===''));const before=h.writes().length;await h.controller.save();await h.controller.replace('credential_fflogs_cn');assert.equal(h.writes().length,before);}finally{h.dispose();}
});
test('held write blocks duplicate submit and disposal fences late acknowledgment without refresh or success',async()=>{
 const h=await consumer({hold:'admin/module-credential-replace'});h.editPair();const operation=h.controller.replace('credential_fflogs_cn');await flush();await h.controller.replace('credential_fflogs_cn');assert.equal(h.writes().length,1);const reads=h.calls.filter(call=>call.action==='admin/module-credential-status').length;h.dispose();h.release();await operation;assert.equal(h.controller.state.credentialRevision,4);assert.equal(h.calls.filter(call=>call.action==='admin/module-credential-status').length,reads);assert.ok(Object.values(h.controller.state.pairs).every(pair=>pair.client_secret===''));
});
for(const decryptable of [false,true])test(`configured/decryptable/upstream remain independent and clear is permitted without decryption: ${decryptable}`,async()=>{
 const h=await consumer({decryptable});try{const state=h.controller.state.credentials.fields.credential_fflogs_cn;assert.equal(state.configured,true);assert.equal(state.decryptable,decryptable);assert.equal(state.upstream,'unverified');await h.controller.clear('credential_fflogs_cn');assert.equal(h.writes().length,1);assert.equal(h.writes()[0].action,'admin/module-credential-clear');}finally{h.dispose();}
});
test('region drafts stay separate; ordinary and credential saves use refreshed shared owner CAS',async()=>{
 const h=await consumer();try{h.editPair();h.editPair('credential_fflogs_global');h.controller.discardPair('credential_fflogs_cn');assert.equal(h.controller.state.pairs.credential_fflogs_cn.client_secret,'');assert.ok(h.controller.state.pairs.credential_fflogs_global.client_secret);h.controller.edit('ff14_default_region','global');await h.controller.save();await h.controller.load();h.editPair('credential_fflogs_global');await h.controller.replace('credential_fflogs_global');assert.deepEqual(h.writes().map(call=>call.body.expected_revision),[4,5]);assert.ok(Object.values(h.controller.state.pairs).every(pair=>pair.client_secret===''));}finally{h.dispose();}
});
