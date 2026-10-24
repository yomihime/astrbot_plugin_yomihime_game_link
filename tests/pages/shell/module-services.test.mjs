import {test} from 'node:test';
import assert from 'node:assert/strict';
import {sourceModule} from './compile.mjs';
const {createModuleServices}=await sourceModule('pages/frontend/src/module-services.ts');
const binding={owner:'example/demo',page:'config',runtime_id:'synthetic-runtime',module_epoch:3};
async function harness(handler,confirmClear=async()=>true){let current=true;const calls=[];const services=await createModuleServices({onDispose:()=>{},bridge:{apiPost:async(action,body)=>{if(action==='admin/module-page-session')return {page_session:'synthetic-proof',expires_at:Date.now()/1000+30};calls.push({action,body});return handler(action,body);}},binding,capabilityId:'example.query',isCurrent:()=>current,confirmClear});return {services,calls,revoke(){current=false;}};}
test('owner-bound services return immutable own DTOs without exposing transport',async()=>{
 const h=await harness(()=>({revision:4,fields:{sample:{value:'safe',state:'valid',present:true,source:'sqlite'}}}));
 const value=await h.services.config.current();assert.ok(Object.isFrozen(value.fields.sample));assert.equal(h.services.apiPost,undefined);
 assert.deepEqual(h.calls,[{action:'admin/module-config-current',body:{...binding,page_session:'synthetic-proof'}}]);
 await assert.rejects(h.services.invoke('other.query',{}),/page_unavailable/);assert.equal(h.calls.length,1);
});
test('late response after route/owner revocation is fenced',async()=>{
 let resolve;const h=await harness(()=>new Promise(done=>{resolve=done;}));const request=h.services.credentials.status();await Promise.resolve();await Promise.resolve();h.revoke();resolve({revision:1,fields:{}});await assert.rejects(request,/page_revoked/);
});
test('duplicate mutation is rejected and revision travels with exact owner',async()=>{
 let resolve;const h=await harness(()=>new Promise(done=>{resolve=done;}));const first=h.services.config.save(3,{sample:'new'});
 await assert.rejects(h.services.config.clear(3,['sample']),/operation_in_progress/);await Promise.resolve();await Promise.resolve();resolve({revision:4});await first;
 assert.deepEqual(h.calls[0].body,{...binding,page_session:'synthetic-proof',expected_revision:3,values:{sample:'new'}});
});
test('cancelled/revoked credential confirmation writes nothing',async()=>{
 const cancelled=await harness(()=>assert.fail('no request'),async()=>false);await assert.rejects(cancelled.services.credentials.clear(1,'credential'),/operation_cancelled/);assert.equal(cancelled.calls.length,0);
 let approve;const h=await harness(()=>assert.fail('no request'),()=>new Promise(resolve=>{approve=resolve;}));const pending=h.services.credentials.clear(1,'credential');h.revoke();approve(true);await assert.rejects(pending,/page_unavailable/);assert.equal(h.calls.length,0);
});
test('credential status refuses secret-bearing or invented upstream DTO',async()=>{
 for(const fields of [{credential:{configured:true,decryptable:true,upstream:'success'}},{credential:{configured:true,decryptable:true,upstream:'unverified',client_secret:'synthetic'}}]){const h=await harness(()=>({revision:1,fields}));await assert.rejects(h.services.credentials.status(),/operation_unavailable/);}
});
test('page session renews singleflight for simultaneous reads and rotates only its own proof',async()=>{
 const nativeNow=Date.now;let time=nativeNow()/1000;Date.now=()=>time*1000;let count=0,dispose;const calls=[];
 const bridge={async apiPost(action,body){calls.push({action,body});if(action==='admin/module-page-session')return {page_session:'proof-'+(++count),expires_at:time+30};if(action==='admin/module-config-current')return {revision:1,fields:{}};if(action==='admin/module-credential-status')return {revision:1,fields:{}};return {closed:true};}};
 try{
  const services=await createModuleServices({bridge,binding,capabilityId:null,isCurrent:()=>true,onDispose:fn=>{dispose=fn;},confirmClear:async()=>true});time+=29;
  await Promise.all([services.config.current(),services.credentials.status()]);assert.equal(count,2);assert.equal(calls.filter(c=>c.action==='admin/module-page-session')[1].body.previous_session,'proof-1');
  for(const call of calls.filter(c=>c.action.endsWith('current')||c.action.endsWith('status')))assert.equal(call.body.page_session,'proof-2');
  dispose();await Promise.resolve();assert.deepEqual(calls.at(-1),{action:'admin/module-page-session-close',body:{page_session:'proof-2'}});await assert.rejects(services.config.current(),/page_revoked/);
 }finally{Date.now=nativeNow;}
});
test('late bootstrap after disposal closes its own new proof and never mounts a usable service',async()=>{
 let resolve,dispose,current=true;const calls=[];
 const promise=createModuleServices({bridge:{apiPost(action,body){calls.push({action,body});return action==='admin/module-page-session'?new Promise(done=>{resolve=done;}):Promise.resolve({closed:true});}},binding,capabilityId:null,isCurrent:()=>current,onDispose:fn=>{dispose=fn;},confirmClear:async()=>true});
 current=false;dispose();resolve({page_session:'late-proof',expires_at:Date.now()/1000+30});await assert.rejects(promise,/page_revoked/);await Promise.resolve();assert.deepEqual(calls.at(-1),{action:'admin/module-page-session-close',body:{page_session:'late-proof'}});
});

