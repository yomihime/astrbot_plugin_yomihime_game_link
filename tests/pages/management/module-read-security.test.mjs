import {test} from 'node:test';
import assert from 'node:assert/strict';
import {consumer,flush} from './module-consumer-fixture.mjs';

const fields=['credential_fflogs_cn','credential_fflogs_global'];
const fill=h=>fields.forEach(field=>h.editPair(field));
const empty=h=>assert.ok(fields.every(field=>Object.values(h.controller.state.pairs[field]).every(value=>value==='')));

for(const action of ['admin/module-config-current','admin/module-credential-status'])
for(const [kind,error,authorized]of [
 ['exact marker',Error('admin_authorization_denied'),false],
 ['unclassified management error',Error('synthetic-unknown'),true],
 ['network failure',TypeError('Failed to fetch'),true],
 ['upstream code on management transport',{code:'upstream_error',message:'synthetic upstream failure'},true],
 ['marker in prose',Error('request failed: admin_authorization_denied'),true],
])test(`${action}: ${kind} clears both secret drafts without clearing ordinary edits`,async()=>{
 const h=await consumer();try{
  h.controller.edit('ff14_calendar_default_days','13');fill(h);
  h.state.failure=action;h.state.error=error;
  await h.controller.load();empty(h);
  assert.equal(h.controller.state.draft.ff14_calendar_default_days,'13');
  assert.equal(h.controller.state.dirty,true);assert.equal(h.controller.state.ready,false);
  assert.equal(h.controller.state.message.includes('管理授权或模块绑定已失效'),!authorized);
  assert.equal(h.writes().length,0);
 }finally{h.dispose();}
});

for(const operation of ['ordinary','credential'])test(`${operation} ACK followed by unknown read clears all secrets, retains unverified receipt and never retries`,async()=>{
 const h=await consumer();try{
  h.controller.edit('ff14_calendar_default_days','13');fill(h);
  h.state.failure='admin/module-credential-status';h.state.error=Error('synthetic-network');
  await(operation==='ordinary'?h.controller.save():h.controller.replace(fields[0]));
  empty(h);assert.equal(h.writes().length,1);assert.equal(h.state.revision,5);
  assert.equal(h.controller.state.revision,4);assert.equal(h.controller.state.baselineUnverified,true);
  assert.equal(h.controller.state.draft.ff14_calendar_default_days,'13');
  assert.equal(h.controller.state.message,'已提交，状态暂未核实，请刷新确认。');
  await h.controller.save();await h.controller.replace(fields[0]);assert.equal(h.writes().length,1);
  h.state.failure='';await h.controller.load();assert.equal(h.controller.state.revision,5);
  assert.equal(h.writes().length,1);
 }finally{h.dispose();}
});

for(const change of ['dispose','owner','session'])test(`late failed read after ${change} cannot scrub the next context's secrets or ordinary draft`,async()=>{
 const old=await consumer();let fresh;
 try{
  fill(old);old.state.hold='admin/module-config-current';old.state.failure=old.state.hold;old.state.error=Error('admin_authorization_denied');
  const pending=old.controller.load();await flush();
  if(change==='dispose')old.dispose();else old.controller.update({...old.context,[change==='owner'?'owner':'boundary']:'next-context'});
  fresh=await consumer({context:{boundary:'new-'+change}});fresh.controller.edit('ff14_calendar_default_days','17');fill(fresh);
  const before=structuredClone(fresh.controller.state.pairs);old.release();await pending;await flush();
  assert.deepEqual(fresh.controller.state.pairs,before);assert.equal(fresh.controller.state.draft.ff14_calendar_default_days,'17');
  assert.equal(fresh.controller.state.ready,true);assert.equal(fresh.writes().length,0);empty(old);
 }finally{old.dispose();fresh?.dispose();}
});
