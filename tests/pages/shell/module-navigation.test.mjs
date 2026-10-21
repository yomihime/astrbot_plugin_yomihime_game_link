import {test} from 'node:test';
import assert from 'node:assert/strict';
import {harness,flush} from './single-root-fixture.mjs';
import {sourceBundle,sourceRuntimeURL} from './source-build.mjs';
import {catalog} from './fixtures.mjs';
let serial=0;

async function moduleHarness({invalid=false,missing=false,twoOwners=false}={}){
 const data=catalog();if(twoOwners){const other=catalog('example/demo').modules[0],base=other.pages[0];other.pages=[{...base,route_id:'home',role:'home',styles:[],capability_id:null},{...base,route_id:'config',role:'config',styles:[],capability_id:null}];data.modules.push(other);}
 for(const module of data.modules)module.runtime_id='navigation-'+(++serial);
 const h=await harness(data);let disposed=0,writeCalls=0;const pendingWrites=[];
 for(const name of ['Event','InputEvent','MouseEvent','KeyboardEvent'])globalThis[name]=h.window[name];
 const runtime=await sourceRuntimeURL();
 const code=(await sourceBundle('modules/ff14/pages/src/entry.ts')).replaceAll('"./runtime.js"',JSON.stringify(runtime)).replaceAll("'./runtime.js'",JSON.stringify(runtime));
 const module=await import('data:text/javascript;base64,'+Buffer.from(code).toString('base64'));
 const original=h.window.AstrBotPluginView.apiPost;
 h.window.AstrBotPluginView.apiPost=async(action,body)=>{
  if(action==='admin/module-config-current')return {revision:4,fields:{reply_theme:{value:null,state:'valid',present:false,source:'default'}},descriptors:{reply_theme:{description:'Theme',default:null,value_schema:{type:'string'},choices:[{value:'one',label:'One'}]}}};
  if(action==='admin/module-credential-status')return {revision:4,fields:{credential_fflogs_cn:{configured:false,decryptable:true,upstream:'unverified'},credential_fflogs_global:{configured:false,decryptable:true,upstream:'unverified'}}};
  if(action==='admin/module-config-save'){writeCalls++;return new Promise(resolve=>pendingWrites.push(()=>resolve({revision:5})));}
  return original(action,body);
 };
 globalThis.__loadFixturePage=()=>Promise.resolve({mount(container,options){const page=module.mount(container,options);return {...page,...(invalid?{navigationImpact(){if(invalid==='shape')return {ordinary:false,credentials:false,pending:false,version:1,owner:'UNTRUSTED OWNER'};throw Error('PRIVATE-MODULE-ERROR');}}:missing?{navigationImpact:undefined}:{}),dispose(){disposed++;page.dispose();}};}});
 await h.go('#/module/ff14%2Fff14/config');await flush();
 const edit=(id,value)=>{const input=h.doc.getElementById(id);assert.ok(input,id);assert.equal(input.disabled,false,id+' must be loaded and editable');input.value=value;input.dispatchEvent(new h.window.Event('input',{bubbles:true}));return input;};
 return {...h,edit,disposed:()=>disposed,writes:()=>writeCalls,releaseWrites(){for(const resolve of pendingWrites.splice(0))resolve();},settings:()=>h.doc.querySelector('.shell-settings'),confirmation:()=>h.doc.querySelector('[data-confirmation=navigation]')};
}
test('Module settings click navigation confirms before secret disposal and retains ordinary drafts on return',async()=>{
 const h=await moduleHarness();try{
  h.edit('ff14-config-ff14_calendar_default_days','8');const secret=h.edit('credential_fflogs_cn-client_secret','SYNTHETIC ONLY');
  h.settings().click();await flush();assert.ok(h.confirmation());assert.match(h.confirmation().textContent,/凭据输入/);assert.equal(h.disposed(),0);
  h.doc.querySelector('[data-action=cancel-navigation]').click();await flush();assert.equal(h.window.location.hash,'#/module/ff14%2Fff14/config');assert.equal(h.doc.getElementById(secret.id),secret);assert.equal(secret.value,'SYNTHETIC ONLY');
  h.settings().click();await flush();h.doc.querySelector('[data-action=confirm-navigation]').click();await flush();assert.equal(h.window.location.hash,'#/settings');assert.equal(h.disposed(),1);
  await h.go('#/module/ff14%2Fff14/config');assert.equal(h.doc.getElementById('ff14-config-ff14_calendar_default_days').value,'8');assert.equal(h.doc.getElementById('credential_fflogs_cn-client_secret').value,'');
 }finally{h.close();}
});
test('Module settings hash navigation is guarded and a new edit invalidates an earlier confirmation',async()=>{
 const h=await moduleHarness();try{
  h.edit('ff14-config-ff14_calendar_default_days','8');await h.go('#/');assert.ok(h.confirmation());assert.equal(h.window.location.hash,'#/module/ff14%2Fff14/config');
  const late=h.doc.querySelector('[data-action=confirm-navigation]');h.edit('ff14-config-ff14_calendar_default_days','9');await flush();late.click();await flush();assert.equal(h.disposed(),0);assert.equal(h.window.location.hash,'#/module/ff14%2Fff14/config');
  h.settings().click();await flush();h.doc.querySelector('[data-action=confirm-navigation]').click();await flush();assert.equal(h.disposed(),1);
 }finally{h.close();}
});
test('a throwing Module navigation summary refuses departure with a controlled error',async()=>{
 const h=await moduleHarness({invalid:true});try{
  h.settings().click();await flush();assert.equal(h.window.location.hash,'#/module/ff14%2Fff14/config');assert.equal(h.disposed(),0);
  assert.match(h.doc.body.textContent,/无法核对当前模块的离页状态/);assert.doesNotMatch(h.doc.body.textContent,/PRIVATE-MODULE-ERROR/);
 }finally{h.close();}
});
test('a Module without the optional navigation hook keeps its original clean-page behavior',async()=>{
 const h=await moduleHarness({missing:true});try{
  h.settings().click();await flush();assert.equal(h.window.location.hash,'#/settings');assert.equal(h.confirmation(),null);assert.equal(h.disposed(),1);
 }finally{h.close();}
});
test('pending Module writes require a leave decision and never resend a late ACK',async()=>{
 const h=await moduleHarness();try{
  h.edit('ff14-config-ff14_calendar_default_days','8');h.doc.querySelector('.ff14-settings form').dispatchEvent(new h.window.Event('submit',{bubbles:true,cancelable:true}));await flush();assert.equal(h.writes(),1);
  h.settings().click();await flush();assert.match(h.confirmation().textContent,/不能撤回已发出的请求/);h.doc.querySelector('[data-action=cancel-navigation]').click();await flush();assert.equal(h.disposed(),0);
  h.settings().click();await flush();h.doc.querySelector('[data-action=confirm-navigation]').click();await flush();assert.equal(h.disposed(),1);h.releaseWrites();await flush();assert.equal(h.writes(),1);assert.equal(h.window.location.hash,'#/settings');
 }finally{h.releaseWrites();h.close();}
});
test('cross-owner navigation confirms current owner drafts without inheriting secrets or ordinary values',async()=>{
 const h=await moduleHarness({twoOwners:true});try{
  h.edit('ff14-config-ff14_calendar_default_days','8');h.edit('credential_fflogs_cn-client_secret','SYNTHETIC ONLY');
  h.doc.querySelector('.shell-sidebar a[href="#/module/example%2Fdemo"]').click();await flush();assert.ok(h.confirmation());h.doc.querySelector('[data-action=cancel-navigation]').click();await flush();assert.equal(h.disposed(),0);
  h.doc.querySelector('.shell-sidebar a[href="#/module/example%2Fdemo"]').click();await flush();h.doc.querySelector('[data-action=confirm-navigation]').click();await flush();
  await h.go('#/module/example%2Fdemo/config');assert.equal(h.doc.getElementById('ff14-config-ff14_calendar_default_days').value,'7');assert.equal(h.doc.getElementById('credential_fflogs_cn-client_secret').value,'');
 }finally{h.close();}
});
test('history forward from a restored dirty Module view uses the same cancelable guard',async()=>{
 const h=await moduleHarness();try{
  h.edit('ff14-config-ff14_calendar_default_days','8');h.settings().click();await flush();h.doc.querySelector('[data-action=confirm-navigation]').click();await flush();
  h.window.history.back();await flush();await flush();assert.equal(h.window.location.hash,'#/module/ff14%2Fff14/config');assert.equal(h.doc.getElementById('ff14-config-ff14_calendar_default_days').value,'8');
  h.window.history.forward();await flush();await flush();assert.ok(h.confirmation());assert.equal(h.window.location.hash,'#/module/ff14%2Fff14/config');h.doc.querySelector('[data-action=cancel-navigation]').click();await flush();assert.equal(h.window.location.hash,'#/module/ff14%2Fff14/config');
 }finally{h.close();}
});
test('a Module cannot provide an owner through its bounded navigation summary',async()=>{
 const h=await moduleHarness({invalid:'shape'});try{h.settings().click();await flush();assert.equal(h.window.location.hash,'#/module/ff14%2Fff14/config');assert.equal(h.disposed(),0);assert.match(h.doc.body.textContent,/无法核对当前模块的离页状态/);assert.doesNotMatch(h.doc.body.textContent,/UNTRUSTED OWNER/);}finally{h.close();}
});
test('a lifecycle epoch change invalidates an outstanding Module leave confirmation',async()=>{
 const h=await moduleHarness();try{
  h.edit('credential_fflogs_cn-client_secret','SYNTHETIC ONLY');h.settings().click();await flush();const late=h.doc.querySelector('[data-action=confirm-navigation]');assert.ok(late);
  const data=await h.window.AstrBotPluginView.apiGet('catalog',{});data.modules[0].module_epoch++;
  h.doc.querySelector('[aria-label="刷新模块状态"]').click();await flush();await flush();assert.equal(h.confirmation(),null);assert.equal(h.disposed(),1);
  late.click();await flush();assert.equal(h.window.location.hash,'#/module/ff14%2Fff14/config');assert.equal(h.doc.getElementById('credential_fflogs_cn-client_secret'),null);
 }finally{h.close();}
});
