import {test} from 'node:test';
import assert from 'node:assert/strict';
import {JSDOM} from '../../../pages/frontend/node_modules/jsdom/lib/api.js';
import {catalog,snapshot,context,credentialCatalog,credentialStatus} from './fixtures.mjs';
const host=new JSDOM('<body></body>',{pretendToBeVisual:true});
for(const key of ['window','document','Element','HTMLElement','SVGElement','Node','MutationObserver'])globalThis[key]=host.window[key];
const {createManagementPage}=await import('../../../pages/management/app.js');
const flush=async()=>{for(let i=0;i<80;i++)await Promise.resolve();};
async function fixture(options={},initialState={}){
 const dom=new JSDOM('<div id="management-root"></div>',{pretendToBeVisual:true}),document=dom.window.document,calls=[],timers=new Map();let id=0,accept,revision=1;
 const state={failure:'',ackFailure:false,afterAckFailure:'',unready:false,readonly:false,readinessError:null,afterAckReadinessError:null,...initialState};
 dom.window.setTimeout=(callback,delay)=>{timers.set(++id,{callback,delay});return id;};dom.window.clearTimeout=id=>timers.delete(id);
 const declarations=catalog(),values=snapshot();
 declarations.fields.push({...declarations.fields.find(f=>f.name==='ff14_calendar_default_days'),name:'ff14_subscriptions_enabled',group:null,value_schema:{type:'boolean'},default:true});values['ff14/ff14'].fields.ff14_subscriptions_enabled={value:true,state:'valid',present:true,source:'sqlite'};
 declarations.fields.push({...declarations.fields.at(-1),name:'another_boolean',default:false});values['ff14/ff14'].fields.another_boolean={value:false,state:'valid',present:true,source:'sqlite'};
 const bridge={ready:()=>Promise.resolve(context),onContext(fn){accept=fn;return()=>{accept=null;};},apiPost(endpoint,body){
  calls.push({endpoint,...(endpoint==='admin/update'?{body}: {})});if(endpoint===state.failure)return Promise.reject(Error('controlled_read_failure'));
  if(['admin/update','admin/credential-update','admin/recover','admin/rollback'].includes(endpoint)){if(state.ackFailure)return Promise.reject(Error('admin_authorization_denied'));state.failure=state.afterAckFailure;state.readinessError=state.afterAckReadinessError;revision++;if(endpoint==='admin/recover')return Promise.resolve({recovered:true});if(endpoint==='admin/rollback')return Promise.resolve({rolled_back:true});return Promise.resolve({module_id:body.module_id,revision});}
  if(endpoint==='admin/credential-readiness'&&state.readinessError)return Promise.reject(state.readinessError);
  if(endpoint==='admin/catalog'){const result=structuredClone(declarations);if(state.readonly)Object.assign(result.fields.find(f=>f.name==='ff14_calendar_default_days'),{editable:false,blocked_reason:'semantic_validator_unavailable'});return Promise.resolve(result);}
  if(endpoint==='admin/read'){const result=structuredClone(values);for(const row of Object.values(result))row.revision=revision;return Promise.resolve(result);}
  if(endpoint==='admin/credential-catalog')return Promise.resolve(credentialCatalog());if(endpoint==='admin/credential-status')return Promise.resolve(credentialStatus(revision));if(endpoint==='admin/credential-readiness')return Promise.resolve(state.unready?{ready:false,state:'unavailable',reason_code:'secret_encryption_unavailable'}:{ready:true,state:'ready',reason_code:'ready'});throw Error('unexpected endpoint');
 }};
 const page=createManagementPage(document,bridge,dom.window,options);page.start();await flush();
 return {dom,document,page,state,calls,timers,emit:ctx=>accept?.(ctx),status:()=>document.querySelector('#status'),click:id=>document.getElementById(id).click(),edit(node,value,event='change'){node.value=value;node.dispatchEvent(new dom.window.Event(event,{bubbles:true}));},field:name=>document.querySelector(`[data-field="${name}"]`),async close(){page.dispose();await flush();dom.window.close();}};
}
test('ordinary scope removes technical/maintenance DOM and survives refresh/suspend/resume/close',async()=>{
 const f=await fixture();try{
  assert.equal(f.document.querySelector('.technical-details,.advanced-maintenance,.maintenance-panel,#recover,#rollback,#replacement'),null);assert.equal(f.status().textContent,'');assert.equal(f.document.querySelector('.n-alert'),null);
  f.page.suspend();f.page.resume();await flush();assert.equal(f.status().textContent,'');f.click('refresh');await flush();assert.equal(f.status().textContent,'已刷新。');assert.equal(f.timers.size,1);
 }finally{await f.close();assert.equal(f.timers.size,0);}
});

