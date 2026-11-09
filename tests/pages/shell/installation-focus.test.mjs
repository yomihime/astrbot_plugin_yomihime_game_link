import {test,after} from 'node:test';
import assert from 'node:assert/strict';
import {JSDOM} from '../../../pages/frontend/node_modules/jsdom/lib/api.js';
import {sourceApp,sourceRuntimeURL} from './source-build.mjs';
// Native keyboard Tab/blur and computed focus visuals remain real-browser work.
// This DOM regression proves the trigger is never natively disabled while busy,
// while actual callbacks, authorization predicates and single-request gates hold.
const dom=new JSDOM('<body></body>',{pretendToBeVisual:true,url:'http://localhost/'});
for(const name of ['window','document','Element','HTMLElement','SVGElement','Node','MutationObserver'])globalThis[name]=name==='window'?dom.window:name==='document'?dom.window.document:dom.window[name];
const runtime=await import(await sourceRuntimeURL());
const {createInstallationManagement}=await sourceApp('pages/frontend/src/installation-management.ts');
const flush=async()=>{for(let i=0;i<12;i++){await Promise.resolve();await runtime.nextTick();}};
const plan=(extra={})=>({ok:true,core_epoch:7,package_id:'steam_dlc',plan_id:'a'.repeat(48),installed_version:'0.3.0',running_version:'0.3.0',installed_digest:'b'.repeat(64),running_digest:'b'.repeat(64),state:'active',signature_state:'verified',grant_revision:2,artifact_revision:0,permissions_delta:{added:[],removed:[],changed:[]},...extra});
const status=(extra={})=>({ok:true,core_epoch:7,core:{ready:true,phase:'active',running_version:'0.2.0'},packages:[plan()],publishers:[],...extra});
function fixture(){
 const calls=[],live=runtime.ref(true);let generation=1,asks=0;
 const bridge={apiPost(endpoint,body){return new Promise((resolve,reject)=>calls.push({endpoint,body,resolve,reject}));}};
 const view=createInstallationManagement(bridge,{cancel(){},async ask(value){asks++;return value.isCurrent();}},()=>live.value,()=>generation,()=> 'en-US');
 const container=document.createElement('div'),outside=document.createElement('button');outside.textContent='Independent control';document.body.append(container,outside);
 const app=runtime.createApp({render:()=>view.render()});app.mount(container);
 const button=text=>{const matches=[...container.querySelectorAll('button')].filter(b=>b.textContent===text);assert.equal(matches.length,1,text);return matches[0];};
 const fillPackage=async()=>{const input=container.querySelector('#hot-update-package');input.value='steam_dlc';input.dispatchEvent(new dom.window.Event('input',{bubbles:true}));await flush();return input;};
 const bootstrap=async(value=status())=>{const work=view.refresh();calls.at(-1).resolve(value);await work;await flush();};
 return {view,calls,container,outside,button,fillPackage,bootstrap,get asks(){return asks;},setCurrent(value){live.value=value;},invalidate(){generation++;view.invalidate();},close(){live.value=false;view.dispose();app.unmount();container.remove();outside.remove();}};
}
function activationBurst(button){
 // Browser Enter/Space activation dispatches click. JSDOM needs that click
 // explicitly, so this models the callback burst without claiming native keys.
 button.click();button.dispatchEvent(new dom.window.MouseEvent('click',{bubbles:true}));button.dispatchEvent(new dom.window.KeyboardEvent('keydown',{key:'Enter',bubbles:true}));button.click();button.dispatchEvent(new dom.window.KeyboardEvent('keydown',{key:' ',bubbles:true}));button.click();
}
function noFocusRestoration(button){let calls=0;const original=button.focus.bind(button);button.focus=(...args)=>{calls++;return original(...args);};return()=>calls;}

test('focused busy refresh stays the same enabled native trigger, announces busy, and rejects same-turn activation bursts',async()=>{
 const f=fixture();try{
  await f.bootstrap();const input=await f.fillPackage(),button=f.button('Refresh status');button.focus();const focusCalls=noFocusRestoration(button),before=f.calls.length;
  activationBurst(button);assert.equal(f.calls.length,before+1,'same event-loop callbacks must be gated before rerender');await flush();
  assert.equal(f.button('Refresh status'),button);assert.equal(button.disabled,false,'busy native disabled is the browser05 blur trigger');assert.equal(button.getAttribute('aria-disabled'),'true');assert.equal(button.getAttribute('aria-busy'),'true');assert.equal(document.activeElement,button);
  activationBurst(button);await flush();assert.equal(f.calls.length,before+1);f.calls.at(-1).resolve(status());await flush();
  assert.equal(f.button('Refresh status'),button);assert.equal(button.disabled,false);assert.equal(button.getAttribute('aria-disabled'),'false');assert.equal(button.getAttribute('aria-busy'),'false');assert.equal(document.activeElement,button);assert.equal(focusCalls(),0);assert.equal(input.value,'steam_dlc');
 }finally{f.close();}
});

