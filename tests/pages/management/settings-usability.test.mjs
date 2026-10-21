// The global and Module surfaces are separate; every former access/ACK failure phase is retained.
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {consumer,flush} from './module-consumer-fixture.mjs';
import {harness,keyStatus} from './global-settings-fixture.mjs';
const accessFailures=[['controlled-code',Error('admin_authorization_denied')],['code-object',{code:'admin_authorization_denied',message:'controlled'}],['error-code',{error_code:'asset_revoked'}],['context-expired',Error('stale_context')],['page-revoked',Error('page_revoked')],['http401',{status:401}],['http403',{response:{status:403}}]];
for(const [kind,error]of accessFailures)for(const when of ['initial','refresh','ack'])test(`Module ${kind} at ${when} clears and fences writes without success`,async()=>{
 const action=when==='ack'?'admin/module-credential-status':'admin/module-config-current';
 const h=await consumer(when==='initial'?{failure:action,error}:{});try{
  h.state.error=error;
  if(when!=='initial'){h.editPair();if(when==='ack'){h.state.failure=action;await h.controller.replace('credential_fflogs_cn');}else{h.state.failure=action;await h.controller.load();}}
  assert.equal(h.controller.state.ready,false);assert.equal(h.controller.state.error,true);assert.ok(Object.values(h.controller.state.pairs).every(pair=>pair.client_secret===''));
  const count=h.writes().length;await h.controller.save();await h.controller.replace('credential_fflogs_cn');assert.equal(h.writes().length,count);assert.equal(count,when==='ack'?1:0);
 }finally{h.dispose();}
});
test('global nonsecret settings survive unrelated unavailable Module credentials without credential API reads',async()=>{
 const global=await harness(keyStatus());const module=await consumer({failure:'admin/module-credential-status'});try{assert.equal(module.controller.state.ready,false);assert.match(global.doc.getElementById('master-key-status').textContent,/\u5c31\u7eea/);assert.equal(global.calls.some(call=>call.endpoint.startsWith('admin/credential-')),false);await global.click('#refresh');assert.equal(global.doc.getElementById('refresh').disabled,false);}finally{global.close();module.dispose();}
});
for(const kind of ['ready-false','crypto503','unknown-message'])test(`unavailable encryption ${kind} permits explicit scoped clear while replace is not claimed successful`,async()=>{
 const h=await consumer({decryptable:false});try{h.editPair();await h.controller.replace('credential_fflogs_cn');assert.equal(h.controller.state.error,true);assert.equal(h.controller.state.ready,false);await h.controller.load();await h.controller.clear('credential_fflogs_cn');assert.equal(h.writes().at(-1).action,'admin/module-credential-clear');assert.equal(h.controller.state.error,false);}finally{h.dispose();}
});
for(const operation of ['ordinary','credential'])for(const outcome of ['success','ack-denied','read-failure'])test(`${operation} save ${outcome} reports one mutation and no automatic retry`,async()=>{
 const h=await consumer();try{
  const mutation=operation==='ordinary'?'admin/module-config-save':'admin/module-credential-replace';
  const reads=['admin/module-config-current','admin/module-credential-status'];
  const beforeReads=reads.map(action=>h.calls.filter(call=>call.action===action).length);
  assert.equal(h.controller.state.ready,true);
  if(outcome==='ack-denied')h.state.failure=mutation;
  if(outcome==='read-failure'){h.state.failure=operation==='ordinary'?reads[0]:reads[1];h.state.error=Error('synthetic-network-failure');}
  if(operation==='ordinary'){assert.ok(h.controller.state.themeChoices.some(choice=>choice.value==='one'));h.controller.edit('reply_theme','one');await h.controller.save();}else{h.editPair();await h.controller.replace('credential_fflogs_cn');}
  await flush();
  assert.equal(h.writes().length,1);assert.equal(h.writes()[0].action,mutation);
  assert.equal(h.writes()[0].body.expected_revision,4);
  if(operation==='ordinary')assert.equal(h.writes()[0].body.values.reply_theme,'one');
  assert.equal(h.controller.state.error,outcome!=='success');
  if(outcome==='read-failure'){
   assert.equal(h.state.revision,5,'the mutation ACK must precede the failed read');
   assert.deepEqual(reads.map(action=>h.calls.filter(call=>call.action===action).length),beforeReads.map(count=>count+1),'both snapshots are read once after ACK, without automatic retry');
   assert.equal(h.controller.state.ready,false);assert.equal(h.controller.state.baselineUnverified,true);assert.equal(h.controller.state.pending,false);
   assert.equal(h.controller.state.revision,4,'an ACK must not become a verified snapshot revision');
   assert.equal(h.controller.state.message,'已提交，状态暂未核实，请刷新确认。');
   if(operation==='ordinary')assert.equal(h.state.fields.reply_theme.value,'one');
   else assert.deepEqual(h.controller.state.pairs.credential_fflogs_cn,{client_id:'',client_secret:''});
  }
 }finally{h.dispose();}
});
test('status separates unset/configured/decryptable from unverified upstream and never reveals previous pair',async()=>{
 for(const configured of [false,true])for(const decryptable of [false,true]){const h=await consumer({configured,decryptable});try{const status=h.controller.state.credentials.fields.credential_fflogs_cn;assert.deepEqual(status,{configured,decryptable,upstream:'unverified'});assert.ok(Object.values(h.controller.state.pairs).every(pair=>pair.client_id===''&&pair.client_secret===''));}finally{h.dispose();}}
});
test('global ready mode keeps recovery collapsed and ordinary/maintenance controls do not mix',async()=>{
 const h=await harness();try{assert.equal(h.doc.querySelector('.maintenance-panel,#recover,#rollback'),null);assert.equal(h.doc.querySelector('details.advanced-config').open,false);h.page.suspend();h.page.resume();await flush();assert.equal(h.doc.querySelector('[data-credential]'),null);}finally{h.close();}
});
