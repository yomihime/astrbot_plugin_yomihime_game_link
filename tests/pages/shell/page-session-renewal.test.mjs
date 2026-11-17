import {test} from 'node:test';
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {createInterface} from 'node:readline';
import {sourceModule,root} from './compile.mjs';
const {createModuleServices}=await sourceModule(root+'pages/frontend/src/module-services.ts');
async function registry(){
 // This helper requires this launcher to import tests and initialize its test-only
 // package alias before runpy; the Python file is not an independent direct entry.
 const child=spawn(process.env.YGL_TEST_PYTHON || 'python',['-u','-c',"import tests;import runpy;runpy.run_path('tests/pages/shell/page-session-fixture.py',run_name='__main__')"],{cwd:root,windowsHide:true,stdio:['pipe','pipe','pipe']});let serial=0,stderr='',failure,closing=false;const waiting=new Map();
 const fail=error=>{failure??=error;for(const item of waiting.values())item.reject(failure);waiting.clear();};
 child.stderr.on('data',chunk=>stderr+=chunk);child.stdin.on('error',fail);createInterface({input:child.stdout}).on('line',line=>{const value=JSON.parse(line),item=waiting.get(value.id);waiting.delete(value.id);value.error?item.reject(Error(value.error)):item.resolve(value.result);});
 const exited=new Promise((resolve,reject)=>{child.once('error',error=>{fail(error);reject(failure);});child.once('close',(code,signal)=>{if(code!==0 || !closing || waiting.size)fail(Error(stderr || `Python fixture exited early (code=${code}, signal=${signal})`));failure?reject(failure):resolve();});});
 // Observe an early rejection immediately; close still propagates the same failure.
 void exited.catch(()=>{});
 return {request(action,now,body={}){return new Promise((resolve,reject)=>{if(failure){reject(failure);return;}const id=++serial;waiting.set(id,{resolve,reject});child.stdin.write(JSON.stringify({id,action,now,...body})+'\n');});},async close(){closing=true;child.stdin.end();await exited;}};
}
for(const seconds of [29,30,31])test(`real registry permits fresh authorized use after ${seconds}s idle without expired rotation`,async(t)=>{
 const server=await registry();let now=1000,dispose,authorized=true;const calls=[];
 t.mock.method(performance,'now',()=>now*1000);
 try{
  const bridge={async apiPost(action,body){calls.push({action,body});if(action==='admin/module-page-session')return server.request('issue',now,{previous_session:body.previous_session,relative_expiry:body.relative_expiry,authorized});if(action==='admin/module-page-session-close')return server.request('close',now,body);await server.request('require',now,{page_session:body.page_session,authorized});return action==='admin/module-config-save'?{revision:2}:{revision:1,fields:{}};}};
  const options={bridge,binding:{owner:'example/demo',page:'config',runtime_id:'synthetic',module_epoch:1},capabilityId:null,isCurrent:()=>true,onDispose:fn=>{dispose=fn;},confirmClear:async()=>true};
  const services=await createModuleServices(options),other=await createModuleServices({...options,onDispose(){}});now+=seconds;
  await Promise.all([services.config.current(),services.credentials.status()]);assert.equal(calls.filter(call=>call.action==='admin/module-page-session').length,3);
  const renewal=calls.filter(call=>call.action==='admin/module-page-session').at(-1).body;assert.equal(Object.hasOwn(renewal,'previous_session'),seconds<30);
  if(seconds<30)await other.config.current();await services.config.save(1,{sample:'after-idle'});assert.equal(calls.filter(call=>call.action==='admin/module-config-save').length,1);
  authorized=false;now+=31;await assert.rejects(services.config.current());assert.equal(calls.filter(call=>call.action==='admin/module-config-save').length,1);dispose();await new Promise(resolve=>setImmediate(resolve));
 }finally{await server.close();}
});
test('expiry while awaiting an already submitted write never causes a mutation retry',async(t)=>{
 const server=await registry();let now=1000,release,writes=0,issues=0;
 t.mock.method(performance,'now',()=>now*1000);
 try{
  const services=await createModuleServices({bridge:{async apiPost(action,body){if(action==='admin/module-page-session'){issues++;return server.request('issue',now,{previous_session:body.previous_session,relative_expiry:body.relative_expiry});}if(action==='admin/module-page-session-close')return server.request('close',now,body);await server.request('require',now,{page_session:body.page_session});writes++;await new Promise(resolve=>{release=resolve;});return {revision:2};}},binding:{owner:'example/demo',page:'config',runtime_id:'synthetic',module_epoch:1},capabilityId:null,isCurrent:()=>true,onDispose(){},confirmClear:async()=>true});
  const pending=services.config.save(1,{sample:'new'});while(!release)await new Promise(resolve=>setImmediate(resolve));now+=31;release();assert.deepEqual(await pending,{revision:2});assert.equal(writes,1);assert.equal(issues,1);
 }finally{await server.close();}
});