for(const target of ['row','input'])test(`busy ${target} inspect retains its trigger and suppresses competing reads`,async()=>{
 const f=fixture();try{
  await f.bootstrap();await f.fillPackage();const row=f.container.querySelector('[data-package-id="steam_dlc"] button');const field=[...f.container.querySelector('details').children].find(e=>e.tagName==='BUTTON'&&e.textContent==='Inspect package');assert.ok(row);assert.ok(field);
  const button=target==='row'?row:field;button.focus();const focusCalls=noFocusRestoration(button),before=f.calls.length;activationBurst(button);f.button('Refresh status').click();activationBurst(target==='row'?field:row);assert.equal(f.calls.length,before+1);await flush();
  assert.equal(button.disabled,false);assert.equal(button.getAttribute('aria-disabled'),'true');assert.equal(button.getAttribute('aria-busy'),'true');assert.equal(document.activeElement,button);
  f.calls.at(-1).resolve(plan());await flush();assert.equal(document.activeElement,button);assert.equal(button.isConnected,true);assert.equal(focusCalls(),0);assert.equal(button.getAttribute('aria-busy'),'false');
 }finally{f.close();}
});

test('permanent denial remains native disabled, including signed trust, artifact CAS, empty enrollment and revoked publisher',async()=>{
 const f=fixture();try{
  const publisher={key_id:'c'.repeat(64),public_key_base64:'A'.repeat(43)+'=',channels:['preview'],revision:3,revoked:true};await f.bootstrap(status({publishers:[publisher]}));
  const inspect=f.view.inspect('steam_dlc');f.calls.at(-1).resolve(plan({signature_state:'publisher_untrusted',grant_revision:0}));await inspect;await flush();const before=f.calls.length,asks=f.asks;
  for(const label of ['Approve package permissions','Approve exact artifact','Enroll publisher','Revoke publisher']){const button=f.button(label);assert.equal(button.disabled,true,label);assert.equal(button.getAttribute('aria-disabled'),'true');button.dispatchEvent(new dom.window.MouseEvent('click',{bubbles:true}));}
  await flush();assert.equal(f.calls.length,before);assert.equal(f.asks,asks);
 }finally{f.close();}
});

test('current revocation blocks the old button even before the next render and remains unavailable after render',async()=>{
 const f=fixture();try{
  await f.bootstrap();const button=f.button('Refresh status'),before=f.calls.length;f.setCurrent(false);activationBurst(button);assert.equal(f.calls.length,before);await flush();assert.equal(button.disabled,true);assert.equal(button.getAttribute('aria-disabled'),'true');
  activationBurst(button);await flush();assert.equal(f.calls.length,before);
 }finally{f.close();}
});

test('generation invalidation rejects late status without restoring focus or an old plan',async()=>{
 const f=fixture();try{
  await f.bootstrap();const button=f.button('Refresh status');button.focus();const focusCalls=noFocusRestoration(button);button.click();const pending=f.calls.at(-1);await flush();f.invalidate();f.outside.focus();const before=f.calls.length;pending.resolve(status());await flush();
  assert.equal(document.activeElement,f.outside);assert.equal(focusCalls(),0);assert.equal(f.calls.length,before);assert.equal(f.container.querySelector('[data-package-id="steam_dlc"]'),null);assert.doesNotMatch(f.container.textContent,/Permission changes/);assert.equal(button.getAttribute('aria-busy'),'false');
 }finally{f.close();}
});

test('failed refresh clears busy and is retryable without asynchronous focus calls',async()=>{
 const f=fixture();try{
  await f.bootstrap();const button=f.button('Refresh status');button.focus();const focusCalls=noFocusRestoration(button),before=f.calls.length;button.click();await flush();f.calls.at(-1).reject(Error('synthetic_read_failure'));await flush();
  assert.equal(document.activeElement,button);assert.equal(focusCalls(),0);assert.equal(button.disabled,false);assert.equal(button.getAttribute('aria-busy'),'false');assert.match(f.container.textContent,/hot_update_failed/);button.click();assert.equal(f.calls.length,before+2);f.calls.at(-1).resolve(status());await flush();assert.equal(document.activeElement,button);
 }finally{f.close();}
});

test('completion preserves user focus moved to an independent control and makes no focus restoration call',async()=>{
 const f=fixture();try{
  await f.bootstrap();const button=f.button('Refresh status');button.focus();const focusCalls=noFocusRestoration(button);button.click();await flush();f.outside.focus();f.calls.at(-1).resolve(status());await flush();assert.equal(document.activeElement,f.outside);assert.equal(focusCalls(),0);
 }finally{f.close();}
});

test('same-turn repeated approval has one exact reviewed CAS request and busy callbacks cannot duplicate it',async()=>{
 const f=fixture();try{
  await f.bootstrap();let work=f.view.inspect('steam_dlc');f.calls.at(-1).resolve(plan());await work;await flush();const button=f.button('Approve package permissions'),before=f.calls.length;activationBurst(button);await flush();
  assert.equal(f.calls.length,before+1);assert.deepEqual(f.calls.at(-1).body,{package_id:'steam_dlc',plan_id:'a'.repeat(48),expected_revision:2,core_epoch:7});button.dispatchEvent(new dom.window.MouseEvent('click',{bubbles:true}));await flush();assert.equal(f.calls.length,before+1);
  f.calls.at(-1).resolve({ok:true,core_epoch:7,revision:3});await flush();assert.match(f.calls.at(-1).endpoint,/bootstrap$/);f.calls.at(-1).resolve(status());await flush();f.calls.at(-1).resolve(plan({grant_revision:3}));await flush();
  assert.equal(f.calls.filter(c=>c.endpoint.endsWith('package-approve')).length,1);
 }finally{f.close();}
});
after(()=>dom.window.close());