test('proof rotates at the ten-second margin while earlier reads keep the existing proof',async()=>{
 const nativeNow=Date.now;let time=1000,issues=0;Date.now=()=>time*1000;const calls=[];
 try{
  const services=await createModuleServices({bridge:{async apiPost(action,body){calls.push({action,body});if(action==='admin/module-page-session')return {page_session:'proof-'+(++issues),expires_at:time+30};return {revision:1,fields:{}};}},binding,capabilityId:null,isCurrent:()=>true,onDispose(){},confirmClear:async()=>true});
  time=1019.999;await services.config.current();assert.equal(issues,1);assert.equal(calls.at(-1).body.page_session,'proof-1');
  time=1020;await Promise.all([services.config.current(),services.credentials.status()]);assert.equal(issues,2);
  const rotation=calls.filter(call=>call.action==='admin/module-page-session')[1];assert.equal(rotation.body.previous_session,'proof-1');
  assert.equal(rotation.body.owner,binding.owner);assert.equal(rotation.body.page,binding.page);
  assert.equal(calls.at(-1).body.page_session,'proof-2');
 }finally{Date.now=nativeNow;}
});
test('an uncertain mutation is sent once without automatically renewing or retrying it',async()=>{
 const h=await harness(()=>{throw Error('write_unknown');});await assert.rejects(h.services.config.save(1,{sample:'new'}),/operation_unavailable/);assert.equal(h.calls.length,1);assert.equal(h.calls[0].action,'admin/module-config-save');
});

test('message-only Host marker is exact; unknown management errors and business upstream errors stay distinct',async()=>{
 for(const error of [Error('admin_authorization_denied'),{code:'admin_authorization_denied'},{response:{status:403}}]){
  const h=await harness(()=>{throw error;});await assert.rejects(h.services.config.current(),{message:'admin_authorization_denied'});
 }
 for(const error of [Error('request failed: admin_authorization_denied'),Error('admin_authorization_denied '),TypeError('Failed to fetch'),{code:'upstream_error'}]){
  const h=await harness(()=>{throw error;});await assert.rejects(h.services.config.current(),{message:'operation_unavailable'});
 }
 const upstream={status:'error',error:{code:'upstream_error',message:'controlled upstream failure'}};
 const business=await harness(()=>upstream);assert.deepEqual(await business.services.invoke('example.query',{}),upstream);
});
test('approved own descriptors preserve null inheritance and immutable labels; private/unknown schema keys refuse',async()=>{
 const snapshot={revision:1,fields:{reply_theme:{value:null,state:'valid',present:false,source:'default'}},descriptors:{reply_theme:{description:'Theme',value_schema:{type:'string',enum:['synthetic-theme']},default:null,choices:[{value:null,label:'Follow Core'},{value:'synthetic-theme',label:'Server label'}]}}};
 const h=await harness(()=>snapshot);const read=await h.services.config.current();assert.equal(read.fields.reply_theme.value,null);assert.ok(Object.isFrozen(read.descriptors.reply_theme.choices[0]));
 for(const mutate of [v=>v.descriptors.reply_theme.value_schema.private_owner='game_link/core',v=>v.descriptors.other=v.descriptors.reply_theme,v=>v.descriptors.reply_theme.description='x'.repeat(513),v=>v.descriptors.reply_theme.choices[1].label='',v=>v.descriptors.reply_theme.source_path='private']){
  const invalid=structuredClone(snapshot);mutate(invalid);const bad=await harness(()=>invalid);await assert.rejects(bad.services.config.current(),/operation_unavailable/);
 }
});
test('effective legacy projection exposes only the target field with an explicit pending marker',async()=>{
 const projected={revision:1,fields:{sample:{value:'effective',state:'valid',present:false,source:'legacy',migration_pending:true}}};
 const h=await harness(()=>projected);assert.equal((await h.services.config.current()).fields.sample.migration_pending,true);
 for(const change of [row=>delete row.migration_pending,row=>row.present=true,row=>row.migration_pending=false,row=>row.source_owner='game_link/core']){const invalid=structuredClone(projected);change(invalid.fields.sample);const bad=await harness(()=>invalid);await assert.rejects(bad.services.config.current(),/operation_unavailable/);}
});
