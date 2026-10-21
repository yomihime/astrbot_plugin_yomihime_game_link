import {test} from 'node:test';
import assert from 'node:assert/strict';
import {harness,keyStatus,flush,digest,context} from './global-settings-fixture.mjs';
test('global settings has independent storage security and no retired Module credential forms',async()=>{
 const h=await harness(keyStatus({storage_security:'legacy_review_required'}));try{
  assert.match(h.doc.getElementById('master-key-status').textContent,/就绪/);assert.match(h.doc.getElementById('storage-security').textContent,/旧布局/);
  assert.equal(h.doc.querySelector('[data-role=client_secret]'),null);assert.equal(h.calls.some(call=>call.endpoint.startsWith('admin/credential-')),false);
  assert.equal(h.doc.querySelector('details.advanced-config').open,false);
 }finally{h.close();}
});
test('known blocked storage gives restart guidance while ordinary settings remain available',async()=>{
 const fields=[{module_id:'game_link/core',name:'sample',description:'Sample',group:null,value_schema:{type:'string'},default:'old',required:false,readable:true,editable:true,blocked_reason:null}];
 const h=await harness(keyStatus({state:'unknown',ready:false,reason_code:'unsafe_storage_layout',source:'unselected',storage_security:'blocked',generation:0,revision:0,inventory_digest:'',unknown_count:1,impact:[],environment_present:null,environment_matches:null}),endpoint=>endpoint==='admin/catalog'?{schema_version:1,fields}:endpoint==='admin/read'?{'game_link/core':{revision:1,fields:{sample:{value:'old',state:'valid',present:false,source:'default'}}}}:undefined);try{
  assert.match(h.doc.querySelector('.master-key-panel').textContent,/修复后正常重启同一宿主/);
  assert.match(h.doc.querySelector('.master-key-panel').textContent,/影响范围暂不可确认/);
  assert.doesNotMatch(h.doc.querySelector('.master-key-panel').textContent,/配置凭据 0 项|授权凭据 0 项/);
  assert.equal(h.doc.getElementById('master-key-retry').disabled,true);
  const ordinary=h.doc.querySelector('[data-field=sample] [data-role=value]');assert.equal(ordinary.disabled,false);ordinary.value='ordinary draft';ordinary.dispatchEvent(new h.dom.window.Event('input'));assert.equal(ordinary.value,'ordinary draft');
  assert.equal(h.writes('master-key-reset').length,0);
 }finally{h.close();}
});
test('verified empty inventory can accurately report zero impact',async()=>{
 const h=await harness(keyStatus({impact:[]}));try{
  assert.match(h.doc.querySelector('.master-key-panel').textContent,/配置凭据 0 项，授权凭据 0 项/);
  assert.doesNotMatch(h.doc.querySelector('.master-key-panel').textContent,/影响范围暂不可确认/);
 }finally{h.close();}
});
test('reset needs shared confirmation; cancel writes nothing and exact CAS submits only once',async()=>{
 let release;const h=await harness(keyStatus(),endpoint=>endpoint==='admin/master-key-reset'?new Promise(resolve=>{release=resolve;}):undefined);try{
  await h.click('#master-key-reset-consent');await h.click('#master-key-reset');assert.equal(h.writes('master-key-reset').length,0);
  await h.click('[data-action=cancel-key-reset]');assert.equal(h.writes('master-key-reset').length,0);
  await h.click('#master-key-reset-consent');await h.click('#master-key-reset');await h.click('[data-action=confirm-key-reset]');
  assert.equal(h.writes('master-key-reset').length,1);assert.deepEqual(h.writes('master-key-reset')[0].body,{expected_revision:2,expected_generation:1,inventory_digest:digest,confirmation:'RESET ENCRYPTED CREDENTIALS'});
  await h.click('#master-key-reset');assert.equal(h.writes('master-key-reset').length,1);release(keyStatus({revision:3,generation:2}));await flush();
 }finally{release?.(keyStatus());h.close();}
});
test('environment adoption uses CAS without key re-entry and gives cold-restart guidance',async()=>{
 const h=await harness(keyStatus({source:'environment',environment_present:true,environment_matches:true,recovery_action:'adopt_environment'}));try{
  await h.click('#master-key-adopt-environment');await h.click('[data-action=confirm-key-adopt]');assert.deepEqual(h.writes('master-key-adopt-environment')[0].body,{expected_revision:2,expected_generation:1,inventory_digest:digest});
  assert.match(h.doc.querySelector('.master-key-panel').textContent,/移除环境.*冷重启/);assert.equal(h.doc.getElementById('master-key-restore-input').value,'');
 }finally{h.close();}
});
test('restore secret draft is scrubbed before confirmation and suspension cancels the pending action',async()=>{
 const h=await harness(keyStatus({state:'needs_restore',ready:false,reason_code:'managed_key_missing'}));try{
  const input=h.doc.getElementById('master-key-restore-input');input.value=Buffer.alloc(32,4).toString('base64');await h.click('#master-key-restore');assert.equal(input.value,'');const old=h.doc.querySelector('[data-action=confirm-key-restore]');assert.ok(old);h.page.suspend();await flush();old.click();await flush();assert.equal(h.writes('master-key-restore').length,0);assert.equal(h.doc.querySelector('[data-confirmation]'),null);
 }finally{h.close();}
});
test('invalid page context cancels confirmation and cannot reuse the old ready result',async()=>{
 const h=await harness();try{await h.click('#master-key-reset-consent');await h.click('#master-key-reset');const old=h.doc.querySelector('[data-action=confirm-key-reset]');h.emit({...context,pageName:'shell'});await flush();old.click();await flush();assert.equal(h.writes('master-key-reset').length,0);assert.equal(h.doc.querySelector('[data-confirmation]'),null);assert.equal(h.doc.getElementById('refresh').disabled,true);}finally{h.close();}
});
test('storage plan is read-only and unavailable production execution is not offered',async()=>{
 const h=await harness(keyStatus({storage_security:'legacy_review_required'}));try{
  await h.click('#storage-migration-plan');assert.equal(h.writes('master-key-migration-execute').length,0);assert.match(h.doc.querySelector('.master-key-panel').textContent,/尚未迁移/);
  assert.equal(h.doc.querySelector('#storage-migration-execute'),null);assert.match(h.doc.querySelector('.master-key-panel').textContent,/仅支持.*计划.*没有.*执行适配器/);assert.equal(h.writes('master-key-migration-execute').length,0);
 }finally{h.close();}
});
test('registered policy copy uses dual revisions and never sends source value, owner or path',async()=>{
 const h=await harness();try{
  h.page.setLegacyPolicies([{policy_id:'sample.copy',target_owner:'example/demo'}]);await flush();await h.click('[data-action=legacy-plan]');assert.deepEqual(h.writes('legacy-config-plan')[0].body,{policy_id:'sample.copy'});
  await h.click('[data-action=legacy-execute]');await h.click('[data-action=cancel-legacy-config]');assert.equal(h.writes('legacy-config-execute').length,0);
  await h.click('[data-action=legacy-execute]');await h.click('[data-action=confirm-legacy-config]');assert.deepEqual(h.writes('legacy-config-execute')[0].body,{policy_id:'sample.copy',source_revision:3,target_revision:4,confirmed:true});
 }finally{h.close();}
});
test('policy revocation and late plan cannot restore a removed owner or replay confirmation',async()=>{
 let release;const h=await harness(keyStatus(),endpoint=>endpoint==='admin/legacy-config-plan'?new Promise(resolve=>{release=resolve;}):undefined);try{
  h.page.setLegacyPolicies([{policy_id:'sample.copy',target_owner:'example/demo'}]);await flush();await h.click('[data-action=legacy-plan]');h.page.setLegacyPolicies([]);release({policy_id:'sample.copy',status:'migration_pending',source_revision:3,target_revision:4,migration_pending:true,written:false});await flush();assert.equal(h.doc.querySelector('[data-legacy-policy]'),null);assert.equal(h.writes('legacy-config-execute').length,0);
 }finally{h.close();}
});