const accessFailures=[['controlled-code',Error('admin_authorization_denied')],['code-object',{code:'admin_authorization_denied',message:'controlled'}],['error-code',{error_code:'asset_revoked'}],['context-expired',Error('stale_context')],['page-revoked',Error('page_revoked')],['http401',{status:401}],['http403',{response:{status:403}}]];
for(const [kind,error] of accessFailures)for(const when of ['initial','refresh','ack'])test(`readiness ${kind} at ${when} clears and fences writes without success`,async()=>{
 const f=await fixture({},when==='initial'?{readinessError:error}:{});try{
  let save,oldTimer;
  if(when!=='initial'){
   f.click('refresh');await flush();oldTimer=[...f.timers.values()][0];
   const card=f.document.querySelector('[data-credential="credential_fflogs_global"]'),mode=card.querySelector('select'),confirm=card.querySelector('input[type=checkbox]');save=card.querySelector('[data-action=credential-save]');
   const pendingSecret=f.document.querySelector('[data-credential="credential_fflogs_cn"] [data-role=client_secret]');f.edit(pendingSecret,Math.random().toString(36),'input');assert.equal(Boolean(pendingSecret.value),true);
   f.edit(mode,'clear');confirm.checked=true;confirm.dispatchEvent(new f.dom.window.Event('change'));
   if(when==='ack'){f.state.afterAckReadinessError=error;save.click();}else{f.state.readinessError=error;f.click('refresh');}await flush();
  }
  const message=f.status().textContent;assert.match(message,/管理授权不可用/);if(when==='ack')assert.match(message,/已提交.*暂未核实/);assert.doesNotMatch(message,/已保存|已刷新/);assert.notEqual(f.status().dataset.tone,'success');assert.equal(f.timers.size,0);oldTimer?.callback();assert.equal(f.status().textContent,message);
  assert.ok([...f.document.querySelectorAll('[data-role=client_id],[data-role=client_secret]')].every(n=>n.value===''));assert.ok([...f.document.querySelectorAll('[data-role=credential-clear-confirm]')].every(n=>!n.checked));
  for(const button of f.document.querySelectorAll('[data-action=save],[data-action=credential-save]')){assert.equal(button.getAttribute('aria-disabled'),'true');button.dispatchEvent(new f.dom.window.Event('click'));}save?.dispatchEvent(new f.dom.window.Event('click'));await flush();assert.equal(f.calls.filter(c=>c.endpoint==='admin/credential-update').length,when==='ack'?1:0);assert.equal(f.calls.filter(c=>c.endpoint==='admin/update').length,0);
  // Ordinary reads succeeding again cannot hide the repeated readiness denial.
  f.click('refresh');await flush();assert.match(f.status().textContent,/管理授权不可用/);assert.notEqual(f.status().dataset.tone,'success');assert.equal(f.timers.size,0);
 }finally{await f.close();}
});
test('ordinary read remains usable on a generic credential read failure, but explicit access recovery needs a new successful read',async()=>{
 const f=await fixture();try{
  f.state.failure='admin/credential-status';f.click('refresh');await flush();const save=f.document.querySelector('#fields [data-owner="ff14/ff14"] [data-action=save]');assert.equal(save.getAttribute('aria-disabled'),'false');assert.match(f.document.querySelector('#credential-status').textContent,/暂不可用/);f.state.afterAckFailure='admin/credential-status';f.edit(f.field('ff14_calendar_default_days').querySelector('[data-role=value]'),'9','input');save.click();await flush();assert.equal(f.calls.filter(c=>c.endpoint==='admin/update').length,1);assert.equal(f.status().textContent,'已保存。');assert.match(f.document.querySelector('#credential-status').textContent,/暂不可用/);
  f.state.failure='';f.state.readinessError={status:401};f.click('refresh');await flush();assert.equal(save.getAttribute('aria-disabled'),'true');assert.match(f.status().textContent,/授权不可用/);
  f.state.readinessError=null;f.click('refresh');await flush();assert.equal(save.getAttribute('aria-disabled'),'false');assert.equal(f.status().textContent,'已刷新。');assert.equal(f.calls.filter(c=>c.endpoint==='admin/update').length,1);assert.equal(f.calls.filter(c=>c.endpoint==='admin/credential-update').length,0);
 }finally{await f.close();}
});
for(const kind of ['ready-false','crypto503','unknown-message'])test(`readiness ${kind} preserves authorized confirmed clear but blocks replacement`,async()=>{
 const error=kind==='crypto503'?{code:'secret_encryption_unavailable',status:503}:kind==='unknown-message'?Error('detail mentions admin_authorization_denied but is not a code'):null;
 const f=await fixture({}, {unready:kind==='ready-false',readinessError:error,afterAckReadinessError:error});try{
  const card=f.document.querySelector('[data-credential="credential_fflogs_global"]'),mode=card.querySelector('select'),confirm=card.querySelector('input[type=checkbox]'),save=card.querySelector('[data-action=credential-save]');
  assert.equal(mode.disabled,false);f.edit(mode,'replace');await flush();assert.equal(save.getAttribute('aria-disabled'),'true');f.edit(mode,'clear');confirm.checked=true;confirm.dispatchEvent(new f.dom.window.Event('change'));await flush();assert.equal(save.getAttribute('aria-disabled'),'false');save.click();await flush();assert.equal(f.calls.filter(c=>c.endpoint==='admin/credential-update').length,1);assert.equal(f.status().textContent,'已保存。');assert.equal(f.status().dataset.tone,'success');assert.ok(f.document.querySelector('#credential-status').textContent);
 }finally{await f.close();}
});
test('module default reset is exact bool replacement, while unrelated bool keeps clear semantics',async()=>{
 const f=await fixture();try{
  const gate=f.field('ff14_subscriptions_enabled'),other=f.field('another_boolean');assert.match(gate.textContent,/订阅推送/);assert.match(gate.textContent,/开启/);assert.match(gate.textContent,/关闭/);
  gate.querySelector('[data-action=default]').click();assert.equal(gate.querySelector('[data-role=mode]').value,'replace');assert.equal(gate.querySelector('[data-role=value]').value,'1');
  other.querySelector('[data-action=default]').click();assert.equal(other.querySelector('[data-role=mode]').value,'clear');
  assert.equal(f.calls.filter(c=>c.endpoint==='admin/update').length,0);f.document.querySelector('[data-owner="ff14/ff14"] [data-action=save]').click();await flush();
  const call=f.calls.find(c=>c.endpoint==='admin/update');assert.deepEqual(call.body.updates,[{field:'ff14_subscriptions_enabled',mode:'replace',value:true},{field:'another_boolean',mode:'clear'}]);assert.equal(call.body.expected_revision,1);
 }finally{await f.close();}
});
for(const operation of ['ordinary','credential'])for(const outcome of ['success','ack-denied','read-failure'])test(`${operation} save ${outcome} has bounded truthful feedback and one write`,async()=>{
 const f=await fixture();try{
  f.state.ackFailure=outcome==='ack-denied';f.state.afterAckFailure=outcome==='read-failure'?(operation==='ordinary'?'admin/read':'admin/credential-status'):'';
  let save;
  if(operation==='ordinary'){f.field('ff14_calendar_default_days').querySelector('[data-action=default]').click();save=f.document.querySelector('[data-owner="ff14/ff14"] [data-action=save]');}
  else{const card=f.document.querySelector('[data-credential]');f.edit(card.querySelector('select'),'clear');card.querySelector('input[type=checkbox]').click();save=card.querySelector('[data-action=credential-save]');}
  save.click();await flush();const endpoint=operation==='ordinary'?'admin/update':'admin/credential-update';assert.equal(f.calls.filter(c=>c.endpoint===endpoint).length,1);
  if(outcome==='success'){assert.equal(f.status().textContent,'已保存。');assert.equal(f.status().dataset.tone,'success');assert.equal(f.timers.size,1);}
  else{assert.equal(f.timers.size,0);assert.equal(f.status().dataset.tone,outcome==='ack-denied'?'error':'warning');assert.match(f.status().textContent,outcome==='ack-denied'?/授权不可用/:/已提交，状态暂未核实/);assert.doesNotMatch(f.status().textContent,/已保存|未取得成功确认/);assert.equal(save.getAttribute('aria-disabled'),'true');save.dispatchEvent(new f.dom.window.Event('click'));await flush();assert.equal(f.calls.filter(c=>c.endpoint===endpoint).length,1);}
 }finally{await f.close();}
});
test('old success timer cannot erase a newer error; suspend/context/dispose revoke callbacks',async()=>{
 const f=await fixture();try{
  f.click('refresh');await flush();const old=[...f.timers.values()][0];f.state.failure='admin/read';f.click('refresh');await flush();const error=f.status().textContent;assert.equal(f.status().dataset.tone,'error');assert.equal(f.timers.size,0);old.callback();assert.equal(f.status().textContent,error);
  f.state.failure='';f.click('refresh');await flush();const afterSuspend=[...f.timers.values()][0];f.page.suspend();assert.equal(f.timers.size,0);assert.equal(f.status().textContent,'');afterSuspend.callback();assert.equal(f.status().textContent,'');
  f.page.resume();await flush();assert.equal(f.status().textContent,'');f.click('refresh');await flush();const contextTimer=[...f.timers.values()][0];f.emit({...context,pageName:'wrong'});await flush();const contextError=f.status().textContent;contextTimer.callback();assert.equal(f.status().textContent,contextError);assert.equal(f.timers.size,0);
 }finally{await f.close();}
});
test('credential unset and encryption unknown are separate persistent hints with restart guidance',async()=>{
 const f=await fixture();try{
  f.state.unready=true;f.click('refresh');await flush();const hint=f.document.querySelector('#credential-status');assert.match(hint.textContent,/当前加密不可用/);assert.match(hint.textContent,/重启同一宿主/);assert.doesNotMatch(hint.textContent,/缺少|未正确配置|key_missing/);
  assert.match(f.document.querySelector('[data-credential="credential_fflogs_cn"] .credential-detail').textContent,/未配置/);assert.match(f.document.querySelector('[data-credential="credential_fflogs_global"] .credential-detail').textContent,/已保存/);
  const warning=hint.textContent;for(const timer of f.timers.values())timer.callback();assert.equal(hint.textContent,warning);assert.ok([...f.document.querySelectorAll('[data-role=client_secret]')].every(node=>node.disabled));
 }finally{await f.close();}
});
test('maintenance scope has no form nodes; default gate denies writes and mutable consent fences old actions',async()=>{
 const denied=await fixture({scope:'maintenance'});try{assert.equal(denied.document.querySelector('#fields,#credentials'),null);denied.click('recover');await flush();assert.equal(denied.calls.some(c=>c.endpoint==='admin/recover'),false);}finally{await denied.close();}
 let release;const f=await fixture({scope:'maintenance',beforeMaintenance:()=>new Promise(resolve=>{release=resolve;})});try{
  f.click('recover');await flush();assert.equal(typeof release,'function');f.page.suspend();release(true);await flush();assert.equal(f.calls.some(c=>c.endpoint==='admin/recover'),false);
 }finally{await f.close();}
});
test('maintenance refresh failure and validator loss deny forced recovery without placeholder controls',async()=>{
 const f=await fixture({scope:'maintenance',beforeMaintenance:async()=>true});try{
  f.state.failure='admin/read';f.click('refresh');await flush();f.document.querySelector('#recover').dispatchEvent(new f.dom.window.Event('click'));await flush();assert.equal(f.calls.some(c=>c.endpoint==='admin/recover'),false);assert.equal(f.document.querySelector('#recover').getAttribute('aria-disabled'),'true');
  f.state.failure='';f.state.readonly=true;f.click('refresh');await flush();f.document.querySelector('#recover').dispatchEvent(new f.dom.window.Event('click'));await flush();assert.equal(f.calls.some(c=>c.endpoint==='admin/recover'),false);assert.equal(f.document.querySelector('#recover').getAttribute('aria-disabled'),'true');
 }finally{await f.close();}
});
