// Production management state; synthetic inputs, no Host or secret readback.
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {JSDOM} from '../../../pages/frontend/node_modules/jsdom/lib/api.js';
import {catalog,snapshot,context,credentialCatalog,credentialStatus} from './fixtures.mjs';
const host=new JSDOM('<body></body>',{pretendToBeVisual:true});
for(const name of ['window','document','Element','HTMLElement','SVGElement','Node','MutationObserver'])globalThis[name]=host.window[name];
const {createManagementPage}=await import('../../../pages/management/app.js');
const flush=async()=>{for(let i=0;i<80;i++)await Promise.resolve();};
async function fixture(denied){
 const dom=new JSDOM('<div id="management-root"></div>',{pretendToBeVisual:true}),calls=[];
 const forms=credentialCatalog();forms.fields.push({...forms.fields[0],module_id:'example/demo',name:'credential_demo'});
 const states={...credentialStatus(),'example/demo':{revision:1,fields:{credential_demo:'unset'}}};
 const responses={'admin/catalog':catalog(),'admin/read':snapshot(),'admin/credential-catalog':forms,'admin/credential-status':states,'admin/credential-readiness':{ready:true,state:'ready',reason_code:'ready'}};
 const bridge={ready:()=>Promise.resolve(context),onContext(){return()=>{};},apiPost(endpoint){calls.push(endpoint);if(endpoint===denied)return Promise.reject(Error('admin_authorization_denied'));if(endpoint in responses)return Promise.resolve(responses[endpoint]);throw Error('unexpected_write');}};
 let mutations=0;const page=createManagementPage(dom.window.document,bridge,dom.window,{onDraftMutation:()=>mutations++});page.start();await flush();
 const edit=(node,value,event='change')=>{if(node.type==='checkbox')node.checked=value;else node.value=value;node.dispatchEvent(new dom.window.Event(event,{bubbles:true}));};
 return {dom,page,calls,edit,mutations:()=>mutations,field:name=>dom.window.document.querySelector(`[data-field="${name}"]`),credential:name=>dom.window.document.querySelector(`[data-credential="${name}"]`),close(){page.dispose();dom.window.close();}};
}
const empty={ordinaryOwners:[],credentialOwners:[],requiresConfirmation:false};
test('ordinary navigation impact follows displayed owner and preserves global module protection',async()=>{
 const f=await fixture();try{
  assert.deepEqual(f.page.navigationImpact(),empty);assert.equal(f.mutations(),0);
  const core=f.field('default_region'),mode=core.querySelector('[data-role=mode]');f.edit(mode,'replace');
  assert.deepEqual(f.page.navigationImpact(),{ordinaryOwners:['game_link/core'],credentialOwners:[],requiresConfirmation:true});
  f.page.selectOwner('ff14/ff14');assert.deepEqual(f.page.navigationImpact(),empty);assert.equal(f.page.dirty(),true);
  f.page.suspend();f.page.resume();await flush();assert.equal(mode.value,'replace');assert.deepEqual(f.page.navigationImpact(),empty);
  const calendar=f.field('ff14_calendar_default_days');f.edit(calendar.querySelector('[data-role=mode]'),'clear');
  assert.deepEqual(f.page.navigationImpact().ordinaryOwners,['ff14/ff14']);
  f.page.selectOwner('game_link/core');assert.deepEqual(f.page.navigationImpact().ordinaryOwners,['game_link/core']);
  f.page.selectOwner(null);assert.deepEqual(new Set(f.page.navigationImpact().ordinaryOwners),new Set(['game_link/core','ff14/ff14']));
  f.page.suspend();f.page.resume();await flush();assert.equal(mode.value,'replace');assert.equal(calendar.querySelector('[data-role=mode]').value,'clear');
 }finally{f.close();}
});
for(const owner of [null,'game_link/core','ff14/ff14'])for(const field of ['credential_fflogs_cn','credential_fflogs_global','credential_demo'])for(const kind of ['client_id','client_secret','replace','clear','confirm'])test(`credential navigation impact protects ${owner??'all'} / ${field} / ${kind}`,async()=>{
 const f=await fixture();try{
  f.page.selectOwner(owner);const card=f.credential(field),mode=card.querySelector('select'),confirm=card.querySelector('input[type=checkbox]');
  // Hidden/disabled controls deliberately model a retained other-owner draft.
  // Values are synthetic and never included in assertion diagnostics.
  if(kind==='client_id'||kind==='client_secret')f.edit(card.querySelector(`[data-role=${kind}]`),'synthetic-navigation-input','input');
  else if(kind==='confirm')f.edit(confirm,true);else f.edit(mode,kind);
  const expected=field==='credential_demo'?'example/demo':'ff14/ff14';
  assert.deepEqual(f.page.navigationImpact(),{ordinaryOwners:[],credentialOwners:[expected],requiresConfirmation:true});
  assert.equal(f.mutations(),1);
  // Pure query/cancellation path never clears input or operation intent.
  assert.equal(f.page.navigationImpact().requiresConfirmation,true);
  if(kind.startsWith('client_'))assert.ok(Boolean(card.querySelector(`[data-role=${kind}]`).value));
  if(kind==='confirm')assert.equal(confirm.checked,true);
  if(['replace','clear'].includes(kind))assert.equal(mode.value,kind);
  f.page.suspend();assert.deepEqual(f.page.navigationImpact(),empty);
  assert.ok([...f.dom.window.document.querySelectorAll('[data-role=client_id],[data-role=client_secret]')].every(node=>node.value===''));
  assert.equal(confirm.checked,false);assert.equal(mode.value,'keep');
 }finally{f.close();}
});
test('suspend clears every owner and region together but preserves ordinary drafts',async()=>{
 const f=await fixture();try{
  const normal=f.field('default_region').querySelector('[data-role=mode]');f.edit(normal,'clear');
  for(const field of ['credential_fflogs_cn','credential_fflogs_global','credential_demo'])f.edit(f.credential(field).querySelector('select'),'clear');
  f.page.selectOwner('game_link/core');assert.deepEqual(new Set(f.page.navigationImpact().credentialOwners),new Set(['ff14/ff14','example/demo']));
  f.page.suspend();assert.equal(normal.value,'clear');assert.deepEqual(f.page.navigationImpact(),{ordinaryOwners:['game_link/core'],credentialOwners:[],requiresConfirmation:true});
  assert.ok([...f.dom.window.document.querySelectorAll('[data-credential] select')].every(node=>node.value==='keep'));
 }finally{f.close();}
});
for(const denied of ['admin/catalog','admin/read','admin/credential-catalog','admin/credential-status','admin/credential-readiness'])test(`fresh ${denied} authorization failure has no navigation impact`,async()=>{
 const f=await fixture(denied);try{
  assert.deepEqual(f.page.navigationImpact(),empty);assert.equal(f.page.dirty(),false);assert.equal(f.mutations(),0);
  if(['admin/catalog','admin/read'].includes(denied)){assert.equal(f.dom.window.document.querySelector('#recover').disabled,true);assert.match(f.dom.window.document.querySelector('#status').textContent,/授权不可用/);}
  else {assert.match(f.dom.window.document.querySelector('#credential-status').textContent,/授权不可用|暂不能填写/);assert.ok([...f.dom.window.document.querySelectorAll('[data-role=client_secret]')].every(node=>node.disabled));}
  assert.equal(f.calls.some(endpoint=>['admin/update','admin/credential-update'].includes(endpoint)),false);
 }finally{f.close();}
});