test('real registry keeps one rotating view alive through 64s of 2s authorization polls',async(t)=>{
 const server=await registry();let now=1000;const calls=[],proofs=[];
 t.mock.method(performance,'now',()=>now*1000);
 try{
  const services=await createModuleServices({bridge:{async apiPost(action,body){calls.push({action,body});
   if(action==='admin/module-page-session'){
    const result=await server.request('issue',now,{previous_session:body.previous_session,relative_expiry:body.relative_expiry});
    if(body.previous_session)await assert.rejects(server.request('require',now,{page_session:body.previous_session}),/AdminAuthorizationDenied/);
    proofs.push(result.page_session);return result;
   }
   await server.request('require',now,{page_session:body.page_session});
   return {session:{session_id:'synthetic-authorization-session',state:'waiting'},receipt:null};
  }},binding:{owner:'example/demo',page:'config',runtime_id:'synthetic',module_epoch:1},capabilityId:null,isCurrent:()=>true,onDispose(){},confirmClear:async()=>true});
  for(let elapsed=2;elapsed<=64;elapsed+=2){now=1000+elapsed;await services.credentials.authorize(7,'example_qr','poll','synthetic-authorization-session');}
  const issues=calls.filter(call=>call.action==='admin/module-page-session');assert.equal(issues.length,4);
  for(let index=1;index<issues.length;index++)assert.equal(issues[index].body.previous_session,proofs[index-1],'rotation must preserve an unexpired lineage instead of bootstrapping a new one');
  const polls=calls.filter(call=>call.action==='admin/module-credential-authorize');assert.equal(polls.length,32);
  assert.ok(polls.every(call=>call.body.expected_revision===7&&call.body.session_id==='synthetic-authorization-session'));
  assert.equal(new Set(polls.map(call=>call.body.page_session)).size,4);
 }finally{await server.close();}
});

for(const skew of [-120,120])test(`Host clock skew of ${skew}s and short JWT expiry preserve session authority`,async(t)=>{
 const server=await registry();
 const binding={owner:'example/demo',page:'config',runtime_id:'synthetic',module_epoch:1};
 const issues=[],calls=[];
 let now=1000;
 const authExpiry=now+skew+15;
 t.mock.method(Date,'now',()=>now*1000);
 t.mock.method(performance,'now',()=>now*1000);
 const bridge={async apiPost(action,body){
  calls.push({action,body});
  const hostNow=now+skew;
  if(action==='admin/module-page-session'){
   const value=await server.request('issue',hostNow,{
    relative_expiry:body.relative_expiry,
    previous_session:body.previous_session,
    auth_expires_at:authExpiry,
   });
   issues.push(value);
   return value;
  }
  await server.request('require',hostNow,{page_session:body.page_session});
  return {revision:1,fields:{}};
 }};
 try{
  const services=await createModuleServices({bridge,binding,capabilityId:null,isCurrent:()=>true,onDispose(){},confirmClear:async()=>true});
  assert.equal(issues[0].expires_in,15);
  now+=4;
  await services.config.current();
  assert.equal(issues.length,1);
  now+=1;
  await services.credentials.status();
  assert.equal(issues.length,2);
  assert.equal(issues[1].expires_in,10);
  assert.equal(calls.filter(call=>call.action==='admin/module-page-session')[1].body.previous_session,issues[0].page_session);
  now+=10;
  await assert.rejects(services.config.current(),/operation_unavailable/);
  assert.equal(calls.at(-1).action,'admin/module-page-session');
  assert.equal(Object.hasOwn(calls.at(-1).body,'previous_session'),false);
 }finally{await server.close();}
});
